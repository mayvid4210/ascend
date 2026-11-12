"""Independent static rules and deterministic, grouped deductions. No code execution."""

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.models.analysis import AnalysisReport
from app.models.readiness import EnvironmentReference, Finding, ReadinessAssessment, SeveritySummary
from app.services.production_startup import ProductionStartup, inspect_startup

Location = tuple[str, int]
SEVERITY_ORDER = {"critical": 0, "warning": 1, "informational": 2}
DEDUCTIONS = {
    ("critical", "confirmed"): 25, ("critical", "potential"): 15,
    ("warning", "confirmed"): 10, ("warning", "potential"): 5,
    ("informational", "confirmed"): 2, ("informational", "potential"): 1,
}
# Closely related signals share the largest deduction, not their sum.
GROUPS = {
    "NET001": "server_binding", "NET002": "server_binding",
    "STO001": "storage", "STO002": "storage",
    "CTR001": "containerization", "CTR002": "containerization",
}
DISCLAIMER = "Static analysis does not guarantee successful deployment; review and test the production configuration."


@dataclass
class Evidence:
    localhost: list[Location] = field(default_factory=list)
    ports: list[Location] = field(default_factory=list)
    absolute_paths: list[Location] = field(default_factory=list)
    storage: list[Location] = field(default_factory=list)
    secrets: list[Location] = field(default_factory=list)
    environment: dict[str, Location] = field(default_factory=dict)
    environment_usage: dict[str, str] = field(default_factory=dict)
    documented_environment: set[str] = field(default_factory=set)
    dynamic_environment: bool = False
    development_startups: set[Location] = field(default_factory=set)
    production: ProductionStartup = field(default_factory=ProductionStartup)


def record_environment(evidence: Evidence, key: str, usage: str, location: Location):
    # A stricter read anywhere prevents a default at another read from hiding that requirement.
    priority = {"required_access": 0, "no_default_detected": 1, "default_detected": 2}
    if key not in evidence.environment_usage or priority[usage] < priority[evidence.environment_usage[key]]:
        evidence.environment_usage[key] = usage
        evidence.environment[key] = location


def filesystem_literals(tree: ast.Module, aliases: dict[str, str]) -> set[int]:
    """Limit path-shaped string inspection to filesystem operations/configuration."""
    from app.services.scanner import dotted, literal, resolve_name

    contexts = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = resolve_name(node.func, aliases)
            if name in {"open", "io.open", "builtins.open", "pathlib.Path", "pathlib.PurePath",
                        "pathlib.PosixPath", "pathlib.WindowsPath", "sqlite3.connect", "aiosqlite.connect",
                        "os.mkdir", "os.makedirs", "os.chdir", "os.listdir", "os.remove", "os.unlink"}:
                contexts.extend(node.args)
                contexts.extend(item.value for item in node.keywords if item.arg in {"file", "path", "filename", "database"})
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                key = literal(target.slice) if isinstance(target, ast.Subscript) else dotted(target)
                if isinstance(key, str) and not re.search(r"(?:url|uri|route|endpoint|api_path)", key, re.I) and re.search(
                    r"(?:^|[_.])(?:file_path|filename|.*_path|.*_dir|.*_folder|directory|database|storage|uploads?)$", key, re.I
                ) and node.value is not None:
                    contexts.append(node.value)
        elif isinstance(node, ast.keyword) and node.arg and re.search(r"(?:directory|filename|file_path|upload_folder|data_dir)$", node.arg, re.I):
            contexts.append(node.value)
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                name = literal(key) if key is not None else None
                if isinstance(name, str) and re.fullmatch(r"(?:FILE_PATH|FILENAME|.*_PATH|.*_DIR|.*_FOLDER|DIRECTORY|DATABASE|STORAGE)", name, re.I) and not re.search(r"url|uri|route|endpoint|api_path", name, re.I):
                    contexts.append(value)
    # Follow only uniquely assigned literal names used in filesystem operations.
    bindings = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bindings.setdefault(target.id, []).append(node.value)
    for context in list(contexts):
        if isinstance(context, ast.Name) and len(bindings.get(context.id, [])) == 1:
            contexts.append(bindings[context.id][0])
    return {id(child) for context in contexts for child in ast.walk(context) if isinstance(child, ast.Constant)}


