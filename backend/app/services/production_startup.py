"""Read simple Docker startup declarations as data. Never run shell commands."""

import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ProductionStartup:
    detected: bool = False
    host: str = "unknown"  # wildcard, loopback, unknown
    port: str = "unknown"  # environment, literal, unknown
    location: tuple[str, int] | None = None
    port_location: tuple[str, int] | None = None
    uncertain: bool = False


def inspect_startup(root: Path, files: list[Path], target: str | None,
                    factory: bool, deployment_files: list[str]) -> ProductionStartup:
    dockerfiles = [path for path in files if path.name == "Dockerfile" or path.name.startswith("Dockerfile.")]
    if not dockerfiles:
        return ProductionStartup()
    unknown = ProductionStartup(uncertain=True)
    if len(dockerfiles) != 1 or dockerfiles[0].parent != root:
        return unknown
    path = dockerfiles[0]
    if path.stat().st_size > 1024 * 1024:
        return unknown
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeError:
        return unknown
    # Overrides in other deployment files, stages, and continuations require fuller interpretation.
    other_configs = [name for name in deployment_files if name not in {
        path.name, ".dockerignore", "runtime.txt"
    } and not name.startswith(".github/workflows/")]
    if other_configs or "\\\n" in text or any(re.match(r"\s*SHELL\s", line, re.I) for line in text.splitlines()) or sum(bool(re.match(r"\s*FROM\s", line, re.I)) for line in text.splitlines()) != 1:
        return unknown
    commands = {}
    for line_number, line in enumerate(text.splitlines(), 1):
        match = re.match(r"\s*(CMD|ENTRYPOINT)\s+(.+)", line, re.I)
        if not match:
            continue
        instruction, command = match[1].upper(), match[2].strip()
        if not command.startswith("[") and (re.search(r"'[^']*\$[^']*'", command) or "\\$" in command):
            return unknown  # Quoted/escaped dollars are not evidence of shell expansion.
        if instruction in commands:
            return unknown
        try:
            tokens = json.loads(command) if command.startswith("[") else shlex.split(command)
        except (ValueError, TypeError, RecursionError):
            return unknown
        if not isinstance(tokens, list) or not all(isinstance(token, str) for token in tokens):
            return unknown
        commands[instruction] = (tokens, not command.startswith("["), line_number)
    if not commands:
        return ProductionStartup()
    base = next((re.match(r"\s*FROM\s+(\S+)", line, re.I)[1] for line in text.splitlines()
                 if re.match(r"\s*FROM\s+(\S+)", line, re.I)), "")
    if "ENTRYPOINT" not in commands and not re.fullmatch(r"(?:docker.io/library/)?python(?::[^\s]+)?", base):
        return unknown  # A custom base may inherit an entrypoint that changes CMD semantics.
    entry, entry_shell, entry_line = commands.get("ENTRYPOINT", ([], False, 0))
    cmd, cmd_shell, cmd_line = commands.get("CMD", ([], False, 0))
    if entry and (entry_shell and cmd or cmd_shell):
        return unknown
    tokens, shell = entry + cmd, entry_shell or cmd_shell
    if len(tokens) == 3 and tokens[0] in {"sh", "/bin/sh", "bash", "/bin/bash"} and tokens[1] == "-c":
        if re.search(r"'[^']*\$[^']*'", tokens[2]) or "\\$" in tokens[2]:
            return unknown
        try:
            tokens = shlex.split(tokens[2])
        except ValueError:
            return unknown
        shell = True
    if tokens[:1] == ["exec"] and shell:
        tokens = tokens[1:]
    if any(any(character in token for character in ";|&<>`\n") or "$(" in token for token in tokens):
        return unknown
    if tokens[:3] in (["python", "-m", "uvicorn"], ["python3", "-m", "uvicorn"]):
        tokens = tokens[2:]
    if not tokens or tokens[0] != "uvicorn" or len(tokens) < 2 or tokens[1] != target:
        return unknown
    if factory != ("--factory" in tokens):
        return unknown
    options = {}
    index = 2
    flags = {"--factory", "--proxy-headers", "--no-proxy-headers", "--no-access-log"}
    valued = {"--host", "--port", "--workers", "--log-level", "--timeout-keep-alive"}
    while index < len(tokens):
        option, separator, value = tokens[index].partition("=")
        if option in flags and not separator:
            index += 1
            continue
        if option not in valued or option in options:
            return unknown
        if not separator:
            index += 1
            if index >= len(tokens):
                return unknown
            value = tokens[index]
        options[option] = value
        index += 1
    host = options.get("--host")
    port = options.get("--port", "")
    host_kind = "wildcard" if host in {"0.0.0.0", "::"} else "loopback" if host in {"localhost", "127.0.0.1", "::1"} else "unknown"
    port_kind = "literal" if port.isdecimal() and len(port) <= 5 and 1 <= int(port) <= 65535 else (
        "environment" if shell and re.fullmatch(r"\$(?:[A-Za-z_][A-Za-z0-9_]*|\{[A-Za-z_][A-Za-z0-9_]*\})", port) else "unknown")
    host_line = entry_line if entry and (not cmd or any(token == "--host" or token.startswith("--host=") for token in entry)) else cmd_line
    port_line = entry_line if entry and (not cmd or any(token == "--port" or token.startswith("--port=") for token in entry)) else cmd_line
    return ProductionStartup(detected=True, host=host_kind, port=port_kind,
                             location=(path.relative_to(root).as_posix(), host_line or cmd_line or entry_line),
                             port_location=(path.relative_to(root).as_posix(), port_line or cmd_line or entry_line),
                             uncertain=host_kind == "unknown" or port_kind == "unknown")
