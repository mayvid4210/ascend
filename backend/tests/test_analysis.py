"""Use in-memory ZIPs: uploaded code is never imported by these tests."""

import io
import stat
import struct
import tempfile
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api import analyze as api
from app.services import archive

client = TestClient(app)
FLASK = 'from flask import Flask\napp = Flask(__name__)\n'
FASTAPI = 'from fastapi import FastAPI\napp = FastAPI()\n'


def zipped(files: dict[str, str | bytes]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as output:
        for name, content in files.items():
            output.writestr(name, content)
    return stream.getvalue()


def upload(files=None, *, raw=None, filename='project.zip'):
    return client.post('/api/analyze', files={
        'file': (filename, raw if raw is not None else zipped(files or {}), 'application/zip'),
    })


@pytest.mark.parametrize(('source', 'framework', 'entry'), [
    (FLASK, 'Flask', 'app:app'), (FASTAPI, 'FastAPI', 'app:app'),
    ('import fastapi as fa\napplication = fa.FastAPI()', 'FastAPI', 'app:application'),
    ('from flask import Flask as Web\nweb = Web(__name__)', 'Flask', 'app:web'),
])
def test_framework_and_entry_point(source, framework, entry):
    response = upload({'app.py': source, 'requirements.txt': framework})
    assert response.status_code == 200
    report = response.json()
    assert report['framework'] == framework
    assert report['entry_point'] == entry
    assert report['entry_point_candidates'] == [entry]
    assert report['dependency_file_present'] is True


def test_nested_root_and_sorted_results():
    report = upload({'repo/backend/main.py': FASTAPI, 'repo/requirements.txt': 'fastapi==0.162.5'}).json()
    assert report['project_name'] == 'repo'
    assert report['entry_point'] == 'backend.main:app'
    assert report['files_scanned'] == 2


def test_dependencies_are_parsed_without_following_directives():
    response = upload({'app.py': FLASK, 'requirements.txt': '''# comment
Flask==3.1.2
requests>=2.32,<3 # comment
uvicorn[standard]
-r other.txt
private @ https://user:secret@example.com/private.whl
''', 'other.txt': 'not-followed==1'})
    assert response.status_code == 200
    report = response.json()
    assert report['dependencies'] == [
        {'name': 'Flask', 'version': '==3.1.2'},
        {'name': 'private', 'version': None},
        {'name': 'requests', 'version': '<3,>=2.32'},
        {'name': 'uvicorn', 'version': None},
    ]
    assert report['analysis_warnings']
    assert 'user:secret' not in response.text
    assert 'https://' not in response.text


def test_entry_point_ambiguity():
    report = upload({'app.py': FLASK, 'main.py': FASTAPI}).json()
    assert report['framework'] == 'Unknown'
    assert report['entry_point'] is None
    assert report['entry_point_candidates'] == ['app:app', 'main:app']
    assert any('Multiple entry points' in item for item in report['analysis_warnings'])


def test_direct_factory_is_detected_as_an_entry_point():
    report = upload({'app.py': 'from flask import Flask\ndef create_app():\n    return Flask(__name__)'}).json()
    assert report['framework'] == 'Flask'
    assert report['entry_point'] == 'app:create_app'
    assert report['entry_point_type'] == 'application_factory'


def test_environment_names_and_no_secret_values():
    response = upload({
        'main.py': FASTAPI + '''
import os as operating
from os import getenv as env
from os import environ
key = operating.getenv("API_KEY", "source-secret")
db = environ["DATABASE_URL"]
mode = environ.get("MODE", "hidden-mode")
token = env("TOKEN", "token-secret")
''',
        '.env': 'PASSWORD=actual-secret\nACTUAL_ENV_ONLY=private-value',
        '.env.production': 'PRODUCTION_PASSWORD=production-secret',
        '.env.example': 'EXAMPLE_KEY=example-secret\nexport OTHER_KEY=other-secret',
    })
    assert response.status_code == 200
    report = response.json()
    assert report['environment_variables'] == ['API_KEY', 'DATABASE_URL', 'EXAMPLE_KEY', 'MODE', 'OTHER_KEY', 'TOKEN']
    assert report['env_example_present'] is True
    assert report['files_scanned'] == 2
    for secret in ['source-secret', 'hidden-mode', 'token-secret', 'actual-secret',
                   'private-value', 'production-secret', 'example-secret', 'other-secret', 'ACTUAL_ENV_ONLY']:
        assert secret not in response.text


def test_runtime_bindings_and_deployment_metadata():
    report = upload({
        'main.py': FASTAPI + '\nimport uvicorn\nuvicorn.run(app, host="0.0.0.0", port=8080)',
        '.python-version': '3.13.9\n', 'Dockerfile': 'FROM python:3.13',
        '.dockerignore': '.env', '.github/workflows/test.yml': 'name: Test',
        'render.yaml': 'services: []', 'k8s/app.yaml': 'kind: Deployment',
    }).json()
    assert report['python_version'] == '3.13.9'
    assert report['detected_ports'] == [8080]
    assert report['detected_hosts'] == ['0.0.0.0']
    assert report['dockerfile_present'] is True
    assert report['dockerignore_present'] is True
    assert report['deployment_files'] == ['.dockerignore', '.github/workflows/test.yml', 'Dockerfile', 'k8s/app.yaml', 'render.yaml']


@pytest.mark.parametrize(('name', 'value', 'expected'), [
    ('runtime.txt', 'python-3.12.1', '3.12.1'),
    ('pyproject.toml', '[project]\nrequires-python = ">=3.11,<4"', '>=3.11,<4'),
    ('setup.cfg', '[options]\npython_requires = >=3.11', '>=3.11'),
])
def test_python_declarations(name, value, expected):
    report = upload({'main.py': FASTAPI, name: value}).json()
    assert report['python_version'] == expected


def test_conflicting_python_versions_are_unknown():
    report = upload({'main.py': FASTAPI, '.python-version': '3.12', 'runtime.txt': 'python-3.13'}).json()
    assert report['python_version'] is None
    assert any('Multiple Python' in warning for warning in report['analysis_warnings'])


def test_unknown_python_project_returns_explicit_warnings():
    report = upload({'main.py': 'print("do not execute")'}).json()
    assert report['framework'] == 'Unknown'
    assert report['entry_point'] is None
    assert report['dependencies'] == []
    assert report['dependency_file_present'] is False
    assert any('Unsupported' in warning for warning in report['analysis_warnings'])


def test_non_python_project_is_rejected():
    assert upload({'index.js': 'console.log("hello")'}).status_code == 422


@pytest.mark.parametrize('name', ['.git', '.venv', 'venv', '__pycache__', 'node_modules', 'dist', 'build'])
def test_generated_files_are_ignored(name):
    report = upload({'main.py': FASTAPI, f'{name}/app.py': FLASK}).json()
    assert report['framework'] == 'FastAPI'
    assert report['files_scanned'] == 1


@pytest.mark.parametrize('raw', [b'not a zip', b'', b'PK\x03\x04broken'])
def test_invalid_zip(raw):
    response = upload(raw=raw)
    assert response.status_code == 400
    assert 'ZIP' in response.json()['detail']


def test_empty_zip():
    assert upload(raw=zipped({})).status_code == 400


@pytest.mark.parametrize('name', ['../escape.py', '/absolute.py', 'x/../../escape.py',
                                  'C:/escape.py', 'x\\escape.py', 'x/../escape.py',
                                  '.git/../../escape.py', 'app.py:stream', 'bad./app.py'])
def test_unsafe_paths(name):
    response = upload({'main.py': FASTAPI, name: 'unsafe'})
    assert response.status_code == 400
    assert 'unsafe path' in response.json()['detail']


def test_symlink_rejected_even_in_ignored_directory():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as output:
        link = zipfile.ZipInfo('.venv/link')
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        output.writestr(link, '/etc/passwd')
        output.writestr('main.py', FASTAPI)
    assert upload(raw=stream.getvalue()).status_code == 400


def test_duplicate_paths_rejected():
    assert upload({'main.py': FASTAPI, 'MAIN.py': FLASK}).status_code == 400


def test_file_directory_collision_rejected():
    assert upload({'main.py': FASTAPI, 'a': 'file', 'a/b.py': FLASK}).status_code == 400


def test_corrupt_crc_rejected():
    data = bytearray(zipped({'main.py': FASTAPI}))
    central = data.index(b'PK\x01\x02')
    data[central + 16] ^= 0xff
    assert upload(raw=bytes(data)).status_code == 400


def test_encrypted_flag_rejected():
    data = bytearray(zipped({'main.py': FASTAPI}))
    central = data.index(b'PK\x01\x02')
    struct.pack_into('<H', data, central + 8, 1)
    assert upload(raw=bytes(data)).status_code == 400


def test_file_count_limit():
    assert upload({f'{index}.py': '' for index in range(1001)}).status_code == 413


def test_exact_file_count_limit_is_accepted():
    files = {f'.git/{index}': '' for index in range(999)}
    response = upload({'main.py': FASTAPI, **files})
    assert response.status_code == 200
    assert response.json()['files_scanned'] == 1


def test_extracted_size_limit_including_ignored_files():
    assert upload({'main.py': FASTAPI, '.venv/large.bin': b'0' * (50 * 1024 * 1024)}).status_code == 413


def test_exact_extracted_size_limit_is_accepted():
    padding = 50 * 1024 * 1024 - len(FASTAPI.encode())
    assert upload({'main.py': FASTAPI, '.venv/large.bin': b'0' * padding}).status_code == 200


def test_actual_streamed_size_limit(monkeypatch):
    # Lower the limit after header validation to exercise the streamed-byte check.
    original_open = zipfile.ZipFile.open

    def changed_limit(self, *args, **kwargs):
        monkeypatch.setattr(archive, 'MAX_EXTRACTED_BYTES', 1)
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, 'open', changed_limit)
    payload = zipped({'main.py': FASTAPI})
    monkeypatch.setattr(archive, 'MAX_EXTRACTED_BYTES', 50 * 1024 * 1024)
    assert upload(raw=payload).status_code == 413


