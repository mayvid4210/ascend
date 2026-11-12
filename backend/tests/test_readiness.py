"""Independent rules, scoring, API eligibility, and redaction regression tests."""

from pathlib import Path

import pytest

from app.models.readiness import Finding
from app.services import readiness
from test_analysis import FASTAPI, FLASK, upload


def issues(response):
    assert response.status_code == 200
    return {item['rule_id']: item for item in response.json()['findings']}


def test_missing_dependencies_are_confirmed_in_the_selected_scope():
    response = upload({'main.py': FASTAPI, 'examples/requirements.txt': 'fastapi'})
    issue = issues(response)['DEP001']
    assert issue['confidence'] == 'confirmed'
    assert issue['severity'] == 'warning'
    assert issue['category'] == 'dependencies'
    assert issue['file_path'] is None


def test_unknown_python_version_is_potential_not_a_known_runtime_failure():
    response = upload({'main.py': FASTAPI})
    assert issues(response)['RUN002']['confidence'] == 'potential'
    assert response.json()['assessment_provisional'] is True


def test_missing_startup_rule_is_independently_testable():
    report = readiness.AnalysisReport.model_construct(entry_point=None)
    issue = readiness.startup_rule(report, readiness.Evidence())
    assert issue.rule_id == 'RUN001'
    assert issue.confidence == 'potential'
    assert readiness.startup_rule(readiness.AnalysisReport.model_construct(entry_point='main:app'), readiness.Evidence()) is None


def test_missing_importable_package_target_is_provisional_in_the_api():
    report = upload({'__init__.py': FLASK, 'requirements.txt': 'Flask'}).json()
    assert report['project_type'] == 'web_application'
    assert report['entry_point'] is None
    assert report['readiness_score'] is not None
    assert report['assessment_provisional'] is True
    assert any(issue['rule_id'] == 'RUN001' for issue in report['findings'])


@pytest.mark.parametrize(('source', 'host', 'port'), [
    (FLASK + '\nif __name__ == "__main__":\n    app.run(host="127.0.0.1", port=5000)', '127.0.0.1', 5000),
    (FASTAPI + '\nimport uvicorn\nuvicorn.run(app, host="localhost", port=8000)', 'localhost', 8000),
    (FASTAPI + '\nfrom uvicorn import run\nrun(app, host="::1", port=8080)', '::1', 8080),
])
def test_literal_network_concerns_are_potential_and_have_source_locations(source, host, port):
    response = upload({'main.py': source})
    found = issues(response)
    for rule in ['NET001', 'NET002']:
        assert found[rule]['confidence'] == 'potential'
        assert found[rule]['file_path'] == 'main.py'
        assert found[rule]['line_number'] > 1
    assert 'overridden' in found['NET001']['explanation']
    assert 'can be valid' in found['NET002']['explanation']


def test_environment_port_and_wildcard_host_are_not_network_findings():
    source = FASTAPI + '\nimport os, uvicorn\nuvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))'
    found = issues(upload({'main.py': source, '.env.example': 'PORT=8000'}))
    assert 'NET001' not in found
    assert 'NET002' not in found


@pytest.mark.parametrize('path', ['/var/app/data.sqlite', '/data', 'C:\\Users\\someone\\data.txt', '\\\\server\\share\\data'])
def test_absolute_paths_are_reported_without_returning_values(path):
    response = upload({'main.py': FASTAPI + f'\nfile_path = {path!r}'})
    found = issues(response)
    assert found['STO001']['confidence'] == 'potential'
    assert found['STO001']['file_path'] == 'main.py'
    assert found['STO001']['line_number'] == 4
    assert path not in str(response.json()['findings'])


def test_url_routes_are_not_treated_as_absolute_file_paths():
    response = upload({'main.py': FASTAPI + '\n@app.get("/api/health")\ndef health():\n    return {}'})
    assert 'STO001' not in issues(response)


@pytest.mark.parametrize('snippet', [
    'import sqlite3\nconnection = sqlite3.connect("data.db")',
    'import aiosqlite\nconnection = aiosqlite.connect("data.db")',
    'url = "sqlite:///data.db"',
    'with open("data.txt", "w") as stream:\n    stream.write("hello")',
    'from pathlib import Path\nPath("data.txt").write_text("hello")',
    'from pathlib import Path\nPath("data.txt").open(mode="a")',
    'from pathlib import Path\nstorage = Path("./uploads")\nstorage.mkdir(exist_ok=True)',
    'import os\nstorage_path = os.environ.get("STORAGE_PATH", "./data")',
])
def test_storage_hints_are_potential_and_not_automatically_critical(snippet):
    found = issues(upload({'main.py': FASTAPI + '\n' + snippet}))
    assert found['STO002']['severity'] == 'warning'
    assert found['STO002']['confidence'] == 'potential'
    assert 'SQLite is not inherently unsuitable' in found['STO002']['explanation']


