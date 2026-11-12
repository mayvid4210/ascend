"""Phase 3.1: evidence accuracy without changing scoring weights or scope rules."""

import pytest

from app.services.readiness import score_findings
from app.models.readiness import Finding
from test_analysis import FASTAPI, upload

DEV = FASTAPI + '\nimport uvicorn\nif __name__ == "__main__":\n    uvicorn.run(app, host="127.0.0.1", port=8000)'


def analyze(source=DEV, docker='FROM python:3.13', **extra):
    files = {'main.py': source, 'requirements.txt': 'fastapi\nuvicorn', '.python-version': '3.13', '.dockerignore': '.env'}
    if docker is not None:
        files['Dockerfile'] = docker
    files.update(extra)
    response = upload(files)
    assert response.status_code == 200
    return response.json()


def rules(report):
    return {issue['rule_id']: issue for issue in report['findings']}


def test_env_names_and_usage_are_exact_sorted_and_value_free():
    source = FASTAPI + '''
import os
one = os.environ["DATABASE_URL"]
two = os.getenv("DEBUG", "do-not-echo-default")
three = os.environ.get("TOKEN")
four = os.getenv("RETRIES", 0)
five = os.getenv("OPTIONAL_NONE", None)
six = os.getenv("DOCUMENTED", "also-not-returned")
'''
    report = analyze(source, **{'.env.example': 'DOCUMENTED=example-value', 'tests/.env.example': 'TOKEN=test-only-value'})
    env = rules(report)['ENV001']
    assert env['environment_variables'] == [
        {'name': 'DATABASE_URL', 'usage': 'required_access'},
        {'name': 'DEBUG', 'usage': 'default_detected'},
        {'name': 'OPTIONAL_NONE', 'usage': 'no_default_detected'},
        {'name': 'RETRIES', 'usage': 'default_detected'},
        {'name': 'TOKEN', 'usage': 'no_default_detected'},
    ]
    assert 'DEBUG (default detected; may be optional)' in env['explanation']
    assert 'TOKEN (no default detected; potentially required)' in env['explanation']
    assert 'DOCUMENTED' not in env['explanation']
    for value in ['do-not-echo-default', 'also-not-returned', 'example-value', 'test-only-value']:
        assert value not in str(report)


def test_stricter_environment_access_is_not_hidden_by_an_optional_default():
    report = analyze(FASTAPI + '\nimport os\na = os.getenv("SHARED", "default")\nb = os.environ["SHARED"]')
    assert rules(report)['ENV001']['environment_variables'] == [{'name': 'SHARED', 'usage': 'required_access'}]


def test_keyword_defaults_and_computed_environment_names():
    report = analyze(FASTAPI + '\nimport os\na = os.getenv(key="OPTION", default=False)\nb = os.getenv("PREFIX_" + str(1))')
    assert rules(report)['ENV001']['environment_variables'] == [{'name': 'OPTION', 'usage': 'default_detected'}]
    assert any('Computed environment' in item for item in report['assessment_limitations'])


def test_environment_assignment_is_not_claimed_to_require_an_existing_value():
    report = analyze(FASTAPI + '\nimport os\nos.environ["SET_BY_CODE"] = "placeholder"')
    assert 'ENV001' not in rules(report)


@pytest.mark.parametrize('command', [
    'CMD uvicorn main:app --host 0.0.0.0 --port $PORT',
    'ENTRYPOINT uvicorn main:app --host 0.0.0.0 --port ${PORT}',
    'CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port $PORT"]',
    'ENTRYPOINT ["uvicorn", "main:app"]\nCMD ["--host", "0.0.0.0", "--port", "8001"]',
    'CMD ["python", "-m", "uvicorn", "main:app", "--host=0.0.0.0", "--port=8001"]',
])
def test_matching_production_commands_resolve_guarded_loopback(command):
    report = analyze(docker='FROM python:3.13\n' + command)
    found = rules(report)
    assert 'NET001' not in found
    if '$PORT' in command or '${PORT}' in command:
        assert 'NET002' not in found
        assert report['readiness_score'] == 100
    else:
        assert found['NET002']['file_path'] == 'Dockerfile'
        assert report['readiness_score'] == 95
    assert any('matching production Uvicorn' in item for item in report['assessment_limitations'])
    assert any('not been validated' in item for item in report['assessment_limitations'])


@pytest.mark.parametrize('command', [
    'CMD uvicorn main:app --host 0.0.0.0 && echo private-command-value',
    'CMD ["uvicorn", "other:app", "--host", "0.0.0.0"]',
    'CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--host", "127.0.0.1"]',
    'CMD ["uvicorn", "main:app", "--host", "0.0.0.0"]\nCMD python main.py',
    'CMD ["uvicorn", invalid-json]',
    'ENTRYPOINT ["/entrypoint.sh"]\nCMD ["uvicorn", "main:app", "--host", "0.0.0.0"]',
    'CMD ["sh", "-c", "if true; then uvicorn main:app --host 0.0.0.0; fi"]',
    "CMD uvicorn main:app --host 0.0.0.0 --port '$PORT'",
    r'CMD uvicorn main:app --host 0.0.0.0 --port \$PORT',
])
def test_ambiguous_commands_keep_potential_python_findings(command):
    report = analyze(docker='FROM python:3.13\n' + command)
    assert {'NET001', 'NET002'} <= rules(report).keys()
    assert report['assessment_provisional'] is True
    assert any('ambiguous, or unsupported' in item for item in report['assessment_limitations'])
    assert 'private-command-value' not in str(report)