def sensitive_name(name: str) -> bool:
    return bool(re.search(r"(?:^|_)(?:secret(?:_key)?|password|passwd|api_key|access_token|auth_token|private_key|token)(?:$|_)", name, re.I))


def has_literal_secret(value: ast.AST | None) -> bool:
    if isinstance(value, ast.Constant):
        return isinstance(value.value, str) and bool(value.value.strip())
    # Defaults such as os.getenv('SECRET_KEY', 'dev') can silently reach production.
    if isinstance(value, ast.Call):
        return any(has_literal_secret(item) for item in value.args[1:]) or any(
            item.arg == "default" and has_literal_secret(item.value) for item in value.keywords)
    return False


def local_storage_value(value: ast.AST | None, aliases: dict[str, str]) -> bool:
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        text = value.value.lower()
        return bool(text) and ":memory:" not in text and ("://" not in text or text.startswith("sqlite:"))
    if isinstance(value, ast.Call):
        from app.services.scanner import resolve_name
        # Only literal/default path hints, not arbitrary database client constructors.
        arguments = value.args
        if resolve_name(value.func, aliases) in {"os.getenv", "os.environ.get", "os.environ.setdefault"}:
            arguments = value.args[1:]
        return any(local_storage_value(item, aliases) for item in arguments) or any(
            item.arg == "default" and local_storage_value(item.value, aliases) for item in value.keywords)
    return False


