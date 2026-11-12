"""Deterministic review templates. No uploaded text or source is copied to downloads."""

import ast
import base64
import io
import json
import os
import re
import shlex
import zipfile
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet

from app.models.generation import GeneratedFile, GenerationReport, SkippedFile
from app.services.readiness import collect_evidence
from app.services.scanner import application_names, read_text, source_aliases

SUPPORTED_PYTHON = ("3.11", "3.12", "3.13", "3.14")
TARGET = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*$", re.ASCII)
IGNORE = '''.git
.venv
venv
**/__pycache__
**/*.py[cod]
.pytest_cache
.mypy_cache
.ruff_cache
.env
.env.*
**/.env
**/.env.*
!.env.example
!**/.env.example
# Add development/private database exclusions after checking required runtime assets:
# **/*.db
# **/*.sqlite
# **/*.sqlite3
node_modules
# Preserve application assets by default; review before excluding build directories:
# dist
# build
.coverage
htmlcov
'''


def choose_python(declared, warnings):
    default = os.getenv("ASCEND_DEFAULT_PYTHON_VERSION", "3.13")
    if default not in SUPPORTED_PYTHON:
        warnings.append("ASCEND_DEFAULT_PYTHON_VERSION is unsupported; configure a supported Python minor version.")
        return None
    if not declared:
        warnings.append(f"Python version unknown: using configurable default {default}; verify compatibility before building.")
        return default
    if re.fullmatch(r"3\.\d+(?:\.\d+)?", declared):
        if '.'.join(declared.split('.')[:2]) in SUPPORTED_PYTHON:
            return declared
    else:
        try:
            constraint = SpecifierSet(declared)
            for candidate in [default, *reversed(SUPPORTED_PYTHON)]:
                if constraint.contains(candidate + ".0"):
                    warnings.append(f"Selected Python {candidate} from the declared constraint; review the image patch version.")
                    return candidate
        except InvalidSpecifier:
            pass
    warnings.append("The declared Python version cannot be mapped reliably to a supported official image; executable templates were withheld.")
    return None


def safe_dependencies(report, root, warnings):
    if not report.dependencies or not report.dependency_file_present:
        warnings.append("Runtime dependencies are missing or unparsed; executable templates were withheld until installation can be specified reliably.")
        return None
    if report.framework.lower() not in {item.name.lower().replace('_', '-') for item in report.dependencies}:
        warnings.append("The detected framework is not explicitly declared as a runtime dependency; review transitive requirements or declare it before generating executable templates.")
        return None
    if any(any(word in warning for word in ("not parsed", "could not be parsed", "Dynamic project dependencies", "must be a list", "Direct URL", "larger than", "not UTF-8")) for warning in report.analysis_warnings):
        warnings.append("Dependency or source analysis is incomplete; executable templates were withheld. Resolve scanner warnings first.")
        return None
    if report.dependency_files == ["requirements.txt"]:
        for line in read_text(root / 'requirements.txt', set()).splitlines():
            line = re.split(r"\s+#", line.strip(), maxsplit=1)[0]
            if not line or line.startswith('#'):
                continue
            try:
                if Requirement(line).url:
                    raise InvalidRequirement('direct URL')
            except InvalidRequirement:
                warnings.append("Requirements contain unsupported directives or direct sources; executable templates were withheld.")
                return None
        return "-r requirements.txt"
    if report.dependency_files == ["pyproject.toml"]:
        warnings.append("pyproject.toml installation uses pip install .; package discovery, build-system dependencies, and non-Python requirements need manual validation.")
        return "."
    warnings.append("Dependency installation format is unsupported; executable templates were withheld.")
    return None


def smoke_path(trees):
    paths = set()
    for tree in trees.values():
        aliases = source_aliases(tree, module_only=False)
        apps = set(application_names(tree, source_aliases(tree)))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, ast.Call):
                from app.services.scanner import resolve_name
                if resolve_name(node.value.func, aliases) in {'flask.Flask', 'fastapi.FastAPI'}:
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    apps.update(item.id for item in targets if isinstance(item, ast.Name))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute) and isinstance(decorator.func.value, ast.Name) and decorator.func.value.id in apps and decorator.func.attr == 'get' and decorator.args:
                        value = decorator.args[0]
                        if isinstance(value, ast.Constant) and isinstance(value.value, str) and re.fullmatch(r'/[A-Za-z0-9/_-]{0,100}', value.value):
                            paths.add(value.value)
    return next((path for path in ['/health', '/healthz', '/api/health', '/'] if path in paths), next(iter(paths)) if len(paths) == 1 else None)


