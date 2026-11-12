"""Ascend's local development API."""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.body_limit import RequestBodyLimitMiddleware

from app.api.analyze import router
from app.api.generate import router as generation_router
from app.services.archive import MAX_UPLOAD_BYTES

app = FastAPI(title="Ascend")
app.include_router(router)
app.include_router(generation_router)
# Bound multipart overhead as well as files, including chunked requests.
app.add_middleware(RequestBodyLimitMiddleware, max_body_size=MAX_UPLOAD_BYTES + 64 * 1024)
origins = os.getenv(
    "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in origins.split(",") if origin.strip()],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    """Return the application identity and liveness status."""
    return {"name": "Ascend", "status": "ok"}
