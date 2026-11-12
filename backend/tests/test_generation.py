"""Generation is tested as text/data; no uploaded app or Docker build is run."""

import base64
import io
import json
import tempfile
import zipfile
from pathlib import Path

import pytest

from app.api import generate as api
from test_analysis import FASTAPI, FLASK, client, zipped


def generate(files=None, raw=None, name='application.zip'):
    return client.post('/api/generate', files={'file': (name, raw if raw is not None else zipped(files or {}), 'application/zip')})


def contents(response):
    assert response.status_code == 200
    return {item['path']: item['content'] for item in response.json()['generated_files']}


@pytest.mark.parametrize(('source', 'framework', 'target', 'command'), [
    (FLASK, 'Flask', 'main:app', 'gunicorn'),
    (FASTAPI, 'fastapi', 'main:app', 'uvicorn'),
    ('from flask import Flask\ndef create_app(config=None):\n    return Flask(__name__)', 'Flask', "main:create_app()", 'gunicorn'),
    ('from fastapi import FastAPI\ndef create_app():\n    return FastAPI()', 'fastapi', 'main:create_app', 'uvicorn'),
])
def test_supported_targets_have_production_commands(source, framework, target, command):
    response = generate({'main.py': source, 'requirements.txt': framework})
    files = contents(response)
    assert response.json()['eligible'] is True
    assert response.json()['status'] == 'manual_review'
    docker = files['Dockerfile']
    cmd = json.loads(next(line[4:] for line in docker.splitlines() if line.startswith('CMD ')))
    assert cmd[:2] == ['sh', '-c']
    assert target in cmd[2] and f'python -m {command}' in cmd[2]
    assert '0.0.0.0' in cmd[2] and '${PORT:-10000}' in cmd[2]
    assert ('--factory' in cmd[2]) == (command == 'uvicorn' and 'create_app' in source)
    assert 'USER app' in docker and 'COPY --chown=app:app' in docker
    assert '-r requirements.txt' in docker
    assert command in files['requirements.ascend.txt']


def test_package_factory_keeps_its_package_target():
    files = contents(generate({'flaskr/__init__.py': 'from flask import Flask\ndef create_app():\n    return Flask(__name__)', 'requirements.txt': 'Flask'}))
    assert "'flaskr:create_app()'" in files['Dockerfile']


def test_src_layout_sets_pythonpath_and_adjusts_target():
    files = contents(generate({'src/api/main.py': FASTAPI, 'requirements.txt': 'fastapi'}))
    assert 'ENV PYTHONPATH=/app/src' in files['Dockerfile']
    assert 'uvicorn api.main:app' in files['Dockerfile']


@pytest.mark.parametrize(('declaration', 'image'), [('3.12', '3.12'), ('3.12.2', '3.12.2'), ('>=3.11,<3.13', '3.12')])
def test_detected_python_versions(declaration, image):
    metadata = {'.python-version': declaration} if declaration[0].isdigit() else {'pyproject.toml': f'[project]\nrequires-python = "{declaration}"'}
    files = contents(generate({'main.py': FASTAPI, 'requirements.txt': 'fastapi', **metadata}))
    assert f'FROM python:{image}-slim' in files['Dockerfile']


def test_default_python_is_configurable_and_disclosed(monkeypatch):
    monkeypatch.setenv('ASCEND_DEFAULT_PYTHON_VERSION', '3.12')
    response = generate({'main.py': FASTAPI, 'requirements.txt': 'fastapi'})
    assert 'FROM python:3.12-slim' in contents(response)['Dockerfile']
    assert any('configurable default 3.12' in warning for warning in response.json()['generation_warnings'])


def test_invalid_default_is_not_interpolated_into_a_docker_command(monkeypatch):
    monkeypatch.setenv('ASCEND_DEFAULT_PYTHON_VERSION', '3.13;echo unsafe')
    response = generate({'main.py': FASTAPI, 'requirements.txt': 'fastapi'})
    assert 'Dockerfile' not in contents(response)
    assert 'echo unsafe' not in response.text


@pytest.mark.parametrize('metadata', [{'.python-version': '3.8'}, {'pyproject.toml': '[project]\nrequires-python = ">=4"'}])
def test_unsupported_python_withholds_executable_files(metadata):
    response = generate({'main.py': FASTAPI, 'requirements.txt': 'fastapi', **metadata})
    assert 'Dockerfile' not in contents(response)
    assert 'render.yaml' not in contents(response)


def test_pyproject_installation_is_explicit_and_requires_review():
    response = generate({'main.py': FASTAPI, 'pyproject.toml': '[project]\nname = "service"\nversion = "0.1.0"\ndependencies = ["fastapi"]'})
    assert 'pip install --no-cache-dir .' in contents(response)['Dockerfile']
    assert any('package discovery' in warning for warning in response.json()['generation_warnings'])


