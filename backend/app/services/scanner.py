"""Conservative static inspection: never import, execute, or install project code."""

import ast
import configparser
import re
import tomllib
from pathlib import Path

from fastapi import HTTPException
from packaging.requirements import InvalidRequirement, Requirement

from app.models.analysis import AnalysisReport, Dependency, ExcludedApplication
from app.services.scope import select_scope, source_scope, unwrap_root

ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DEPLOY_NAMES = {
    "Dockerfile", ".dockerignore", "Procfile", "docker-compose.yml", "docker-compose.yaml",
    "compose.yml", "compose.yaml", "render.yaml", "render.yml", "fly.toml",
    "app.yaml", "app.yml", "vercel.json", "serverless.yml", "serverless.yaml",
    "runtime.txt", "railway.json", "railway.toml", "netlify.toml",
}


def dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{dotted(node.value)}.{node.attr}"
    return ""


def literal(node: ast.AST):
    return node.value if isinstance(node, ast.Constant) else None


def read_text(path: Path, warnings: set[str]) -> str:
    if path.stat().st_size > 1024 * 1024:
        warnings.add("Text files larger than 1 MB were not parsed; analysis may be incomplete.")
        return ""
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeError:
        warnings.add("Some text files were not UTF-8 and could not be inspected.")
        return ""


def source_aliases(tree: ast.Module, *, module_only: bool = True) -> dict[str, str]:
    aliases = {}
    for node in tree.body if module_only else ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                aliases[item.asname or item.name.split(".")[0]] = item.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for item in node.names:
                aliases[item.asname or item.name] = f"{node.module}.{item.name}"
    return aliases


def resolve_name(node: ast.AST, aliases: dict[str, str]) -> str:
    head, _, tail = dotted(node).partition(".")
    return aliases.get(head, head) + (f".{tail}" if tail else "")


def application_names(tree: ast.Module, aliases: dict[str, str]) -> list[str]:
    names = []
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or not isinstance(node.value, ast.Call):
            continue
        if resolve_name(node.value.func, aliases) not in {"flask.Flask", "fastapi.FastAPI"}:
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names.extend(target.id for target in targets if isinstance(target, ast.Name))
    return sorted(set(names))


def project_metadata(path: Path, warnings: set[str]) -> dict:
    try:
        config = tomllib.loads(read_text(path, warnings))
        project = config.get("project", {})
        if isinstance(project, dict):
            return project
    except tomllib.TOMLDecodeError:
        pass
    warnings.add("pyproject.toml could not be parsed.")
    return {}