def test_upload_limit():
    assert upload(raw=b'0' * (10 * 1024 * 1024 + 1)).status_code == 413


def test_exact_upload_limit_is_accepted():
    def stored_archive(padding):
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_STORED) as archive_file:
            archive_file.writestr('main.py', FASTAPI)
            archive_file.writestr('.venv/padding', padding)
        return output.getvalue()

    overhead = len(stored_archive(b''))
    data = stored_archive(b'0' * (10 * 1024 * 1024 - overhead))
    assert len(data) == 10 * 1024 * 1024
    assert upload(raw=data).status_code == 200


def test_complete_request_limit():
    assert upload(raw=b'0' * (11 * 1024 * 1024)).status_code == 413


def test_chunked_request_body_limit():
    def chunks():
        for _ in range(11):
            yield b'0' * (1024 * 1024)
    response = client.post('/api/analyze', content=chunks(), headers={
        'Content-Type': 'multipart/form-data; boundary=example',
    })
    assert response.status_code == 413


def test_only_zip_filename_accepted():
    assert upload({'main.py': FASTAPI}, filename='project.tar').status_code == 415


def test_missing_or_extra_file_rejected():
    assert client.post('/api/analyze', files={'other': ('app.zip', zipped({'main.py': FASTAPI}))}).status_code == 422
    response = client.post('/api/analyze', files=[('file', ('a.zip', b'a')), ('file', ('b.zip', b'b'))])
    assert response.status_code == 400


