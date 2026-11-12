"""Public report fields. Source text and environment values never belong here."""

from typing import Literal

from pydantic import BaseModel
from app.models.readiness import ReadinessAssessment

EntryPointType = Literal["application_object", "application_factory"]


class Dependency(BaseModel):
    name: str
    version: str | None = None


class ExcludedApplication(BaseModel):
    path: str
    object_name: str
    scope: Literal["test", "example", "documentation", "benchmark", "tooling"]


class AnalysisReport(ReadinessAssessment):
    project_name: str
    project_type: Literal["web_application", "python_library", "framework_repository", "ambiguous", "unsupported"]
    analysis_root: str | None
    project_root_candidates: list[str]
    dependency_files: list[str]
    excluded_application_objects: list[ExcludedApplication]
    framework: Literal["Flask", "FastAPI", "Unknown"]
    python_version: str | None = None
    dependencies: list[Dependency]
    dependency_file_present: bool
    entry_point: str | None = None
    entry_point_type: EntryPointType | None = None
    entry_point_candidate_types: dict[str, EntryPointType]
    entry_point_candidates: list[str]
    environment_variables: list[str]
    detected_ports: list[int]
    detected_hosts: list[str]
    dockerfile_present: bool
    dockerignore_present: bool
    env_example_present: bool
    deployment_files: list[str]
    files_scanned: int
    analysis_warnings: list[str]