def scan_project(root: Path, files: list[Path], fallback_name: str) -> AnalysisReport:
    root = unwrap_root(root, files)
    repository_root = root

    warnings: set[str] = set()
    py_files = [path for path in files if path.suffix == ".py"]
    if not py_files:
        raise HTTPException(422, "No supported Python project found: the archive must contain Python source files.")

    trees = {}
    objects = {}
    for path in py_files:
        try:
            tree = ast.parse(read_text(path, warnings))
            trees[path] = tree
            objects[path] = application_names(tree, source_aliases(tree))
        except (SyntaxError, ValueError, RecursionError):
            warnings.add("Some Python files could not be parsed; analysis may be incomplete.")
    selected, root_candidates, scoped_files = select_scope(root, files)
    excluded = [ExcludedApplication(path=path.relative_to(root).as_posix(), object_name=name,
                                    scope=source_scope(path, root))
                for path in sorted(objects) if source_scope(path, root) != "primary"
                for name in objects[path]]
    if any(source_scope(path, root) != "primary" for path in py_files):
        warnings.add("Tests, documentation, examples, benchmarks, and development tooling are excluded from primary runtime requirements.")
    if selected is None:
        warnings.add("Multiple project roots found; production dependencies and runtime requirements were not combined. Upload a specific application directory instead.")
    root = selected or root
    packaging_present = any(path.parent == root and path.name in {"setup.cfg", "setup.py"}
                            for path in files)
    files = scoped_files
    py_files = [path for path in files if path.suffix == ".py"]
    metadata = {path: project_metadata(path, warnings) for path in files if path.name == "pyproject.toml"}
    root_metadata = metadata.get(root / "pyproject.toml", {})
    dependencies: set[tuple[str, str | None]] = set()
    req_files = [path for path in files if path.parent == root and path.name == "requirements.txt"]

    def add_dependency(line: str):
        try:
            requirement = Requirement(line)
            if requirement.url:
                warnings.add("Direct URL dependencies have no reported URL or version.")
            dependencies.add((requirement.name, str(requirement.specifier) or None))
        except InvalidRequirement:
            warnings.add("Some requirements directives or lines were not parsed; no dependencies were installed.")

    # Root requirements.txt takes precedence over project.dependencies; never merge subprojects.
    if req_files:
        for line in read_text(req_files[0], warnings).splitlines():
            line = re.split(r"\s+#", line.strip(), maxsplit=1)[0]
            if not line or line.startswith("#"):
                continue
            add_dependency(line)
    elif "dependencies" in root_metadata:
        declared = root_metadata["dependencies"]
        if isinstance(declared, list) and all(isinstance(item, str) for item in declared):
            for line in declared:
                add_dependency(line)
        else:
            warnings.add("pyproject.toml project.dependencies must be a list of requirement strings.")
    dependency_files = ["requirements.txt"] if req_files else (
        ["pyproject.toml"] if "dependencies" in root_metadata else [])
    if not dependency_files:
        warnings.add("No requirements.txt or static pyproject.toml project.dependencies found at the selected root.")
    dynamic = root_metadata.get("dynamic", [])
    if isinstance(dynamic, list) and "dependencies" in dynamic:
        warnings.add("Dynamic project dependencies cannot be determined without executing build tooling.")

    frameworks: set[str] = set()
    candidates: set[str] = set()
    env: set[str] = set()
    ports: set[int] = set()
    hosts: set[str] = set()
    # With ambiguous roots retain primary candidate locations, but not combined runtime metadata.
    if selected is None:
        for path, names in objects.items():
            if source_scope(path, repository_root) == "primary":
                module = path.relative_to(repository_root).with_suffix("").as_posix().replace("/", ".")
                candidates.update(f"{module}:{name}" for name in names)
    for path in py_files:
        if path not in trees:
            continue
        tree = trees[path]
        aliases = source_aliases(tree, module_only=False)

        def resolve(node):
            return resolve_name(node, aliases)

        for value in aliases.values():
            if value.split(".")[0] in {"flask", "fastapi"}:
                frameworks.add("Flask" if value.split(".")[0] == "flask" else "FastAPI")

        app_names: set[str] = set()
        # Only module-level app assignments are importable startup candidates.
        for name in objects[path]:
            module = path.relative_to(root).with_suffix("").parts
            if module[-1] == "__init__":
                module = module[:-1]
            if module and all(part.isidentifier() for part in module):
                candidates.add(f"{'.'.join(module)}:{name}")
                app_names.add(name)

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = resolve(node.func)
                if name in {"os.getenv", "os.environ.get", "os.environ.setdefault"} and node.args:
                    value = literal(node.args[0])
                    if isinstance(value, str) and ENV_NAME.fullmatch(value):
                        env.add(value)
                # Only recognizable server startup calls; arbitrary host/password values stay private.
                if name in {"uvicorn.run", "hypercorn.asyncio.serve"} or (
                    isinstance(node.func, ast.Attribute) and node.func.attr == "run"
                    and dotted(node.func.value) in app_names
                ):
                    for keyword in node.keywords:
                        value = literal(keyword.value)
                        if keyword.arg == "port" and type(value) is int and 1 <= value <= 65535:
                            ports.add(value)
                        if keyword.arg == "host" and isinstance(value, str) and (
                            value in {"localhost", "0.0.0.0", "127.0.0.1", "::", "::1"}
                            or re.fullmatch(r"[a-zA-Z0-9.-]+", value)
                        ):
                            hosts.add(value)
            elif isinstance(node, ast.Subscript) and resolve(node.value) == "os.environ":
                value = literal(node.slice)
                if isinstance(value, str) and ENV_NAME.fullmatch(value):
                    env.add(value)

    versions: set[str] = set()
    for path in files:
        if path.name == ".env.example":
            for line in read_text(path, warnings).splitlines():
                match = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
                if match:
                    env.add(match[1])
        if path.name in {".python-version", "runtime.txt"}:
            value = read_text(path, warnings).strip().removeprefix("python-")
            if re.fullmatch(r"3\.\d+(?:\.\d+)?", value):
                versions.add(value)
        elif path.name == "setup.cfg":
            parser = configparser.ConfigParser(interpolation=None)
            try:
                parser.read_string(read_text(path, warnings))
                value = parser.get("options", "python_requires", fallback="")
                if re.fullmatch(r"[0-9.,<>=!~*\s]+", value) and value:
                    versions.add(value.strip())
            except configparser.Error:
                warnings.add("setup.cfg could not be parsed.")
        elif path.name == "pyproject.toml":
            value = metadata[path].get("requires-python")
            if isinstance(value, str) and re.fullmatch(r"[0-9.,<>=!~*\s]+", value):
                versions.add(value)

    package_present = any(path.name == "__init__.py" for path in py_files)
    project_name = root_metadata.get("name")
    framework_source = isinstance(project_name, str) and project_name.lower() in {"flask", "fastapi"} and any(
        path.relative_to(root).as_posix() in {
            f"src/{str(project_name).lower()}/__init__.py", f"{str(project_name).lower()}/__init__.py"
        } for path in py_files)
    if framework_source:
        frameworks.add("Flask" if str(project_name).lower() == "flask" else "FastAPI")
    primary_objects = any(objects.get(path) for path in py_files)
    if selected is None or len(candidates) > 1:
        project_type = "ambiguous"
    elif primary_objects:
        project_type = "web_application"
    elif framework_source:
        project_type = "framework_repository"
    elif package_present or isinstance(project_name, str) or packaging_present:
        project_type = "python_library"
    else:
        project_type = "unsupported"
    if project_type in {"framework_repository", "python_library", "unsupported"}:
        warnings.add("No clear deployable web application was found. Upload a specific application directory instead; repository classification does not establish cloud readiness.")

    if len(versions) > 1:
        warnings.add("Multiple Python version declarations found; no single version selected.")
    if len(frameworks) != 1:
        warnings.add("Framework is unknown or multiple frameworks were detected.")
    if len(candidates) > 1:
        warnings.add("Multiple entry points found; select a startup target manually.")
    elif not candidates:
        warnings.add("No unambiguous module-level Flask or FastAPI application object found.")
    if not frameworks:
        warnings.add("Unsupported or unknown Python application; only Flask and FastAPI are recognized.")
    names = {path.name for path in files}
    deployment = sorted(path.relative_to(root).as_posix() for path in files if (
        path.name in DEPLOY_NAMES or path.name.startswith("Dockerfile.")
        or (".github/workflows/" in path.relative_to(root).as_posix() and path.suffix in {".yml", ".yaml"})
        or (any(part in {"k8s", "kubernetes"} for part in path.relative_to(root).parts)
            and path.suffix in {".yml", ".yaml"})
    ))
    return AnalysisReport(
        project_name=root.name if root.name != "project" else fallback_name,
        project_type=project_type,
        analysis_root=selected.relative_to(repository_root).as_posix() if selected else None,
        project_root_candidates=[path.relative_to(repository_root).as_posix() for path in root_candidates],
        dependency_files=dependency_files, excluded_application_objects=excluded,
        framework=next(iter(frameworks)) if len(frameworks) == 1 else "Unknown",
        python_version=next(iter(versions)) if len(versions) == 1 else None,
        dependencies=[Dependency(name=name, version=version) for name, version in sorted(dependencies, key=lambda item: (item[0].lower(), item[0], item[1] or ""))],
        dependency_file_present=bool(dependency_files),
        entry_point=next(iter(candidates)) if len(candidates) == 1 and selected else None,
        entry_point_candidates=sorted(candidates), environment_variables=sorted(env),
        detected_ports=sorted(ports), detected_hosts=sorted(hosts),
        dockerfile_present=any(name == "Dockerfile" or name.startswith("Dockerfile.") for name in names),
        dockerignore_present=".dockerignore" in names, env_example_present=".env.example" in names,
        deployment_files=deployment, files_scanned=len(files), analysis_warnings=sorted(warnings),
    )
