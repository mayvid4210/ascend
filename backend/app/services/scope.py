"""Repository-relative scope rules; auxiliary code is retained, not discarded."""

from pathlib import Path

DIRECTORY_SCOPES = {
    "tests": "test", "test": "test", "testing": "test", "fixtures": "test",
    "docs": "documentation", "doc": "documentation", "documentation": "documentation",
    "examples": "example", "example": "example", "samples": "example", "sample": "example",
    "benchmarks": "benchmark", "benchmark": "benchmark",
    "tools": "tooling", "scripts": "tooling", "dev": "tooling", ".github": "tooling",
}
PROJECT_MARKERS = {"requirements.txt", "pyproject.toml", "setup.cfg", "setup.py"}


def source_scope(path: Path, root: Path) -> str:
    relative = path.relative_to(root)
    for part in relative.parts[:-1]:
        if part.lower() in DIRECTORY_SCOPES:
            return DIRECTORY_SCOPES[part.lower()]
    name = path.name.lower()
    if name == "conftest.py" or name.startswith("test_") or name.endswith("_test.py"):
        return "test"
    if name in {"setup.py", "noxfile.py", "toxfile.py", "fabfile.py", "manage_dev.py"}:
        return "tooling"
    return "primary"


def unwrap_root(root: Path, files: list[Path]) -> Path:
    """Remove ZIP wrappers, but retain meaningful source/test directory boundaries."""
    while files:
        relative = [path.relative_to(root) for path in files]
        first = relative[0].parts[0]
        if any(len(path.parts) < 2 or path.parts[0] != first for path in relative):
            break
        if root / first / "__init__.py" in files:
            break  # Preserve importable package names such as flaskr.
        if first.lower() in {*DIRECTORY_SCOPES, "src", "apps", "packages"}:
            # An explicitly uploaded example root with its own declaration is valid.
            child = root / first
            standalone_example = DIRECTORY_SCOPES.get(first.lower()) == "example" and any(
                path.parent == child and path.name in PROJECT_MARKERS for path in files
            ) and any(path.parent == child and path.suffix == ".py" for path in files)
            if not standalone_example:
                break
        root = root / first
    return root


def select_scope(root: Path, files: list[Path]) -> tuple[Path | None, list[Path], list[Path]]:
    """Choose one declaration boundary, or leave production metadata unselected."""
    primary = [path for path in files if source_scope(path, root) == "primary"]
    boundaries = {path.parent for path in primary if path.name in PROJECT_MARKERS}
    # setup.py is tooling, but still marks a package/project boundary.
    boundaries.update(path.parent for path in files if path.name == "setup.py"
                      and source_scope(path.parent / "placeholder", root) == "primary")

    def owner(path: Path) -> Path:
        return next((parent for parent in path.parents if parent in boundaries), root)

    candidates = sorted({owner(path) for path in primary if path.suffix == ".py"}) or [root]
    if len(candidates) != 1:
        return None, candidates, []
    selected = candidates[0]
    scoped = [path for path in files if path.is_relative_to(selected) and owner(path) == selected
              and (source_scope(path, root) == "primary" or (
                  path.parent.name == "workflows" and path.parent.parent.name == ".github"
                  and path.suffix in {".yml", ".yaml"}))]
    return selected, candidates, scoped