def test_missing_framework_dependency_is_not_invented():
    response = generate({'main.py': FASTAPI, 'requirements.txt': 'requests'})
    assert 'Dockerfile' not in contents(response)
    assert any('framework is not explicitly declared' in warning for warning in response.json()['generation_warnings'])


def test_ignore_preserves_runtime_assets_and_marks_database_exclusions_for_review():
    output = contents(generate({'main.py': FASTAPI, 'requirements.txt': 'fastapi', 'assets/data.sqlite': b'data'}))
    active = {line.strip() for line in output['.dockerignore'].splitlines() if line.strip() and not line.startswith('#')}
    assert not {'dist', 'build', 'static', 'templates', 'assets', '**/*.sqlite', '**/*.db'} & active
    assert {'.git', '.venv', '**/.env', '**/.env.*'} <= active


def test_unsafe_root_names_do_not_become_workflow_expressions():
    response = generate({'README.md': 'wrapper', 'api$(unsafe)/main.py': FASTAPI, 'api$(unsafe)/requirements.txt': 'fastapi'})
    assert response.json()['eligible'] is False
    assert response.json()['download_zip_base64'] is None


@pytest.mark.parametrize('requirement', [None, '-r other.txt', 'private @ https://user:hidden-value@example.invalid/package.whl', '--index-url https://example.invalid'])
def test_unreliable_dependencies_withhold_executable_files(requirement):
    files = {'main.py': FASTAPI}
    if requirement is not None:
        files['requirements.txt'] = requirement
    response = generate(files)
    output = contents(response)
    assert 'Dockerfile' not in output and 'render.yaml' not in output
    assert '.github/workflows/ascend-migration.yml' not in output
    assert 'MIGRATION.md' in output
    assert 'hidden-value' not in response.text


@pytest.mark.parametrize('files', [
    {'main.py': 'print("utility")'},
    {'app.py': FLASK, 'main.py': FASTAPI},
    {'pyproject.toml': '[project]\nname = "Flask"', 'src/flask/__init__.py': 'from .app import Flask'},
    {'src/library/__init__.py': ''},
    {'__init__.py': FASTAPI},
    {'one/app.py': FLASK, 'one/requirements.txt': 'Flask', 'two/app.py': FASTAPI, 'two/requirements.txt': 'fastapi'},
])
def test_unsupported_and_ambiguous_projects_are_ineligible(files):
    response = generate(files)
    assert response.status_code == 200
    report = response.json()
    assert report['eligible'] is False and report['status'] == 'ineligible'
    assert report['generated_files'] == [] and report['download_zip_base64'] is None
    assert report['generation_warnings']


def test_existing_configurations_and_ci_are_never_copied_or_replaced():
    response = generate({'main.py': FASTAPI, 'requirements.txt': 'fastapi', 'Dockerfile': 'private-docker-content',
        '.dockerignore': 'private-ignore', '.env.example': 'API_KEY=private-env', 'render.yaml': 'private-yaml',
        '.github/workflows/test.yml': 'private-workflow', 'MIGRATION.md': 'private-guide'})
    output = contents(response)
    assert set(output) == {'MIGRATION.ascend-1.md'}
    for value in ['private-docker-content', 'private-ignore', 'private-env', 'private-yaml', 'private-workflow', 'private-guide']:
        assert value not in response.text
    assert len(response.json()['skipped_files']) == 5
    assert 'Existing Dockerfile reused without validation' in output['MIGRATION.ascend-1.md']


def test_nested_root_and_repository_level_ci_conflicts():
    response = generate({'README.md': 'wrapper', 'backend/main.py': FASTAPI, 'backend/requirements.txt': 'fastapi',
                         '.github/workflows/test.yml': 'private-ci', 'render.yaml': 'private-render'})
    output = contents(response)
    assert response.json()['analysis_root'] == 'backend'
    assert 'render.yaml' not in output and '.github/workflows/ascend-migration.yml' not in output
    assert 'Dockerfile' in output


def test_nested_root_context_is_used_in_new_workflow_and_render():
    response = generate({'README.md': 'wrapper', 'backend/main.py': FASTAPI, 'backend/requirements.txt': 'fastapi'})
    output = contents(response)
    assert 'APP_DIR: "backend"' in output['.github/workflows/ascend-migration.yml']
    assert 'dockerContext: "./backend"' in output['render.yaml']
    assert 'dockerfilePath: "./backend/Dockerfile"' in output['render.yaml']


def test_supplemental_name_conflicts_are_avoided():
    output = contents(generate({'main.py': FLASK, 'requirements.txt': 'Flask', 'requirements.ascend.txt': 'private-existing'}))
    assert 'requirements.ascend-2.txt' in output
    assert '-r requirements.ascend-2.txt' in output['Dockerfile']
    assert 'private-existing' not in str(output)


