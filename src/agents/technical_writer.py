"""Subagent 2: TechnicalWriterAgent — rewrites only affected EN/FR manual sections."""

from __future__ import annotations

import time
from typing import Optional

from src.llm_provider import create_writer_backend, estimate_cost_usd
from src.schemas import (
    AgentStepMetric,
    CriticVerificationReport,
    DiffAnalysisReport,
    DocumentationDraft,
    FactCheckError,
    ProviderConfig,
    SectionRewrite,
    SpecParameterChange,
)
from src.spec_validator import reassemble_markdown_sections, split_markdown_sections


class TechnicalWriterAgent:
    """Rewrites only affected Markdown/LaTeX sections in EN and FR manuals."""

    def __init__(self, config: Optional[ProviderConfig] = None) -> None:
        self.config = config or ProviderConfig()
        self.backend = create_writer_backend(self.config)

    def write(
        self,
        manual_en: str,
        manual_fr: str,
        diff_analysis: DiffAnalysisReport,
        prior_verification: Optional[CriticVerificationReport] = None,
        attempt: int = 1,
    ) -> tuple[DocumentationDraft, AgentStepMetric]:
        t0 = time.perf_counter()

        total_prompt_tokens = 0
        total_completion_tokens = 0
        rewrites: list[SectionRewrite] = []

        # Map (language, section_heading) -> changes and prior errors
        en_changes_by_sec: dict[str, list[SpecParameterChange]] = {}
        fr_changes_by_sec: dict[str, list[SpecParameterChange]] = {}
        for chg in diff_analysis.changes:
            for sec in chg.affected_sections_en:
                en_changes_by_sec.setdefault(sec, []).append(chg)
            for sec in chg.affected_sections_fr:
                fr_changes_by_sec.setdefault(sec, []).append(chg)

        errors_by_sec: dict[tuple[str, str], list[FactCheckError]] = {}
        if prior_verification is not None:
            for err in prior_verification.errors:
                errors_by_sec.setdefault((err.language, err.section_heading), []).append(err)

        def _process_manual(
            lang: str,
            raw_manual: str,
            changes_by_sec: dict[str, list[SpecParameterChange]],
        ) -> str:
            nonlocal total_prompt_tokens, total_completion_tokens
            preamble, sections = split_markdown_sections(raw_manual)
            new_sections: list[tuple[str, str]] = []

            for heading, body in sections:
                sec_changes = changes_by_sec.get(heading)
                if not sec_changes:
                    new_sections.append((heading, body))
                    continue

                sec_errors = errors_by_sec.get((lang, heading), [])
                updated_body, p_tok, c_tok = self.backend.rewrite_section(
                    language=lang,  # type: ignore[arg-type]
                    section_heading=heading,
                    original_body=body,
                    changes=sec_changes,
                    prior_errors=sec_errors,
                    attempt=attempt,
                )
                total_prompt_tokens += p_tok
                total_completion_tokens += c_tok

                rewrites.append(
                    SectionRewrite(
                        language=lang,  # type: ignore[arg-type]
                        section_heading=heading,
                        original_content=body,
                        updated_content=updated_body,
                        applied_change_ids=[c.change_id for c in sec_changes],
                    )
                )
                new_sections.append((heading, updated_body))

            return reassemble_markdown_sections(preamble, new_sections)

        updated_en = _process_manual("en", manual_en, en_changes_by_sec)
        updated_fr = _process_manual("fr", manual_fr, fr_changes_by_sec)

        # Optional test-only fault injection targeting an untouched section
        if "corrupt_untouched" in self.config.fault_injection.get(attempt, []):
            updated_en = updated_en.replace("1.45 kg", "1.99 kg", 1)

        draft = DocumentationDraft(
            revision=attempt,
            updated_manual_en=updated_en,
            updated_manual_fr=updated_fr,
            section_rewrites=rewrites,
            untouched_sections_preserved=(
                "corrupt_untouched" not in self.config.fault_injection.get(attempt, [])
            ),
        )

        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 3)
        metric = AgentStepMetric(
            node_name="technical_writer",
            attempt=attempt,
            latency_ms=elapsed_ms,
            prompt_tokens=total_prompt_tokens,
            completion_tokens=total_completion_tokens,
            estimated_cost_usd=estimate_cost_usd(
                total_prompt_tokens, total_completion_tokens, self.config.model_name
            ),
        )
        return draft, metric
