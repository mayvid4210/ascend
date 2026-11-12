from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import APIRouter, Request

from app.api.analyze import process_upload
from app.models.generation import GenerationReport
from app.services.archive import extract_project
from app.services.generator import generate
from app.services.scanner import scan_project
from app.services.scope import select_scope, unwrap_root

router = APIRouter()


def generate_archive(source, name):
    with TemporaryDirectory(prefix='ascend-generate-') as directory:
        root = Path(directory) / 'project'
        root.mkdir()
        files = extract_project(source, root)
        report = scan_project(root, files, name)
        repository_root = unwrap_root(root, files)
        selected, _, scoped = select_scope(repository_root, files)
        # Preserve every existing file at the selected root for collision checks, including CI.
        return generate(report, selected or repository_root, scoped, files, repository_root)


@router.post('/api/generate', response_model=GenerationReport, openapi_extra={
    'requestBody': {'required': True, 'content': {'multipart/form-data': {'schema': {
        'type': 'object', 'required': ['file'], 'properties': {'file': {'type': 'string', 'format': 'binary'}},
    }}}},
})
async def generate_files(request: Request):
    return await process_upload(request, generate_archive)