def collect_evidence(root: Path, files: list[Path], trees: dict[Path, ast.Module]) -> Evidence:
    from app.services.scanner import (ENV_NAME, application_names, dotted, literal,
                                      read_text, resolve_name, source_aliases)

    evidence = Evidence()
    for path in sorted(files):
        if path.name == ".env.example":
            for line in read_text(path, set()).splitlines():
                match = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
                if match:
                    evidence.documented_environment.add(match[1])
        if path not in trees:
            continue
        tree = trees[path]
        aliases = source_aliases(tree, module_only=False)
        path_literals = filesystem_literals(tree, aliases)
        main_block_nodes = {id(child) for guard in ast.walk(tree) if isinstance(guard, ast.If)
                            and isinstance(guard.test, ast.Compare) and len(guard.test.ops) == 1
                            and isinstance(guard.test.ops[0], ast.Eq)
                            and ((dotted(guard.test.left) == "__name__" and literal(guard.test.comparators[0]) == "__main__")
                                 or (literal(guard.test.left) == "__main__" and dotted(guard.test.comparators[0]) == "__name__"))
                            for statement in guard.body for child in ast.walk(statement)}
        apps = set(application_names(tree, source_aliases(tree)))
        # Factories may bind an app inside their body; only literal constructor assignments qualify.
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, ast.Call):
                if resolve_name(node.value.func, aliases) in {"flask.Flask", "fastapi.FastAPI"}:
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    apps.update(target.id for target in targets if isinstance(target, ast.Name))
        for node in ast.walk(tree):
            location = (path.relative_to(root).as_posix(), getattr(node, "lineno", 1))
            if isinstance(node, ast.Call):
                name = resolve_name(node.func, aliases)
                if name in {"os.getenv", "os.environ.get", "os.environ.setdefault"}:
                    key = literal(node.args[0]) if node.args else next(
                        (literal(item.value) for item in node.keywords if item.arg == "key"), None)
                    if isinstance(key, str) and ENV_NAME.fullmatch(key):
                        default = node.args[1] if len(node.args) > 1 else next(
                            (item.value for item in node.keywords if item.arg == "default"), None)
                        has_default = default is not None and not (isinstance(default, ast.Constant) and default.value is None)
                        record_environment(evidence, key, "default_detected" if has_default else "no_default_detected", location)
                        if sensitive_name(key) and has_literal_secret(node):
                            evidence.secrets.append(location)
                    else:
                        evidence.dynamic_environment = True
                server = name == "uvicorn.run" or (
                    isinstance(node.func, ast.Attribute) and node.func.attr == "run" and dotted(node.func.value) in apps)
                if server:
                    if id(node) in main_block_nodes:
                        evidence.development_startups.add(location)
                    for item in node.keywords:
                        value = literal(item.value)
                        if item.arg == "host" and value in ("localhost", "127.0.0.1", "::1"):
                            evidence.localhost.append(location)
                        if item.arg == "port" and type(value) is int and 1 <= value <= 65535:
                            evidence.ports.append(location)
                if name in {"sqlite3.connect", "aiosqlite.connect"}:
                    if not node.args or literal(node.args[0]) != ":memory:":
                        evidence.storage.append(location)
                if name in {"open", "io.open"} or isinstance(node.func, ast.Attribute) and node.func.attr == "open":
                    mode_index = 1 if name in {"open", "io.open"} else 0
                    mode = literal(node.args[mode_index]) if len(node.args) > mode_index else next(
                        (literal(item.value) for item in node.keywords if item.arg == "mode"), None)
                    if isinstance(mode, str) and any(flag in mode for flag in "wax+"):
                        evidence.storage.append(location)
                if isinstance(node.func, ast.Attribute) and node.func.attr in {"write_text", "write_bytes", "mkdir", "makedirs"}:
                    evidence.storage.append(location)
                for item in node.keywords:
                    if item.arg and sensitive_name(item.arg) and has_literal_secret(item.value):
                        evidence.secrets.append(location)
            elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load) and resolve_name(node.value, aliases) == "os.environ":
                key = literal(node.slice)
                if isinstance(key, str) and ENV_NAME.fullmatch(key):
                    record_environment(evidence, key, "required_access", location)
                else:
                    evidence.dynamic_environment = True
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    key = dotted(target)
                    if isinstance(target, ast.Subscript):
                        key = literal(target.slice)
                    if isinstance(key, str) and sensitive_name(key.replace(".", "_")) and has_literal_secret(node.value):
                        evidence.secrets.append(location)
                    if isinstance(key, str) and re.search(r"(?:storage|upload|database|data_dir|data_path)", key, re.I):
                        if local_storage_value(node.value, aliases):
                            evidence.storage.append(location)
            elif isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if key is not None and isinstance(literal(key), str) and sensitive_name(literal(key)) and has_literal_secret(value):
                        evidence.secrets.append(location)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                value = node.value
                if value.startswith("sqlite:") and ":memory:" not in value:
                    evidence.storage.append(location)
                # Inspect path-shaped literals, but never include the literal in the report.
                path_value = value.removeprefix("sqlite:///") if value.startswith("sqlite:///") else value
                if (path_value.startswith("/") and len(path_value) > 1 or re.match(r"^[A-Za-z]:[\\/]", path_value)
                        or value.startswith("\\\\")):
                    if id(node) in path_literals:
                        evidence.absolute_paths.append(location)
    return evidence


def finding(rule_id, title, severity, confidence, category, explanation, fix, location=None):
    return Finding(rule_id=rule_id, title=title, severity=severity, confidence=confidence,
                   category=category, explanation=explanation, recommended_fix=fix,
                   file_path=location[0] if location else None, line_number=location[1] if location else None)


def dependency_rule(report, evidence):
    if not report.dependency_file_present:
        return finding("DEP001", "Missing dependency declarations", "warning", "confirmed", "dependencies",
            "No supported dependency declaration was found at the selected application root.",
            "Declare runtime dependencies in requirements.txt or pyproject.toml project.dependencies.")


def startup_rule(report, evidence):
    if not report.entry_point:
        return finding("RUN001", "Startup target is missing or ambiguous", "warning", "potential", "runtime",
            "Static analysis could not select one production startup target; a dynamic target may exist.",
            "Document one importable application object or supported application factory and its startup command.")


def localhost_rule(report, evidence):
    production = evidence.production
    locations = evidence.localhost
    if production.detected and production.host == "wildcard":
        locations = [location for location in locations if location not in evidence.development_startups]
    if production.detected and production.host == "loopback":
        locations = [production.location]
    if locations:
        return finding("NET001", "Loopback-only server binding", "warning", "potential", "networking",
            ("The detected Docker startup command binds to loopback; the configuration has not been validated." if locations[0] == production.location else
             "A Python startup call binds to loopback. A guarded development startup block may be overridden by production, but no clear resolving override was established for this call."),
            "Configure the production server to bind to 0.0.0.0 or the platform-required interface.", locations[0])


