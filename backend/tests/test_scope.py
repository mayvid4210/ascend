"""Regression cases for repository classification and production analysis scope."""

import pytest

from test_analysis import FASTAPI, FLASK, upload


def test_framework_source_repository_does_not_promote_its_examples_or_tests():
    response = upload({
        'flask/pyproject.toml': '''[project]
name = "Flask"
dependencies = ["Werkzeug>=3.1", "Jinja2>=3.1"]
''',
        'flask/src/flask/__init__.py': 'from .app import Flask',
        'flask/src/flask/app.py': 'class Flask:\n    pass',
        'flask/tests/conftest.py': FLASK + '\nimport os\ntoken = os.getenv("TEST_TOKEN")',
        'flask/examples/demo/app.py': FLASK + '\nimport os\nkey = os.getenv("EXAMPLE_KEY")',
        'flask/examples/demo/requirements.txt': 'Flask\nexample-only==1',
        'flask/examples/demo/.env.example': 'EXAMPLE_SETTING=not-a-secret',
    })
    assert response.status_code == 200
    report = response.json()
    assert report['project_type'] == 'framework_repository'
    assert report['framework'] == 'Flask'
    assert report['entry_point'] is None
    assert report['entry_point_candidates'] == []
    assert report['environment_variables'] == []
    assert report['dependency_files'] == ['pyproject.toml']
    assert {item['name'] for item in report['dependencies']} == {'Werkzeug', 'Jinja2'}
    assert {item['scope'] for item in report['excluded_application_objects']} == {'test', 'example'}
    assert any('Upload a specific application directory' in item for item in report['analysis_warnings'])


def test_library_with_flask_imports_is_not_a_web_application():
    report = upload({
        'pyproject.toml': '[project]\nname = "flask-extension"\ndependencies = ["Flask>=3"]',
        'src/extension/__init__.py': 'from flask import current_app\ndef helper():\n    return current_app.name',
        'tests/test_extension.py': FLASK,
    }).json()
    assert report['project_type'] == 'python_library'
    assert report['framework'] == 'Flask'
    assert report['entry_point_candidates'] == []


def test_imports_alone_do_not_establish_a_deployable_application():
    report = upload({'main.py': 'from flask import Flask\nprint("utility")'}).json()
    assert report['project_type'] == 'unsupported'
    assert report['entry_point'] is None


@pytest.mark.parametrize('test_path', ['tests/app.py', 'test/app.py', 'tests/fixtures/main.py',
                                      'conftest.py', 'test_app.py', 'app_test.py'])
def test_test_only_application_objects_are_never_primary(test_path):
    report = upload({test_path: FLASK + '\nimport os\nx = os.getenv("TEST_ONLY")'}).json()
    assert report['project_type'] == 'unsupported'
    assert report['entry_point_candidates'] == []
    assert report['environment_variables'] == []
    assert report['excluded_application_objects'] == [
        {'path': test_path, 'object_name': 'app', 'scope': 'test'},
    ]


def test_primary_app_excludes_all_auxiliary_runtime_and_declarations():
    files = {
        'main.py': FASTAPI + '\nimport os\nx = os.getenv("PRODUCTION_KEY")',
        'requirements.txt': 'fastapi==0.162.5',
        '.env.example': 'ROOT_KEY=placeholder',
    }
    for directory in ['tests', 'docs', 'examples', 'benchmarks', 'tools']:
        files[f'{directory}/main.py'] = FLASK + f'\nimport os\nx = os.getenv("{directory.upper()}_ONLY")'
        files[f'{directory}/requirements.txt'] = 'unrelated==1'
        files[f'{directory}/.env.example'] = 'AUXILIARY_KEY=placeholder'
        files[f'{directory}/Dockerfile'] = 'FROM python:3'
    report = upload(files).json()
    assert report['project_type'] == 'web_application'
    assert report['framework'] == 'FastAPI'
    assert report['entry_point'] == 'main:app'
    assert report['environment_variables'] == ['PRODUCTION_KEY', 'ROOT_KEY']
    assert report['dependencies'] == [{'name': 'fastapi', 'version': '==0.162.5'}]
    assert report['dockerfile_present'] is False
    assert len(report['excluded_application_objects']) == 5


@pytest.mark.parametrize('prefix', ['', 'demo/', 'examples/'])
def test_an_example_uploaded_as_its_own_root_remains_supported(prefix):
    report = upload({
        f'{prefix}app.py': FLASK,
        f'{prefix}requirements.txt': 'Flask>=3',
        f'{prefix}tests/app.py': FASTAPI,
    }).json()
    assert report['project_type'] == 'web_application'
    assert report['entry_point'] == 'app:app'
    assert report['framework'] == 'Flask'
    assert report['dependencies'] == [{'name': 'Flask', 'version': '>=3'}]


def test_single_nested_application_selects_its_local_declarations():
    report = upload({
        'README.md': 'Repository wrapper',
        'backend/main.py': FASTAPI,
        'backend/pyproject.toml': '[project]\nname = "service"\ndependencies = ["fastapi>=0.162"]',
        'examples/demo/app.py': FLASK,
        'examples/demo/requirements.txt': 'Flask==3.1.2',
    }).json()
    assert report['analysis_root'] == 'backend'
    assert report['project_root_candidates'] == ['backend']
    assert report['entry_point'] == 'main:app'
    assert report['dependencies'] == [{'name': 'fastapi', 'version': '>=0.162'}]


