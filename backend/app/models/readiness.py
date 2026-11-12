"""Public readiness results contain descriptions and locations, never source values."""

from typing import Literal

from pydantic import BaseModel, Field


class EnvironmentReference(BaseModel):
    name: str
    usage: Literal["required_access", "no_default_detected", "default_detected"]


class Finding(BaseModel):
    rule_id: str
    title: str
    severity: Literal["critical", "warning", "informational"]
    confidence: Literal["confirmed", "potential"]
    category: Literal["dependencies", "runtime", "networking", "storage", "security", "containerization"]
    file_path: str | None = None
    line_number: int | None = Field(default=None, ge=1)
    explanation: str
    recommended_fix: str
    environment_variables: list[EnvironmentReference] = Field(default_factory=list)


class SeveritySummary(BaseModel):
    critical: int = 0
    warning: int = 0
    informational: int = 0


class ReadinessAssessment(BaseModel):
    readiness_score: int | None = Field(default=None, ge=0, le=100)
    readiness_status: Literal["ready_for_migration_review", "needs_improvements", "significant_migration_concerns", "unavailable"] = "unavailable"
    assessment_provisional: bool = True
    findings: list[Finding] = Field(default_factory=list)
    severity_summary: SeveritySummary = Field(default_factory=SeveritySummary)
    migration_recommendations: list[str] = Field(default_factory=list)
    assessment_limitations: list[str] = Field(default_factory=list)