def test_non_multipart_rejected():
    assert client.post('/api/analyze', json={}).status_code == 415


@pytest.mark.parametrize('failure', [False, True])
def test_temporary_cleanup(tmp_path, monkeypatch, failure):
    created = []

    def tracked_tempdir(**kwargs):
        directory = tempfile.TemporaryDirectory(dir=tmp_path, **kwargs)
        created.append(Path(directory.name))
        return directory

    monkeypatch.setattr(api, 'TemporaryDirectory', tracked_tempdir)
    response = upload({'main.py': FASTAPI, **({'../unsafe.py': ''} if failure else {})})
    assert response.status_code == (400 if failure else 200)
    assert created and all(not directory.exists() for directory in created)
    assert not list(tmp_path.iterdir())


def test_temporary_cleanup_on_unexpected_scanner_failure(tmp_path, monkeypatch):
    created = []

    def tracked_tempdir(**kwargs):
        directory = tempfile.TemporaryDirectory(dir=tmp_path, **kwargs)
        created.append(Path(directory.name))
        return directory

    def fail(*args):
        raise RuntimeError('scanner failed')

    monkeypatch.setattr(api, 'TemporaryDirectory', tracked_tempdir)
    monkeypatch.setattr(api, 'scan_project', fail)
    with pytest.raises(RuntimeError, match='scanner failed'):
        upload({'main.py': FASTAPI})
    assert created and all(not path.exists() for path in created)


def test_syntax_error_and_oversized_source_produce_warnings():
    report = upload({'bad.py': 'def broken(', 'large.py': '#' * (1024 * 1024 + 1)}).json()
    assert report['framework'] == 'Unknown'
    assert len(report['analysis_warnings']) >= 2


def test_non_utf8_source_is_not_echoed():
    response = upload({'main.py': b'\xff\xfeprivate-source'})
    assert response.status_code == 200
    assert 'private-source' not in response.text
    assert any('UTF-8' in warning for warning in response.json()['analysis_warnings'])


def test_invalid_runtime_metadata_is_a_warning():
    report = upload({'main.py': FASTAPI, 'pyproject.toml': '[invalid', 'setup.cfg': '[invalid'}).json()
    assert report['python_version'] is None
    assert any('pyproject.toml' in warning for warning in report['analysis_warnings'])
    assert any('setup.cfg' in warning for warning in report['analysis_warnings'])


def test_applications_are_not_executed(tmp_path):
    marker = tmp_path / 'must-not-exist'
    source = FASTAPI + f'\nfrom pathlib import Path\nPath({str(marker)!r}).write_text("executed")'
    assert upload({'main.py': source}).status_code == 200
    assert not marker.exists()


@pytest.mark.parametrize(('directory', 'framework', 'entry', 'port'), [
    ('flask-app', 'Flask', 'app:app', 5000), ('fastapi-app', 'FastAPI', 'main:app', 8001),
])
def test_trusted_samples(directory, framework, entry, port):
    root = Path(__file__).resolve().parents[2] / 'examples' / directory
    files = {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob('*') if path.is_file()}
    report = upload(files).json()
    assert report['framework'] == framework
    assert report['entry_point'] == entry
    assert report['detected_ports'] == [port]


def test_cors_allows_analysis_preflight():
    response = client.options('/api/analyze', headers={
        'Origin': 'http://127.0.0.1:5173', 'Access-Control-Request-Method': 'POST',
    })
    assert response.status_code == 200
    assert response.headers['access-control-allow-origin'] == 'http://127.0.0.1:5173'
