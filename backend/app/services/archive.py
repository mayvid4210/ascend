"""Validate every ZIP member, then stream into an isolated temporary directory."""

import re
import stat
import zipfile
import zlib
from pathlib import Path, PurePosixPath

from fastapi import HTTPException

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_EXTRACTED_BYTES = 50 * 1024 * 1024
MAX_FILES = 1000
IGNORED_DIRS = {
    ".git", ".venv", "venv", "__pycache__", "node_modules", "dist", "build",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "coverage", "htmlcov", "__MACOSX",
}
IGNORED_SUFFIXES = {".pyc", ".pyo", ".log", ".tsbuildinfo"}


def ignored(path: PurePosixPath) -> bool:
    return (
        any(part in IGNORED_DIRS or part.endswith(".egg-info") for part in path.parts)
        or path.suffix in IGNORED_SUFFIXES
        or path.name in {".DS_Store", ".coverage"}
        or (path.name.startswith(".env") and path.name != ".env.example")
    )


def extract_project(source, destination: Path) -> list[Path]:
    """Never use extractall, trust member sizes, or copy special filesystem objects."""
    try:
        with zipfile.ZipFile(source) as archive:
            members = archive.infolist()
            if not members:
                raise HTTPException(400, "The ZIP archive is empty.")
            if len(members) > MAX_FILES:
                raise HTTPException(413, "Archive exceeds the 1000-entry limit.")
            total = 0
            seen: set[str] = set()
            validated = []
            for member in members:
                name = member.orig_filename
                path = PurePosixPath(name)
                mode = member.external_attr >> 16
                if (
                    not name or "\x00" in name or "\\" in name or path.is_absolute()
                    or ".." in path.parts or re.match(r"^[A-Za-z]:", name)
                    or any(":" in part for part in path.parts)
                    or any(part.endswith((" ", ".")) for part in path.parts)
                ):
                    raise HTTPException(400, "Archive contains an unsafe path.")
                if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
                    raise HTTPException(400, "Archive contains a symbolic link or special file.")
                if member.flag_bits & 1:
                    raise HTTPException(400, "Encrypted ZIP archives are unsupported.")
                key = str(path).casefold()
                if key in seen:
                    raise HTTPException(400, "Archive contains duplicate paths.")
                seen.add(key)
                total += member.file_size
                if total > MAX_EXTRACTED_BYTES:
                    raise HTTPException(413, "Archive exceeds the 50 MB extracted-size limit.")
                validated.append((member, path))

            written: list[Path] = []
            actual_total = 0
            for member, path in validated:
                if member.is_dir():
                    continue
                # Even ignored files count toward limits and receive CRC validation.
                target = destination.joinpath(*path.parts)
                keep = not ignored(path)
                if keep:
                    target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as stream:
                    output = target.open("xb") if keep else None
                    try:
                        while chunk := stream.read(64 * 1024):
                            actual_total += len(chunk)
                            if actual_total > MAX_EXTRACTED_BYTES:
                                raise HTTPException(413, "Archive exceeds the 50 MB extracted-size limit.")
                            if output:
                                output.write(chunk)
                    finally:
                        if output:
                            output.close()
                if keep:
                    written.append(target)
            return sorted(written)
    except HTTPException:
        raise
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError,
            RuntimeError, NotImplementedError, EOFError, zlib.error) as exc:
        raise HTTPException(400, "Malformed or unsupported ZIP archive.") from exc