def test_multiple_legitimate_roots_do_not_mix_runtime_metadata():
    report = upload({
        'README.md': 'Two services',
        'apps/one/main.py': FASTAPI + '\nimport os\nx = os.getenv("ONE_KEY")',
        'apps/one/requirements.txt': 'fastapi==0.162.5',
        'apps/two/main.py': FLASK + '\nimport os\nx = os.getenv("TWO_KEY")',
        'apps/two/requirements.txt': 'Flask==3.1.2',
    }).json()
    assert report['project_type'] == 'ambiguous'
    assert report['analysis_root'] is None
    assert report['project_root_candidates'] == ['apps/one', 'apps/two']
    assert report['entry_point'] is None
    assert len(report['entry_point_candidates']) == 2
    assert report['dependencies'] == []
    assert report['dependency_files'] == []
    assert report['dependency_file_present'] is False
    assert report['environment_variables'] == []
    assert any('Multiple project roots' in item for item in report['analysis_warnings'])


def test_multiple_apps_in_one_root_stay_ambiguous():
    report = upload({'app.py': FLASK, 'other.py': FLASK, 'requirements.txt': 'Flask'}).json()
    assert report['project_type'] == 'ambiguous'
    assert report['analysis_root'] == '.'
    assert report['entry_point'] is None
    assert report['entry_point_candidates'] == ['app:app', 'other:app']


def test_root_library_and_nested_service_are_not_silently_collapsed():
    report = upload({
        'pyproject.toml': '[project]\nname = "library"\ndependencies = ["requests"]',
        'src/library/__init__.py': '',
        'service/main.py': FASTAPI,
        'service/requirements.txt': 'fastapi',
    }).json()
    assert report['project_type'] == 'ambiguous'
    assert report['project_root_candidates'] == ['.', 'service']
    assert report['dependencies'] == []
    assert report['entry_point'] is None


def test_pep621_dependencies_exclude_optional_and_development_groups():
    response = upload({
        'main.py': FASTAPI,
        'pyproject.toml': '''[project]
name = "service"
requires-python = ">=3.11"
dependencies = ["fastapi>=0.162", "uvicorn[standard]>=0.54; python_version >= '3.11'", "private @ https://user:secret@example.com/pkg.whl"]
[project.optional-dependencies]
test = ["pytest", "test-only"]
[dependency-groups]
dev = ["dev-only"]
''',
    })
    report = response.json()
    assert report['project_type'] == 'web_application'
    assert report['dependency_file_present'] is True
    assert report['dependency_files'] == ['pyproject.toml']
    assert report['python_version'] == '>=3.11'
    assert report['dependencies'] == [
        {'name': 'fastapi', 'version': '>=0.162'},
        {'name': 'private', 'version': None},
        {'name': 'uvicorn', 'version': '>=0.54'},
    ]
    assert 'user:secret' not in response.text


def test_requirements_at_selected_root_take_precedence_over_pyproject():
    report = upload({
        'main.py': FASTAPI, 'requirements.txt': 'fastapi==0.162.5',
        'pyproject.toml': '[project]\ndependencies = ["requests==2"]',
    }).json()
    assert report['dependencies'] == [{'name': 'fastapi', 'version': '==0.162.5'}]
    assert report['dependency_files'] == ['requirements.txt']


@pytest.mark.parametrize('declaration', ['dependencies = "invalid"', 'dynamic = ["dependencies"]'])
def test_invalid_or_dynamic_dependencies_are_not_invented(declaration):
    report = upload({'main.py': FASTAPI, 'pyproject.toml': f'[project]\n{declaration}'}).json()
    assert report['dependencies'] == []
    assert report['analysis_warnings']


@pytest.mark.parametrize(('source', 'framework'), [(FLASK, 'Flask'), (FASTAPI, 'FastAPI')])
def test_existing_apps_are_classified_without_claiming_readiness(source, framework):
    report = upload({'main.py': source, 'requirements.txt': framework}).json()
    assert report['project_type'] == 'web_application'
    assert report['framework'] == framework
    assert report['entry_point'] == 'main:app'
    assert report['readiness_score'] is not None
    assert report['assessment_provisional'] is True
    assert any('does not guarantee' in item for item in report['assessment_limitations'])


def test_non_web_package_without_pyproject_is_a_library():
    report = upload({'README.md': 'Library', 'src/utils/__init__.py': 'def helper():\n    return 1'}).json()
    assert report['project_type'] == 'python_library'
    assert report['entry_point'] is None


def test_legacy_setup_file_is_metadata_not_an_application():
    report = upload({
        'setup.py': FLASK + '\nraise RuntimeError("must never execute")',
        'utilities.py': 'def helper():\n    return 1',
    }).json()
    assert report['project_type'] == 'python_library'
    assert report['entry_point_candidates'] == []
    assert report['excluded_application_objects'][0]['scope'] == 'tooling'


def test_primary_runtime_environment_detection_keeps_function_local_imports():
    report = upload({
        'main.py': FASTAPI + '\ndef configure():\n    import os\n    return os.getenv("APP_SETTING")',
        'tests/test_config.py': 'def fixture():\n    import os\n    return os.getenv("TEST_SETTING")',
    }).json()
    assert report['project_type'] == 'web_application'
    assert report['environment_variables'] == ['APP_SETTING']