def port_rule(report, evidence):
    production = evidence.production
    locations = evidence.ports
    if production.detected and production.port == "environment":
        locations = [location for location in locations if location not in evidence.development_startups]
    if production.detected and production.port == "literal":
        locations = [production.port_location]
    if locations:
        return finding("NET002", "Hardcoded application port", "warning", "potential", "networking",
            ("The detected Docker startup command" if locations[0] == production.port_location else "A Python startup call") +
            " uses a literal port. This can be valid, but a PaaS may require an environment-supplied port.",
            "Read the platform port from an environment variable, with an appropriate local default.", locations[0])


def absolute_path_rule(report, evidence):
    if evidence.absolute_paths:
        return finding("STO001", "Hardcoded absolute file path", "warning", "potential", "storage",
            "A path-shaped absolute literal may depend on a machine-specific filesystem layout.",
            "Use a configurable data directory or package-relative paths and verify filesystem permissions.", evidence.absolute_paths[0])


def storage_rule(report, evidence):
    if evidence.storage:
        return finding("STO002", "Potential local persistent storage", "warning", "potential", "storage",
            "SQLite, file writes, or local storage configuration may require durable storage. SQLite is not inherently unsuitable; container filesystems may be ephemeral.",
            "Review persistence needs; configure a durable volume or external storage and backups when necessary.", evidence.storage[0])


def secret_rule(report, evidence):
    if evidence.secrets:
        return finding("SEC001", "Potential hardcoded secret", "critical", "potential", "security",
            "A sensitive configuration name has a literal value or fallback. It may be a placeholder; no value is included in this report.",
            "Remove literal secret values and unsafe defaults; inject secrets through the runtime environment and rotate any real exposed credentials.", evidence.secrets[0])


def dockerfile_rule(report, evidence):
    if not report.dockerfile_present:
        return finding("CTR001", "No Dockerfile detected", "informational", "confirmed", "containerization",
            "A Dockerfile was not detected. Many cloud platforms also support source builds, so this does not prevent cloud deployment.",
            "For a container-based deployment, add and review a Dockerfile in a later configuration step.")


def dockerignore_rule(report, evidence):
    if not report.dockerignore_present:
        return finding("CTR002", "No .dockerignore detected", "informational", "confirmed", "containerization",
            "A container build ignore file was not detected; future build contexts should exclude secrets, caches, and local dependencies.",
            "Add a .dockerignore before container builds to exclude environment files, caches, and development artifacts.")


def environment_rule(report, evidence):
    missing = sorted(set(evidence.environment) - evidence.documented_environment)
    if missing:
        labels = {"required_access": "required-style lookup; potentially required", "no_default_detected": "no default detected; potentially required",
                  "default_detected": "default detected; may be optional"}
        issue = finding("ENV001", "Undocumented environment requirements", "warning", "confirmed", "runtime",
            "Missing from scoped .env.example: " + "; ".join(f"{key} ({labels[evidence.environment_usage[key]]})" for key in missing) +
            ". Usage is detected statically; no variable values are returned and defaults are not verified.",
            "Document these variable names and their optional or required usage in .env.example, using safe placeholders only.", evidence.environment[missing[0]])
        issue.environment_variables = [EnvironmentReference(name=key, usage=evidence.environment_usage[key]) for key in missing]
        return issue


def python_rule(report, evidence):
    if not report.python_version:
        return finding("RUN002", "Python runtime version is unknown", "informational", "potential", "runtime",
            "No single supported Python version declaration could be determined; the runtime may be declared elsewhere.",
            "Declare a supported Python version in .python-version or pyproject.toml project.requires-python.")


RULES = (dependency_rule, startup_rule, localhost_rule, port_rule, absolute_path_rule,
         storage_rule, secret_rule, dockerfile_rule, dockerignore_rule, environment_rule, python_rule)


