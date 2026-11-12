from typing import Literal

from pydantic import BaseModel, Field

from app.models.readiness import Finding


class GeneratedFile(BaseModel):
    path: str
    content: str
    requires_manual_review: bool = True


class SkippedFile(BaseModel):
    path: str
    reason: str


class GenerationReport(BaseModel):
    eligible: bool
    status: Literal["manual_review", "ineligible"]
    analysis_root: str | None
    generated_files: list[GeneratedFile] = Field(default_factory=list)
    existing_files: list[str] = Field(default_factory=list)
    skipped_files: list[SkippedFile] = Field(default_factory=list)
    generation_warnings: list[str] = Field(default_factory=list)
    unresolved_findings: list[Finding] = Field(default_factory=list)
    deployment_instructions: list[str] = Field(default_factory=list)
    download_zip_base64: str | None = None
