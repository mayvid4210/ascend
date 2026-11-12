"""Prepare fresh contexts for repository-owned samples only; never build/run code.

Run with backend/.venv/bin/python scripts/prepare_validation.py --sample flask-app
--output /tmp/new-context. The output directory must not already exist.
"""

import argparse
import io
import json
import shutil
import sys
import zipfile
from pathlib import Path, PurePosixPath

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ('flask-app', 'fastapi-app')
sys.path.insert(0, str(REPOSITORY_ROOT / 'backend'))

from app.api.generate import generate_archive
from app.services.archive import ignored


def prepare_context(sample: str, output: Path) -> dict:
    if sample not in SAMPLES:
        raise ValueError('Only repository-owned Flask/FastAPI samples can be prepared.')
    if output.exists():
        raise ValueError('Output already exists; choose a new temporary context directory.')
    source = REPOSITORY_ROOT / 'examples' / sample
    files = []
    for path in sorted(source.rglob('*')):
        if path.is_symlink():
            raise ValueError('Trusted samples must not contain symbolic links.')
        if path.is_file() and not ignored(PurePosixPath(path.relative_to(source).as_posix())):
            files.append(path)
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, f'{sample}/{path.relative_to(source).as_posix()}')
    payload.seek(0)
    # This is the same extraction/scanning/generation processor as POST /api/generate.
    report = generate_archive(payload, sample)
    generated = {item.path: item.content for item in report.generated_files}
    if not report.eligible or 'Dockerfile' not in generated or 'requirements.ascend.txt' not in generated:
        raise RuntimeError('Fresh executable sample configuration was not generated; review generator warnings.')
    output.mkdir(parents=True, exist_ok=False)
    try:
        for path in files:
            target = output / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        for name, content in generated.items():
            relative = PurePosixPath(name)
            if relative.is_absolute() or '..' in relative.parts or '\\' in name:
                raise ValueError('Unsafe generated configuration path.')
            target = output.joinpath(*relative.parts)
            if target.exists():
                raise RuntimeError('Generator attempted to overwrite a required existing sample file.')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding='utf-8')
    except BaseException:
        shutil.rmtree(output)
        raise
    return {
        'sample': sample,
        'generated_files': sorted(generated),
        'preserved_files': sorted(path.relative_to(source).as_posix() for path in files),
        'generation_status': report.status,
        'warnings': report.generation_warnings,
        'validation_status': 'context_prepared_not_built',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sample', required=True, choices=SAMPLES)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare_context(args.sample, args.output), indent=2))


if __name__ == '__main__':
    main()