@pytest.mark.parametrize('snippet', [
    'import sqlite3\nconnection = sqlite3.connect(":memory:")',
    'database_url = "postgresql://example.invalid/database"',
    'with open("data.txt", "r") as stream:\n    contents = stream.read()',
    'import os\ndatabase_url = os.getenv("DATABASE_URL")',
])
def test_nonpersistent_or_read_only_hints_do_not_trigger_storage(snippet):
    assert 'STO002' not in issues(upload({'main.py': FASTAPI + '\n' + snippet}))


@pytest.mark.parametrize('snippet', [
    'API_KEY = "do-not-return-this-credential"',
    'app.config["SECRET_KEY"] = "do-not-return-this-credential"',
    'app.config.update(SECRET_KEY="do-not-return-this-credential")',
    'import os\npassword = os.getenv("PASSWORD", "do-not-return-this-credential")',
    'config = {"password": "do-not-return-this-credential"}',
])
def test_potential_secrets_are_redacted_everywhere(snippet):
    response = upload({'main.py': FASTAPI + '\n' + snippet, '.env': 'ACTUAL_SECRET=also-never-return'})
    issue = issues(response)['SEC001']
    assert issue['severity'] == 'critical'
    assert issue['confidence'] == 'potential'
    assert issue['file_path'] == 'main.py'
    assert 'do-not-return-this-credential' not in response.text
    assert 'also-never-return' not in response.text


def test_environment_secret_with_no_literal_default_is_not_hardcoded():
    assert 'SEC001' not in issues(upload({'main.py': FASTAPI + '\nimport os\nAPI_KEY = os.getenv("API_KEY")'}))


def test_missing_container_files_are_informational_not_a_deployment_blocker():
    found = issues(upload({'main.py': FASTAPI}))
    assert found['CTR001']['severity'] == 'informational'
    assert found['CTR002']['severity'] == 'informational'
    assert 'does not prevent' in found['CTR001']['explanation']


def test_existing_container_files_remove_corresponding_findings():
    found = issues(upload({'main.py': FASTAPI, 'Dockerfile': 'FROM python:3.13', '.dockerignore': '.env'}))
    assert 'CTR001' not in found and 'CTR002' not in found


def test_only_referenced_undocumented_env_names_trigger_documentation_rule():
    response = upload({'main.py': FASTAPI + '\nimport os\nx = os.environ["NEEDED"]',
                       '.env.example': 'DOCUMENTED_ONLY=placeholder',
                       'tests/.env.example': 'NEEDED=not-a-production-declaration'})
    assert issues(response)['ENV001']['confidence'] == 'confirmed'
    assert issues(response)['ENV001']['line_number'] == 5
    assert 'ENV001' not in issues(upload({'main.py': FASTAPI + '\nimport os\nx = os.getenv("NEEDED")',
                                        '.env.example': 'export NEEDED=placeholder'}))


def test_unknown_dynamic_environment_does_not_invent_a_missing_key():
    response = upload({'main.py': FASTAPI + '\nimport os\nx = os.getenv("CONFIG_" + str(1))'})
    assert 'ENV001' not in issues(response)
    assert any('Computed environment' in item for item in response.json()['assessment_limitations'])


def test_unknown_production_binding_is_not_penalized_as_localhost():
    response = upload({'main.py': FASTAPI})
    assert 'NET001' not in issues(response)
    assert response.json()['assessment_provisional'] is True
    assert any('no penalty' in item for item in response.json()['assessment_limitations'])


def test_factories_receive_an_available_assessment():
    report = upload({'flaskr/__init__.py': 'from flask import Flask\ndef create_app():\n    return Flask(__name__)',
                     'requirements.txt': 'Flask'}).json()
    assert report['entry_point_type'] == 'application_factory'
    assert report['readiness_score'] is not None
    assert 'RUN001' not in {item['rule_id'] for item in report['findings']}


