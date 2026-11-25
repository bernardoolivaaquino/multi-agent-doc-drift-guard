"""Strict Pydantic v2 schemas for the Multi-Agent Doc Drift Guard pipeline."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class ChangeType(str, Enum):
    """Supported specification diff operations."""

    MODIFIED = "modified"
    ADDED = "added"
    REMOVED = "removed"


class SpecParameterChange(BaseModel):
    """Structured representation of a single technical specification change."""

    change_id: str = Field(..., description="Unique change identifier (e.g. CHG-001)")
    yaml_path: str = Field(..., description="Dot-separated YAML path to the changed key")
    parameter_name: str = Field(..., description="Leaf parameter name")
    change_type: ChangeType
    old_value: Any = None
    new_value: Any = None
    unit: Optional[str] = None
    endpoint: Optional[str] = None
    extra_metadata: dict[str, Any] = Field(default_factory=dict)
    affected_sections_en: list[str] = Field(default_factory=list)
    affected_sections_fr: list[str] = Field(default_factory=list)
    severity: Literal["critical", "high", "medium"] = "critical"
    rationale: str = Field(..., min_length=1)


class DiffAnalysisReport(BaseModel):
    """Output emitted by DiffAnalyzerAgent."""

    source_spec_file: str
    total_changes: int = Field(..., ge=0)
    changes: list[SpecParameterChange] = Field(default_factory=list)
    affected_manuals: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_count(self) -> "DiffAnalysisReport":
        if self.total_changes != len(self.changes):
            raise ValueError(
                f"total_changes ({self.total_changes}) must equal len(changes) ({len(self.changes)})"
            )
        return self


class SectionRewrite(BaseModel):
    """Tracks a single rewritten Markdown section in one language."""

    language: Literal["en", "fr"]
    section_heading: str
    original_content: str
    updated_content: str
    applied_change_ids: list[str] = Field(default_factory=list)


class DocumentationDraft(BaseModel):
    """Output emitted by TechnicalWriterAgent."""

    revision: int = Field(..., ge=1)
    updated_manual_en: str
    updated_manual_fr: str
    section_rewrites: list[SectionRewrite] = Field(default_factory=list)
    untouched_sections_preserved: bool = True


FactCheckErrorCode = Literal[
    "VALUE_MISMATCH",
    "UNIT_MISMATCH",
    "STALE_VALUE_REMAINING",
    "MISSING_PARAMETER",
    "LATEX_SYNTAX_BROKEN",
    "UNTOUCHED_SECTION_MODIFIED",
    "SPEC_VALUE_HALLUCINATION",
]


class FactCheckError(BaseModel):
    """Structured rejection feedback produced by FactCheckerCriticAgent."""

    error_code: FactCheckErrorCode
    language: Literal["en", "fr"]
    section_heading: str
    yaml_path: str
    expected_value: str
    found_value: str
    message: str


class CriticVerificationReport(BaseModel):
    """Verification verdict produced by FactCheckerCriticAgent."""

    approved: bool
    attempt: int = Field(..., ge=1)
    checked_parameters: int = Field(..., ge=0)
    numeric_hallucinations_found: int = Field(..., ge=0)
    errors: list[FactCheckError] = Field(default_factory=list)
    summary: str

    @model_validator(mode="after")
    def _validate_approval(self) -> "CriticVerificationReport":
        if self.approved and self.errors:
            raise ValueError("approved cannot be True when errors are present")
        if not self.approved and not self.errors:
            raise ValueError("errors cannot be empty when approved is False")
        return self


class AgentStepMetric(BaseModel):
    """Execution telemetry captured for each agent node invocation."""

    node_name: str
    attempt: int = Field(..., ge=1)
    latency_ms: float = Field(..., ge=0.0)
    prompt_tokens: int = Field(..., ge=0)
    completion_tokens: int = Field(..., ge=0)
    estimated_cost_usd: float = Field(..., ge=0.0)


class ProviderConfig(BaseModel):
    """Configuration controlling backend selection, retries, and fault injection."""

    provider: Literal["deterministic", "openai", "anthropic", "gemini"] = "deterministic"
    model_name: str = "gpt-4o-mini"
    max_retries: int = Field(default=3, ge=0, le=10)
    fault_injection: dict[int, list[str]] = Field(default_factory=dict)


class PipelineResult(BaseModel):
    """Final aggregated result returned by the LangGraph workflow."""

    status: Literal["APPROVED", "NO_DRIFT", "MAX_RETRIES_EXCEEDED"]
    diff_analysis: DiffAnalysisReport
    final_draft: Optional[DocumentationDraft] = None
    verification_history: list[CriticVerificationReport] = Field(default_factory=list)
    retry_count: int = Field(..., ge=0)
    metrics: list[AgentStepMetric] = Field(default_factory=list)
    total_latency_ms: float = Field(..., ge=0.0)
    total_tokens: int = Field(..., ge=0)
    total_cost_usd: float = Field(..., ge=0.0)
