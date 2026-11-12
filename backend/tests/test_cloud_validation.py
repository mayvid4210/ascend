"""Trusted context preparation is verified without building or executing any app."""

import importlib.util
import json
from pathlib import Path

import pytest

from test_analysis import FASTAPI
from test_generation import contents, generate

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('prepare_validation', ROOT / 'scripts' / 'prepare_validation.py')
preparation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preparation)


@pytest.mark.parametrize(('sample', 'source_name', 'server'), [
    ('flask-app', 'app.py', 'gunicorn'), ('fastapi-app', 'main.py', 'uvicorn'),
])
def test_trusted_context_uses_fresh_actual_generation(tmp_path, sample, source_name, server):
    output = tmp_path / 'context'
    manifest = preparation.prepare_context(sample, output)
    source = ROOT / 'examples' / sample
    assert (output / source_name).read_bytes() == (source / source_name).read_bytes()
    assert (output / 'requirements.txt').read_bytes() == (source / 'requirements.txt').read_bytes()
    assert (output / '.env.example').read_bytes() == (source / '.env.example').read_bytes()
    assert '.env.example' not in manifest['generated_files']
    assert manifest['validation_status'] == 'context_prepared_not_built'
    assert 'Dockerfile' in manifest['generated_files']
    dockerfile = (output / 'Dockerfile').read_text()
    assert 'FROM python:3.13-slim' in dockerfile
    assert 'USER app' in dockerfile and 'chown app:app /app' in dockerfile
    assert '-r requirements.ascend.txt' in dockerfile
    assert server in (output / 'requirements.ascend.txt').read_text()
    command = json.loads(next(line[4:] for line in dockerfile.splitlines() if line.startswith('CMD ')))
    assert f'python -m {server}' in command[2]
    assert '${PORT:-10000}' in command[2]
    assert 'SMOKE_TEST_PATH: "/health"' in (output / '.github/workflows/ascend-migration.yml').read_text()
    assert not (output / '.env').exists()


def test_preparer_does_not_read_docs_generated_dockerfiles(tmp_path, monkeypatch):
    original = preparation.generate_archive
    called = []
    def fresh(source, name):
        report = original(source, name)
        called.append(name)
        for item in report.generated_files:
            if item.path == 'Dockerfile':
                item.content += '# dynamically generated test marker\n'
        return report
    monkeypatch.setattr(preparation, 'generate_archive', fresh)
    output = tmp_path / 'context'
    preparation.prepare_context('fastapi-app', output)
    assert called == ['fastapi-app']
    assert '# dynamically generated test marker' in (output / 'Dockerfile').read_text()


def test_arbitrary_projects_and_existing_output_are_rejected(tmp_path):
    with pytest.raises(ValueError, match='repository-owned'):
        preparation.prepare_context('../../untrusted', tmp_path / 'context')
    output = tmp_path / 'existing'
    output.mkdir()
    marker = output / 'keep.txt'
    marker.write_text('preserve')
    with pytest.raises(ValueError, match='already exists'):
        preparation.prepare_context('flask-app', output)
    assert marker.read_text() == 'preserve'


def test_partial_context_is_cleaned_if_applying_configuration_fails(tmp_path, monkeypatch):
    original = preparation.generate_archive
    def conflicting(source, name):
        report = original(source, name)
        from app.models.generation import GeneratedFile
        report.generated_files.append(GeneratedFile(path='.env.example', content='must not overwrite'))
        return report
    monkeypatch.setattr(preparation, 'generate_archive', conflicting)
    output = tmp_path / 'context'
    with pytest.raises(RuntimeError, match='overwrite'):
        preparation.prepare_context('flask-app', output)
    assert not output.exists()


def test_generated_smoke_workflow_distinguishes_ports():
    files = contents(generate({'main.py': FASTAPI, 'requirements.txt': 'fastapi'}))
    workflow = files['.github/workflows/ascend-migration.yml']
    assert 'HOST_PORT: "10000"' in workflow
    assert 'CONTAINER_PORT: "10000"' in workflow
    assert 'CONTAINER_PORT_REVIEWED: "true"' in workflow
    assert '-p "127.0.0.1:${HOST_PORT}:${CONTAINER_PORT}"' in workflow
    assert '-e PORT="$CONTAINER_PORT"' in workflow
    assert 'http://127.0.0.1:${HOST_PORT}${SMOKE_TEST_PATH}' in workflow
    assert '127.0.0.1:10000:10000' not in workflow


def test_existing_dockerfile_requires_explicit_port_review_and_is_preserved():
    response = generate({'main.py': FASTAPI, 'requirements.txt': 'fastapi',
                         'Dockerfile': 'FROM python:3.13\nCMD custom-server --port 8080'})
    files = contents(response)
    assert 'Dockerfile' not in files
    assert 'Dockerfile' in response.json()['existing_files']
    workflow = files['.github/workflows/ascend-migration.yml']
    assert 'CONTAINER_PORT_REVIEWED: "false"' in workflow
    assert 'Existing images may ignore PORT' in workflow
    assert workflow.index('if [[ "$CONTAINER_PORT_REVIEWED"') < workflow.index('docker build')
    assert any('container port requires manual review' in item for item in response.json()['generation_warnings'])


def test_workflow_runs_fresh_matrix_with_http_validation_and_cleanup():
    workflow = (ROOT / '.github/workflows/validate-migration.yml').read_text()
    assert 'branches: [main]' in workflow
    assert 'workflow_dispatch:' in workflow
    assert 'sample: [flask-app, fastapi-app]' in workflow
    assert 'scripts/prepare_validation.py' in workflow
    assert 'docker build --progress=plain' in workflow
    assert 'status != 200 or payload != {"status": "ok"}' in workflow
    assert 'CONTAINER_PORT: \'18081\'' in workflow
    assert workflow.count('if: ${{ always() }}') == 2
    assert 'docker rm -f' in workflow and 'shutil.rmtree' in workflow
    assert 'docs/generated' not in workflow
