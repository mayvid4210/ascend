"""Multipart transport and temporary workspace lifecycle."""

import re
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from app.models.analysis import AnalysisReport
from app.services.archive import MAX_UPLOAD_BYTES, extract_project
from app.services.scanner import scan_project

router = APIRouter()


def analyze_archive(source, project_name: str) -> AnalysisReport:
    with TemporaryDirectory(prefix="ascend-") as directory:
        root = Path(directory) / "project"
        root.mkdir()
        files = extract_project(source, root)
        return scan_project(root, files, project_name)


@router.post("/api/analyze", response_model=AnalysisReport, openapi_extra={
    "requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
        "type": "object", "required": ["file"],
        "properties": {"file": {"type": "string", "format": "binary"}},
    }}}},
})
async def analyze(request: Request) -> AnalysisReport:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "multipart/form-data":
        raise HTTPException(415, "Send one ZIP archive as multipart/form-data in the 'file' field.")
    async with request.form(max_files=1, max_fields=0) as form:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise HTTPException(422, "A ZIP upload in the 'file' field is required.")
        if not upload.filename or not upload.filename.lower().endswith(".zip"):
            raise HTTPException(415, "Only .zip archives are accepted.")
        if upload.size is None or upload.size > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "ZIP upload exceeds the 10 MB limit.")
        filename = upload.filename.replace("\\", "/").rsplit("/", 1)[-1]
        name = re.sub(r"[^A-Za-z0-9_.-]", "_", filename[:-4])[:100] or "uploaded-project"
        return await run_in_threadpool(analyze_archive, upload.file, name)