def workflow(path, application_root='.'):
    return '''name: Migration container smoke test
on: [push, pull_request]
permissions:
  contents: read
jobs:
  smoke:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    env:
      SMOKE_TEST_PATH: "''' + path + '''"
      APP_DIR: ''' + json.dumps(application_root) + '''
    steps:
      - uses: actions/checkout@v6
        with:
          persist-credentials: false
      - name: Build and smoke test
        working-directory: ${{ env.APP_DIR }}
        shell: bash
        run: |
          set -euo pipefail
          trap 'docker logs ascend-smoke 2>/dev/null || true; docker rm -f ascend-smoke 2>/dev/null || true' EXIT
          docker build -t ascend-smoke .
          # Add required application environment configuration before enabling this job.
          docker run -d --name ascend-smoke -e PORT=10000 -p 127.0.0.1:10000:10000 ascend-smoke
          curl --fail --retry 20 --retry-all-errors --retry-delay 3 --max-time 5 "http://127.0.0.1:10000${SMOKE_TEST_PATH}"
'''


def generate(report, root: Path, files: list[Path], existing_paths=None, repository_root=None):
    eligible = report.project_type == 'web_application' and report.analysis_root is not None and bool(re.fullmatch(r'[A-Za-z0-9_./-]+', report.analysis_root)) and report.framework in {'Flask', 'FastAPI'} and bool(report.entry_point and TARGET.fullmatch(report.entry_point))
    result = GenerationReport(eligible=eligible, status='manual_review' if eligible else 'ineligible',
                              analysis_root=report.analysis_root, unresolved_findings=report.findings)
    if not eligible:
        result.generation_warnings.append("Select one supported web application with an unambiguous importable startup target and a simple safe application-root path before generation.")
        return result
    existing = {path.relative_to(root).as_posix(): path for path in (existing_paths if existing_paths is not None else files) if path.is_relative_to(root)}
    if repository_root is not None:
        for path in existing_paths or []:
            name = path.relative_to(repository_root).as_posix()
            if name in {'render.yaml', 'render.yml'} or name.startswith('.github/workflows/'):
                existing[name] = path
    result.existing_files = sorted(name for name in existing if Path(name).name.startswith('Dockerfile') or name in {'.dockerignore', '.env.example', 'render.yaml', 'render.yml', 'MIGRATION.md'} or name.startswith('.github/workflows/') or name in report.deployment_files)
    warnings = result.generation_warnings
    warnings.append("Generated files require manual review. No build, startup, or cloud deployment has been validated.")
    version = choose_python(report.python_version, warnings)
    installation = safe_dependencies(report, root, warnings)
    trees = {}
    for path in files:
        if path.suffix == '.py':
            try:
                trees[path] = ast.parse(read_text(path, set()))
            except (SyntaxError, ValueError, RecursionError):
                pass
    evidence = collect_evidence(root, files, trees)
    specialized = {'playwright', 'selenium', 'pyppeteer', 'opencv-python', 'torch', 'tensorflow', 'pytesseract', 'psycopg2', 'mysqlclient', 'weasyprint', 'cairocffi'}
    imports = {item.name.split('.')[0] for tree in trees.values() for node in ast.walk(tree)
               if isinstance(node, ast.Import) for item in node.names}
    imports.update(node.module.split('.')[0] for tree in trees.values() for node in ast.walk(tree)
                   if isinstance(node, ast.ImportFrom) and node.module)
    if any(item.name.lower().replace('_', '-') in specialized for item in report.dependencies) or imports & {'playwright', 'selenium', 'pyppeteer', 'cv2', 'torch', 'tensorflow', 'pytesseract', 'weasyprint'}:
        warnings.append("Specialized dependencies detected: browser binaries, OS packages, native libraries, or model assets may be required. The generic image does not install these; review runtime requirements manually.")
    if any(issue.category == 'storage' for issue in report.findings) or any(path.suffix in {'.db', '.sqlite', '.sqlite3'} for path in files):
        warnings.append("Review persistent/local storage and database files before building. Add explicit exclusions for development/private data; database and build-asset exclusions are commented suggestions so required runtime assets are preserved. Free Render instances do not provide persistent disks.")
    detected_path = smoke_path(trees)
    health = detected_path or '/'
    warnings.append("Review the HTTP smoke-test path and authentication/environment requirements; detected routes are not verified health checks.")
    if not detected_path:
        warnings.append("No reliable simple GET health/root route detected; SMOKE_TEST_PATH defaults to / and must be configured. No Render healthCheckPath is assumed.")

    def add(path, content):
        if path.casefold() in {name.casefold() for name in existing}:
            result.skipped_files.append(SkippedFile(path=path, reason="Existing file preserved; review and reuse it. No replacement generated."))
            return False
        result.generated_files.append(GeneratedFile(path=path, content=content))
        return True

    env_lines = ['# Names only. Never copy real secrets into version control.', '# Leave defaulted variables unset to use application defaults.']
    for name in sorted(set(report.environment_variables)):
        usage = evidence.environment_usage.get(name, 'uncertain; review whether needed')
        env_lines += [f'# {name}: {usage}; requirements are not verified.', f'# {name}=' if usage == 'default_detected' else f'{name}=']
    add('.env.example', '\n'.join(env_lines) + '\n')
    add('.dockerignore', IGNORE)
    can_build = bool(version and installation)
    if any(name.casefold() == 'dockerfile' and name != 'Dockerfile' for name in existing):
        can_build = False
        warnings.append("A case-conflicting Dockerfile name requires manual resolution; executable templates were withheld.")
    if can_build:
        supplemental = 'requirements.ascend.txt'
        index = 2
        while supplemental.casefold() in {name.casefold() for name in existing}:
            supplemental = f'requirements.ascend-{index}.txt'
            index += 1
        server = 'gunicorn' if report.framework == 'Flask' else 'uvicorn'
        if 'Dockerfile' not in existing:
            add(supplemental, f'# Explicit production server. Review and pin a compatible version.\n{server}\n')
            target = report.entry_point
            pythonpath = ''
            if target.startswith('src.') and (root / 'src').is_dir():
                target = target[4:]
                pythonpath = 'ENV PYTHONPATH=/app/src\n'
                warnings.append("src-layout target adjusted for PYTHONPATH=/app/src; verify application import paths.")
            if server == 'gunicorn':
                if report.entry_point_type == 'application_factory':
                    target += '()'
                command = f'exec python -m gunicorn --bind "0.0.0.0:${{PORT:-10000}}" {shlex.quote(target)}'
            else:
                command = f'exec python -m uvicorn {shlex.quote(target)} --host 0.0.0.0 --port "${{PORT:-10000}}"' + (' --factory' if report.entry_point_type == 'application_factory' else '')
            add('Dockerfile', f'''FROM python:{version}-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=10000
RUN useradd --create-home --uid 10001 app && mkdir -p /app && chown app:app /app
WORKDIR /app
{pythonpath}COPY --chown=app:app . .
RUN python -m pip install --no-cache-dir {installation} && python -m pip install --no-cache-dir -r {supplemental}
USER app
EXPOSE 10000
CMD {json.dumps(['sh', '-c', command])}
''')
        else:
            add('Dockerfile', '')
            warnings.append("Existing Dockerfile reused without validation. Review its dependencies, PORT handling, non-root user, and specialized runtime packages; generated CI/Render files do not replace its startup command.")
        if any(name.startswith('.github/workflows/') for name in existing):
            result.skipped_files.append(SkippedFile(path='.github/workflows/ascend-migration.yml', reason='Existing CI workflows preserved; integrate container smoke testing manually.'))
        else:
            add('.github/workflows/ascend-migration.yml', workflow(health, report.analysis_root))
        if any(name in existing for name in ['render.yaml', 'render.yml']):
            result.skipped_files.append(SkippedFile(path='render.yaml', reason='Existing Render configuration preserved; review and reuse it.'))
        else:
            render = '''# Review template; schema fields checked against https://render.com/docs/blueprint-spec
# Nothing is provisioned by Ascend. Review free-tier limits before manual setup.
services:
  - type: web
    name: ascend-app
    runtime: docker
    plan: free
    autoDeployTrigger: "off"
'''
            context = '.' if report.analysis_root == '.' else './' + report.analysis_root
            render += f'    dockerfilePath: {json.dumps(context + "/Dockerfile")}\n    dockerContext: {json.dumps(context)}\n'
            if detected_path:
                render += f'    healthCheckPath: {json.dumps(detected_path)}\n'
            render += '    envVars:\n      - key: PORT\n        value: "10000"\n'
            for name in sorted(evidence.environment):
                if name != 'PORT' and evidence.environment_usage[name] != 'default_detected':
                    render += f'      - key: {name}\n        sync: false\n'
            add('render.yaml', render)
    else:
        for path in ['Dockerfile', '.github/workflows/ascend-migration.yml', 'render.yaml']:
            result.skipped_files.append(SkippedFile(path=path, reason='Executable configuration withheld: unresolved Python/dependency installation.'))
    result.deployment_instructions = [
        f"Apply reviewed files to the selected application root ({report.analysis_root}); build context is that directory, not the complete repository.",
        "Exception: place .github/workflows/ascend-migration.yml and render.yaml at the repository root. Their paths refer to the selected application root; adjust them if extracting the application into a new standalone repository.",
        "Keep existing files unchanged and merge missing configuration manually. Read the generated migration guide before using any template.",
        "For generated Dockerfile: docker build -t ascend-app . ; run with docker run --rm -p 10000:10000 -e PORT=10000 ascend-app after adding required runtime environment configuration.",
        f"Smoke-test http://localhost:10000{health}; configure SMOKE_TEST_PATH and application environment in CI before relying on that workflow.",
        "Review render.yaml manually against Render's current official schema and free-plan limits. Set required variables in the dashboard; leave optional defaulted variables unset. No cloud resources have been created.",
    ]
    guide = f'''# Migration review guide

Framework: {report.framework}
Startup target: `{report.entry_point}` ({report.entry_point_type})
Selected analysis root: {json.dumps(report.analysis_root)}
Python image version: {version or 'UNRESOLVED'}
Executable configuration available: {can_build}

## Setup and validation
''' + '\n'.join(f'{index}. {step}' for index, step in enumerate(result.deployment_instructions, 1))
    guide += '\n\n## Warnings and manual review\n' + '\n'.join('- ' + warning for warning in warnings)
    guide += '\n\n## Scanner limitations\n' + '\n'.join('- ' + warning for warning in report.analysis_warnings)
    guide += '\n\n## Environment names (never values)\n' + '\n'.join(f'- {name}: {evidence.environment_usage.get(name, "uncertain; review")}' for name in sorted(report.environment_variables))
    guide += '\n\n## Existing files preserved\n' + '\n'.join('- ' + json.dumps(name) for name in result.existing_files)
    guide += '\n\n## Unresolved readiness findings\n' + '\n'.join(f'- {issue.rule_id}: {issue.title}. {issue.recommended_fix}' for issue in report.findings)
    guide += '\n\nNo generated files have been built or deployed by Ascend. A generic Python image cannot guarantee runtime compatibility. No databases, disks, credentials, or paid plans are provisioned. Review source before any build; pip/build systems may execute project build code during your manual build.\n'
    guide_name = 'MIGRATION.md'
    index = 1
    while guide_name.casefold() in {name.casefold() for name in existing}:
        guide_name = f'MIGRATION.ascend-{index}.md'
        index += 1
    add(guide_name, guide)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for item in result.generated_files:
            # All names are generator-owned, not supplied by archive members or clients.
            if '..' in Path(item.path).parts or item.path.startswith('/') or '\\' in item.path:
                raise ValueError('Unsafe generated path')
            info = zipfile.ZipInfo(item.path, date_time=(2020, 1, 1, 0, 0, 0))
            archive.writestr(info, item.content)
    result.download_zip_base64 = base64.b64encode(buffer.getvalue()).decode('ascii')
    return result