def test_exec_form_does_not_expand_environment_variables():
    report = analyze(docker='FROM python:3.13\nCMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "$PORT"]')
    assert 'NET001' not in rules(report)
    assert 'NET002' in rules(report)
    assert report['assessment_provisional'] is True


def test_custom_docker_shell_is_not_assumed_to_expand_posix_environment():
    report = analyze(docker='FROM python:3.13\nSHELL ["custom-shell", "-c"]\nCMD uvicorn main:app --host 0.0.0.0 --port $PORT')
    assert {'NET001', 'NET002'} <= rules(report).keys()


def test_import_time_python_binding_is_not_assumed_overridden():
    source = DEV.replace('if __name__ == "__main__":\n    ', '')
    report = analyze(source, docker='FROM python:3.13\nCMD uvicorn main:app --host 0.0.0.0 --port $PORT')
    assert {'NET001', 'NET002'} <= rules(report).keys()


def test_production_loopback_and_literal_port_have_docker_locations():
    report = analyze(FASTAPI, docker='FROM python:3.13\nCMD uvicorn main:app --host 127.0.0.1 --port 8001')
    for rule in ['NET001', 'NET002']:
        assert rules(report)[rule]['file_path'] == 'Dockerfile'
        assert rules(report)[rule]['line_number'] == 2
        assert rules(report)[rule]['confidence'] == 'potential'


def test_split_entrypoint_and_cmd_preserve_correct_option_locations():
    report = analyze(FASTAPI, docker='FROM python:3.13\nENTRYPOINT ["uvicorn", "main:app", "--host", "127.0.0.1"]\nCMD ["--port", "8001"]')
    assert rules(report)['NET001']['line_number'] == 2
    assert rules(report)['NET002']['line_number'] == 3


@pytest.mark.parametrize('additional', [
    {'compose.yaml': 'services: {}'},
    {'Dockerfile.dev': 'FROM python:3.13'},
])
def test_possible_deployment_overrides_do_not_silently_resolve_networking(additional):
    report = analyze(docker='FROM python:3.13\nCMD uvicorn main:app --host 0.0.0.0 --port $PORT', **additional)
    assert 'NET001' in rules(report)


def test_factory_production_target_requires_factory_flag():
    source = 'from fastapi import FastAPI\ndef create_app():\n    return FastAPI()'
    report = analyze(source, docker='FROM python:3.13\nCMD uvicorn main:create_app --factory --host 0.0.0.0 --port $PORT')
    assert report['entry_point_type'] == 'application_factory'
    assert any('matching production Uvicorn' in item for item in report['assessment_limitations'])


@pytest.mark.parametrize('snippet', [
    'API_PATH = "/api/v1"', 'endpoint = "/users/me"', 'urls = ["/auth/login", "/health"]',
    'route_path = "/public/data"',
    '@app.get(path="/api/v1")\ndef health():\n    return {}',
    'app.add_api_route("/api/v1", lambda: {})',
    'import requests\nrequests.get("/api/v1")',
    'app.mount("/static/assets", object())',
])
def test_url_strings_are_not_filesystem_paths(snippet):
    assert 'STO001' not in rules(analyze(FASTAPI + '\n' + snippet))


@pytest.mark.parametrize('snippet', [
    'open("/srv/private/data.txt")',
    'from pathlib import Path\nPath("/Users/Some User/data.txt")',
    'import sqlite3\nsqlite3.connect("/var/lib/app/data.db")',
    'app.config["UPLOAD_FOLDER"] = "/srv/uploads"',
    'config = {"UPLOAD_FOLDER": "/srv/uploads"}',
    'file_path = "/var/app/data.txt"',
    'location = "/srv/data"\nopen(location)',
])
def test_filesystem_context_preserves_legitimate_absolute_paths_without_values(snippet):
    report = analyze(FASTAPI + '\n' + snippet)
    issue = rules(report)['STO001']
    assert issue['file_path'] == 'main.py'
    assert issue['line_number'] is not None
    assert '/srv/private' not in str(report)
    assert '/Users/Some User' not in str(report)


def test_existing_configuration_recommendations_reuse_not_replace():
    report = analyze(FASTAPI, docker='FROM python:3.13\nCMD uvicorn main:app --host 0.0.0.0',
                     **{'render.yaml': 'services: []'})
    recommendations = '\n'.join(report['migration_recommendations'])
    assert 'reuse the existing Dockerfile' in recommendations
    assert 'reuse relevant existing deployment configurations' in recommendations
    assert 'not validated' in recommendations
    assert 'add and review a Dockerfile' not in recommendations
    assert 'CTR001' not in rules(report)


def test_network_group_scores_remain_consistent_as_findings_change():
    unresolved = analyze()
    fixed_port = analyze(docker='FROM python:3.13\nCMD uvicorn main:app --host 0.0.0.0 --port 8001')
    resolved = analyze(docker='FROM python:3.13\nCMD uvicorn main:app --host 0.0.0.0 --port $PORT')
    assert unresolved['readiness_score'] == fixed_port['readiness_score'] == 95
    assert resolved['readiness_score'] == 100
    for report in [unresolved, fixed_port, resolved]:
        assert report['readiness_score'] == score_findings([Finding(**issue) for issue in report['findings']])
        assert sum(report['severity_summary'].values()) == len(report['findings'])
