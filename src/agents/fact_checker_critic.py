"""Subagent 3: FactCheckerCriticAgent — 100% deterministic verification against YAML ground truth."""

from __future__ import annotations

import time
from typing import Any, Optional

from src.llm_provider import estimate_cost_usd, estimate_tokens
from src.schemas import (
    AgentStepMetric,
    CriticVerificationReport,
    DiffAnalysisReport,
    DocumentationDraft,
    ProviderConfig,
)
from src.spec_validator import verify_draft_against_spec


class FactCheckerCriticAgent:
    """Compares generated EN/FR drafts against api_and_hardware_specs.yaml and rejects drift."""

    def __init__(self, config: Optional[ProviderConfig] = None) -> None:
        self.config = config or ProviderConfig()

    def verify(
        self,
        original_en: str,
        original_fr: str,
        draft: DocumentationDraft,
        diff_analysis: DiffAnalysisReport,
        spec_data: dict[str, Any],
        attempt: int = 1,
    ) -> tuple[CriticVerificationReport, AgentStepMetric]:
        t0 = time.perf_counter()

        report = verify_draft_against_spec(
            original_en=original_en,
            original_fr=original_fr,
            draft=draft,
            diff_analysis=diff_analysis,
            spec_data=spec_data,
            attempt=attempt,
        )

        prompt_tokens = estimate_tokens(draft.updated_manual_en) + estimate_tokens(
            draft.updated_manual_fr
        )
        completion_tokens = estimate_tokens(report.model_dump_json())
        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 3)
        metric = AgentStepMetric(
            node_name="fact_checker_critic",
            attempt=attempt,
            latency_ms=elapsed_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            estimated_cost_usd=estimate_cost_usd(
                prompt_tokens, completion_tokens, self.config.model_name
            ),
        )
        return report, metric
