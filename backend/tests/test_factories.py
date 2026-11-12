"""Static factory detection regressions; no uploaded application is executed."""

from pathlib import Path

import pytest

from test_analysis import FASTAPI, FLASK, upload


def test_flask_tutorial_package_factory_fixture():
    root = Path(__file__).parent / 'fixtures' / 'flask-tutorial'
    response = upload({f'tutorial/{path.relative_to(root).as_posix()}': path.read_bytes()
                       for path in root.rglob('*') if path.is_file()})
    assert response.status_code == 200
    report = response.json()
    assert report['project_type'] == 'web_application'
    assert report['framework'] == 'Flask'
    assert report['entry_point'] == 'flaskr:create_app'
    assert report['entry_point_type'] == 'application_factory'
    assert report['entry_point_candidate_types'] == {'flaskr:create_app': 'application_factory'}
    assert report['dependencies'] == [{'name': 'Flask', 'version': '<4,>=3.1'}]
    assert report['environment_variables'] == ['SECRET_KEY']
    assert 'fixture-only-placeholder' not in response.text


@pytest.mark.parametrize(('source', 'framework'), [
    ('from flask import Flask\ndef create_app():\n    app = Flask(__name__)\n    return app', 'Flask'),
    ('import fastapi as web\ndef create_app():\n    app: web.FastAPI = web.FastAPI()\n    return app', 'FastAPI'),
    ('from fastapi import FastAPI\ndef build_app():\n    return FastAPI()', 'FastAPI'),
    ('def create_app():\n    from flask import Flask as Web\n    return Web(__name__)', 'Flask'),
])
def test_simple_factory_forms(source, framework):
    report = upload({'main.py': source, 'requirements.txt': framework}).json()
    function = 'build_app' if 'build_app' in source else 'create_app'
    assert report['project_type'] == 'web_application'
    assert report['framework'] == framework
    assert report['entry_point'] == f'main:{function}'
    assert report['entry_point_type'] == 'application_factory'


@pytest.mark.parametrize('source', [
    'def create_app():\n    app = Flask(__name__)\n    return None',
    'def create_app():\n    app = Flask(__name__)',
    'def create_app():\n    app = Flask(__name__)\n    app = None\n    return app',
    'def create_app():\n    app = Flask(__name__)\n    if True:\n        app = None\n    return app',
    'def create_app(required_config):\n    return Flask(__name__)',
    'def create_app(*, required_config):\n    return Flask(__name__)',
    'async def create_app():\n    return Flask(__name__)',
    '@decorator\ndef create_app():\n    return Flask(__name__)',
    'def create_app():\n    def nested():\n        return Flask(__name__)\n    return None',
    'def create_app():\n    yield 1\n    return Flask(__name__)',
    'def create_app():\n    item = (yield 1)\n    return Flask(__name__)',
    'def create_app():\n    raise RuntimeError("never returns")\n    return Flask(__name__)',
    'def create_app(Flask=None):\n    return Flask(__name__)',
    'def create_app():\n    Flask = None\n    return Flask(__name__)',
])
def test_unsupported_or_unreturned_factories_are_not_guessed(source):
    report = upload({'main.py': f'from flask import Flask\n{source}'}).json()
    assert report['entry_point_candidates'] == []
    assert report['entry_point_type'] is None
    assert report['entry_point_candidate_types'] == {}
    assert report['project_type'] != 'web_application'


def test_module_objects_remain_distinct_from_factories():
    report = upload({'main.py': FASTAPI}).json()
    assert report['entry_point'] == 'main:app'
    assert report['entry_point_type'] == 'application_object'
    assert report['entry_point_candidate_types'] == {'main:app': 'application_object'}


def test_package_application_object_keeps_its_package_target():
    report = upload({'flaskr/__init__.py': FLASK}).json()
    assert report['entry_point'] == 'flaskr:app'
    assert report['entry_point_type'] == 'application_object'


@pytest.mark.parametrize(('filename', 'package'), [('flaskr/__init__.py', 'flaskr'), ('service/api/__init__.py', 'api')])
def test_package_name_survives_when_the_only_upload_is_a_package(filename, package):
    report = upload({filename: 'from flask import Flask\ndef create_app():\n    return Flask(__name__)'}).json()
    assert report['entry_point'] == f'{package}:create_app'
    assert report['entry_point_type'] == 'application_factory'


def test_factory_and_object_targets_remain_ambiguous():
    report = upload({'main.py': FLASK + '\ndef create_app():\n    return Flask(__name__)'}).json()
    assert report['project_type'] == 'ambiguous'
    assert report['entry_point'] is None
    assert report['entry_point_type'] is None
    assert report['entry_point_candidates'] == ['main:app', 'main:create_app']
    assert report['entry_point_candidate_types'] == {
        'main:app': 'application_object', 'main:create_app': 'application_factory',
    }


def test_multiple_factories_remain_ambiguous():
    source = 'from fastapi import FastAPI\ndef create_app():\n    return FastAPI()\ndef other_app():\n    return FastAPI()'
    report = upload({'main.py': source}).json()
    assert report['project_type'] == 'ambiguous'
    assert report['entry_point'] is None
    assert report['entry_point_type'] is None
    assert report['entry_point_candidates'] == ['main:create_app', 'main:other_app']


def test_test_and_example_factories_do_not_enter_production_scope():
    factory = 'from flask import Flask\nimport os\ndef create_app():\n    x = os.getenv("TEST_ONLY")\n    return Flask(__name__)'
    report = upload({'main.py': FASTAPI, 'tests/factory.py': factory, 'examples/factory.py': factory}).json()
    assert report['project_type'] == 'web_application'
    assert report['framework'] == 'FastAPI'
    assert report['entry_point_candidates'] == ['main:app']
    assert report['entry_point_type'] == 'application_object'
    assert report['environment_variables'] == []


def test_multiple_factory_roots_keep_dependencies_separate():
    factory = 'from flask import Flask\ndef create_app():\n    return Flask(__name__)'
    report = upload({
        'one/flaskr/__init__.py': factory, 'one/requirements.txt': 'Flask==3.1.2',
        'two/flaskr/__init__.py': factory, 'two/requirements.txt': 'Flask>=3',
    }).json()
    assert report['analysis_root'] is None
    assert report['project_type'] == 'ambiguous'
    assert report['entry_point'] is None
    assert report['entry_point_type'] is None
    assert report['dependencies'] == []
    assert report['entry_point_candidates'] == ['one.flaskr:create_app', 'two.flaskr:create_app']
    assert set(report['entry_point_candidate_types'].values()) == {'application_factory'}