def score_findings(findings: list[Finding]) -> int:
    groups = {}
    for issue in findings:
        group = GROUPS.get(issue.rule_id, issue.rule_id)
        groups[group] = max(groups.get(group, 0), DEDUCTIONS[(issue.severity, issue.confidence)])
    return max(0, min(100, 100 - sum(groups.values())))


def score_status(score: int) -> str:
    if score >= 85:
        return "ready_for_migration_review"
    return "needs_improvements" if score >= 60 else "significant_migration_concerns"


def assess(report: AnalysisReport, root: Path, files: list[Path], trees: dict[Path, ast.Module]) -> ReadinessAssessment:
    limitations = [DISCLAIMER, "Only static Python source and supported metadata in the selected scope are inspected; production commands, external services, and deployment behavior are not verified."]
    if report.project_type != "web_application" or report.analysis_root is None or report.framework not in {"Flask", "FastAPI"}:
        return ReadinessAssessment(assessment_limitations=[
            "Assessment unavailable: upload one supported web application directory with a clear application scope.", *limitations],
            migration_recommendations=["Upload a specific Flask or FastAPI application directory and resolve startup or project-root ambiguity before assessment."])
    evidence = collect_evidence(root, files, trees)
    evidence.production = inspect_startup(root, files, report.entry_point,
                                          report.entry_point_type == "application_factory", report.deployment_files)
    findings = [issue for rule in RULES if (issue := rule(report, evidence)) is not None]
    findings.sort(key=lambda issue: (SEVERITY_ORDER[issue.severity], issue.confidence != "confirmed", issue.rule_id))
    binding_known = bool(report.detected_hosts) or evidence.production.host != "unknown"
    if not binding_known:
        limitations.append("The production server binding could not be determined; no penalty is applied for an unknown binding.")
    if evidence.dynamic_environment:
        limitations.append("Computed environment variable names could not be resolved; no missing-documentation penalty is inferred for unknown names.")
    if report.analysis_warnings:
        limitations.append("Scanner warnings may indicate incomplete or unsupported configuration; review analysis_warnings alongside this assessment.")
    if evidence.production.detected:
        limitations.append("A matching production Uvicorn command was detected in a Dockerfile. Guarded development networking settings may be overridden; the configuration and deployment have not been validated.")
    if evidence.production.uncertain:
        limitations.append("Docker startup settings are incomplete, ambiguous, or unsupported; unresolved production behavior remains a limitation and potential Python networking concerns are retained.")
    provisional = bool(any(issue.confidence == "potential" for issue in findings)
        or not binding_known or evidence.production.uncertain or evidence.dynamic_environment or report.analysis_warnings
        or not report.dependencies or not report.entry_point or not report.python_version)
    if not report.dependencies and report.dependency_file_present:
        limitations.append("A dependency declaration exists but no runtime packages were parsed; dependency completeness is unknown.")
    summary = SeveritySummary(**{severity: sum(issue.severity == severity for issue in findings) for severity in SEVERITY_ORDER})
    score = score_findings(findings)
    recommendations = list(dict.fromkeys(issue.recommended_fix for issue in findings))
    if report.dockerfile_present:
        recommendations.append("Review and reuse the existing Dockerfile where suitable; its presence and detected startup settings are not validation. Test the existing build before considering a replacement.")
    if any(name not in {"Dockerfile", ".dockerignore", "runtime.txt"} and not name.startswith(("Dockerfile.", ".github/workflows/"))
           for name in report.deployment_files):
        recommendations.append("Review and reuse relevant existing deployment configurations listed in deployment_files. Check command overrides, environment, storage, and health checks; detected files are not validated deployments.")
    recommendations.append("After reviewing these findings, consider a container-based PaaS with a managed HTTP service, environment-injected configuration, health checks, and durable storage when needed. No provider is provisioned or deployed by Ascend.")
    return ReadinessAssessment(readiness_score=score, readiness_status=score_status(score),
        assessment_provisional=provisional, findings=findings, severity_summary=summary,
        migration_recommendations=recommendations, assessment_limitations=limitations)