def test_specialized_runtime_dependencies_are_not_silently_invented():
    response = generate({'main.py': FASTAPI, 'requirements.txt': 'fastapi\nplaywright'})
    assert any('browser binaries' in warning for warning in response.json()['generation_warnings'])
    assert 'apt-get' not in contents(response)['Dockerfile']
    assert 'playwright install' not in contents(response)['Dockerfile']


def test_generated_environment_names_defaults_and_secrets():
    response = generate({'main.py': FASTAPI + '\nimport os\nAPI_KEY = os.getenv("API_KEY", "source-secret-value")\nDEBUG = os.getenv("DEBUG", "default-secret-value")\nDB = os.environ["DATABASE_URL"]',
        'requirements.txt': 'fastapi', '.env': 'OTHER_SECRET=env-secret-value'})
    output = contents(response)
    assert '# DEBUG=' in output['.env.example']
    assert 'DATABASE_URL=' in output['.env.example']
    assert 'default_detected' in output['.env.example']
    for value in ['source-secret-value', 'default-secret-value', 'env-secret-value']:
        assert value not in response.text
        assert value not in base64.b64decode(response.json()['download_zip_base64']).decode('latin1')
    assert 'sync: false' in output['render.yaml']
    assert 'key: DEBUG' not in output['render.yaml']


def test_known_get_route_and_configurable_unknown_route():
    response = generate({'main.py': FASTAPI + '\n@app.get("/health")\ndef health():\n    return {}', 'requirements.txt': 'fastapi'})
    assert 'healthCheckPath: "/health"' in contents(response)['render.yaml']
    assert 'SMOKE_TEST_PATH: "/health"' in contents(response)['.github/workflows/ascend-migration.yml']
    unknown = contents(generate({'main.py': FASTAPI, 'requirements.txt': 'fastapi'}))
    assert 'healthCheckPath:' not in unknown['render.yaml']
    assert 'SMOKE_TEST_PATH: "/"' in unknown['.github/workflows/ascend-migration.yml']


def test_test_only_routes_and_env_are_not_used_for_generation():
    output = contents(generate({'main.py': FASTAPI, 'requirements.txt': 'fastapi',
        'tests/test_app.py': FASTAPI + '\nimport os\nTEST = os.getenv("TEST_ONLY")\n@app.get("/health")\ndef health():\n    return {}'}))
    assert 'TEST_ONLY' not in output['.env.example']
    assert 'healthCheckPath:' not in output['render.yaml']


def test_zip_is_deterministic_controlled_and_contains_no_source():
    files = {'main.py': FASTAPI, 'requirements.txt': 'fastapi', 'data/private.txt': 'private-source'}
    first, second = generate(files).json(), generate(files).json()
    assert first == second
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(first['download_zip_base64']))) as archive:
        assert set(archive.namelist()) == {item['path'] for item in first['generated_files']}
        for name in archive.namelist():
            assert not name.startswith('/') and '..' not in Path(name).parts and '\\' not in name
            assert name not in {'main.py', 'requirements.txt', '.env', 'data/private.txt'}
            assert b'private-source' not in archive.read(name)
    assert 'plan: free' in contents(generate(files))['render.yaml']
    assert 'autoDeployTrigger: "off"' in contents(generate(files))['render.yaml']


@pytest.mark.parametrize('raw', [b'not-a-zip', zipped({'../escape.py': FASTAPI})])
def test_upload_protection_is_shared(raw):
    assert generate(raw=raw).status_code == 400


@pytest.mark.parametrize('failure', [False, True])
def test_temporary_cleanup_on_success_and_failure(tmp_path, monkeypatch, failure):
    created = []
    def temporary(**kwargs):
        directory = tempfile.TemporaryDirectory(dir=tmp_path, **kwargs)
        created.append(Path(directory.name))
        return directory
    monkeypatch.setattr(api, 'TemporaryDirectory', temporary)
    response = generate({'main.py': FASTAPI, 'requirements.txt': 'fastapi'}, raw=b'broken' if failure else None)
    assert response.status_code == (400 if failure else 200)
    assert created and all(not path.exists() for path in created)


def test_cleanup_if_generator_raises(tmp_path, monkeypatch):
    def fail(*args):
        raise RuntimeError('failure')
    def temporary(**kwargs):
        return tempfile.TemporaryDirectory(dir=tmp_path, **kwargs)
    monkeypatch.setattr(api, 'TemporaryDirectory', temporary)
    monkeypatch.setattr(api, 'generate', fail)
    with pytest.raises(RuntimeError):
        generate({'main.py': FASTAPI, 'requirements.txt': 'fastapi'})
    assert list(tmp_path.iterdir()) == []


def test_generation_does_not_execute_source(tmp_path):
    marker = tmp_path / 'must-not-exist'
    source = FASTAPI + f'\nfrom pathlib import Path\nPath({str(marker)!r}).write_text("executed")'
    assert generate({'main.py': source, 'requirements.txt': 'fastapi'}).status_code == 200
    assert not marker.exists()