@pytest.mark.parametrize('files', [
    {'main.py': FLASK, 'other.py': FASTAPI},
    {'one/main.py': FLASK, 'one/requirements.txt': 'Flask', 'two/main.py': FASTAPI, 'two/requirements.txt': 'fastapi'},
    {'pyproject.toml': '[project]\nname = "Flask"', 'src/flask/__init__.py': 'from .app import Flask'},
    {'pyproject.toml': '[project]\nname = "library"', 'src/library/__init__.py': ''},
    {'main.py': 'print("utility")'},
])
def test_non_applications_and_ambiguity_have_no_score(files):
    report = upload(files).json()
    assert report['readiness_score'] is None
    assert report['readiness_status'] == 'unavailable'
    assert report['assessment_provisional'] is True
    assert report['findings'] == []
    assert report['severity_summary'] == {'critical': 0, 'warning': 0, 'informational': 0}
    assert report['assessment_limitations']


def make_finding(rule, severity='warning', confidence='confirmed'):
    return Finding(rule_id=rule, title='Test', severity=severity, confidence=confidence,
                   category='runtime', explanation='Test concern', recommended_fix='Test fix')


def test_score_bounds_weights_and_duplicate_groups():
    assert readiness.score_findings([]) == 100
    assert readiness.score_findings([make_finding('X', 'critical', 'confirmed')]) == 75
    assert readiness.score_findings([make_finding('X', 'critical', 'potential')]) == 85
    assert readiness.score_findings([make_finding('NET001'), make_finding('NET001'), make_finding('NET002')]) == 90
    assert readiness.score_findings([make_finding('STO001'), make_finding('STO002')]) == 90
    assert readiness.score_findings([make_finding('CTR001', 'informational'), make_finding('CTR002', 'informational')]) == 98
    assert readiness.score_findings([make_finding(str(index), 'critical') for index in range(20)]) == 0


@pytest.mark.parametrize(('score', 'status'), [(100, 'ready_for_migration_review'), (85, 'ready_for_migration_review'),
    (84, 'needs_improvements'), (60, 'needs_improvements'), (59, 'significant_migration_concerns'), (0, 'significant_migration_concerns')])
def test_classification_boundaries(score, status):
    assert readiness.score_status(score) == status


def test_clear_static_configuration_can_have_a_nonprovisional_score():
    report = upload({
        'main.py': FASTAPI + '\nimport os, uvicorn\nuvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT")))',
        'requirements.txt': 'fastapi\nuvicorn', '.python-version': '3.13',
        '.env.example': 'PORT=8000', 'Dockerfile': 'FROM python:3.13', '.dockerignore': '.env',
    }).json()
    assert report['readiness_score'] == 100
    assert report['findings'] == []
    assert report['assessment_provisional'] is False
    assert any('does not guarantee' in item for item in report['assessment_limitations'])


def test_repeated_signals_have_one_rule_and_one_deduction():
    report = upload({'main.py': FASTAPI + '\nimport uvicorn\nuvicorn.run(app, port=8000)\nuvicorn.run(app, port=8001)'}).json()
    assert sum(item['rule_id'] == 'NET002' for item in report['findings']) == 1
    assert len({item['rule_id'] for item in report['findings']}) == len(report['findings'])
    assert report['severity_summary']['warning'] == 2  # dependencies and port


def test_scope_and_secret_protection_survive_readiness():
    report = upload({'main.py': FASTAPI, 'requirements.txt': 'fastapi',
        'tests/test_config.py': FLASK + '\nAPI_KEY = "test-fixture-secret"\nwith open("data", "w") as f:\n    pass',
        'examples/app.py': FLASK + '\napp.run(host="localhost", port=5000)',
    }).json()
    assert not {'SEC001', 'STO002', 'NET001', 'NET002'} & {item['rule_id'] for item in report['findings']}
    assert report['entry_point'] == 'main:app'
    assert 'test-fixture-secret' not in str(report)


def test_priorities_summary_and_recommendations_are_deterministic():
    files = {'main.py': FASTAPI + '\nAPI_KEY = "private-value"', 'requirements.txt': 'fastapi'}
    first, second = upload(files).json(), upload(files).json()
    assert first == second
    assert first['findings'][0]['rule_id'] == 'SEC001'
    assert first['migration_recommendations'][0] == first['findings'][0]['recommended_fix']
    assert sum(first['severity_summary'].values()) == len(first['findings'])
    assert 'container-based PaaS' in first['migration_recommendations'][-1]


@pytest.mark.parametrize('name', ['flask-app', 'fastapi-app'])
def test_trusted_samples_get_real_assessments(name):
    root = Path(__file__).resolve().parents[2] / 'examples' / name
    report = upload({path.relative_to(root).as_posix(): path.read_bytes()
                     for path in root.rglob('*') if path.is_file()}).json()
    assert report['readiness_score'] == 87
    assert report['assessment_provisional'] is True
    assert {'NET001', 'NET002', 'STO002', 'CTR001', 'CTR002', 'RUN002'} <= {item['rule_id'] for item in report['findings']}
