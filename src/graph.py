"""LangGraph StateGraph orchestration for Multi-Agent Doc Drift Guard."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Literal, Optional, TypedDict

import yaml
from langgraph.graph import END, START, StateGraph

from src.agents.diff_analyzer import DiffAnalyzerAgent
from src.agents.fact_checker_critic import FactCheckerCriticAgent
from src.agents.technical_writer import TechnicalWriterAgent
from src.schemas import (
    AgentStepMetric,
    CriticVerificationReport,
    DiffAnalysisReport,
    DocumentationDraft,
    PipelineResult,
    ProviderConfig,
)


class DriftGuardState(TypedDict):
    """Shared typed state passed across LangGraph nodes."""

    spec_data: dict[str, Any]
    diff_text: str
    source_spec_file: str
    original_manual_en: str
    original_manual_fr: str
    max_retries: int
    retry_count: int
    diff_analysis: Optional[DiffAnalysisReport]
    draft: Optional[DocumentationDraft]
    latest_verification: Optional[CriticVerificationReport]
    verification_history: list[CriticVerificationReport]
    metrics: list[AgentStepMetric]
    status: Literal["PENDING", "APPROVED", "NO_DRIFT", "MAX_RETRIES_EXCEEDED"]


def build_drift_guard_graph(config: Optional[ProviderConfig] = None):
    """Compile and return the LangGraph StateGraph for documentation drift guard."""
    cfg = config or ProviderConfig()
    analyzer = DiffAnalyzerAgent(cfg)
    writer = TechnicalWriterAgent(cfg)
    critic = FactCheckerCriticAgent(cfg)

    def diff_analyzer_node(state: DriftGuardState) -> dict[str, Any]:
        report, metric = analyzer.analyze(
            diff_text=state["diff_text"],
            spec_data=state["spec_data"],
            manual_en=state["original_manual_en"],
            manual_fr=state["original_manual_fr"],
            source_spec_file=state["source_spec_file"],
        )
        next_status: Literal["PENDING", "NO_DRIFT"] = (
            "NO_DRIFT" if report.total_changes == 0 else "PENDING"
        )
        return {
            "diff_analysis": report,
            "metrics": [*state["metrics"], metric],
            "status": next_status,
        }

    def technical_writer_node(state: DriftGuardState) -> dict[str, Any]:
        assert state["diff_analysis"] is not None
        attempt = state["retry_count"] + 1
        draft, metric = writer.write(
            manual_en=state["original_manual_en"],
            manual_fr=state["original_manual_fr"],
            diff_analysis=state["diff_analysis"],
            prior_verification=state["latest_verification"],
            attempt=attempt,
        )
        return {
            "draft": draft,
            "metrics": [*state["metrics"], metric],
        }

    def fact_checker_critic_node(state: DriftGuardState) -> dict[str, Any]:
        assert state["diff_analysis"] is not None
        assert state["draft"] is not None
        attempt = state["retry_count"] + 1
        report, metric = critic.verify(
            original_en=state["original_manual_en"],
            original_fr=state["original_manual_fr"],
            draft=state["draft"],
            diff_analysis=state["diff_analysis"],
            spec_data=state["spec_data"],
            attempt=attempt,
        )
        history = [*state["verification_history"], report]
        metrics = [*state["metrics"], metric]

        if report.approved:
            return {
                "latest_verification": report,
                "verification_history": history,
                "metrics": metrics,
                "status": "APPROVED",
            }
        if state["retry_count"] < state["max_retries"]:
            return {
                "latest_verification": report,
                "verification_history": history,
                "metrics": metrics,
                "retry_count": state["retry_count"] + 1,
                "status": "PENDING",
            }
        return {
            "latest_verification": report,
            "verification_history": history,
            "metrics": metrics,
            "status": "MAX_RETRIES_EXCEEDED",
        }

    def route_after_diff(state: DriftGuardState) -> str:
        analysis = state["diff_analysis"]
        if analysis is None or analysis.total_changes == 0:
            return "no_drift"
        return "has_drift"

    def route_after_verification(state: DriftGuardState) -> str:
        if state["status"] == "APPROVED":
            return "approved"
        if state["status"] == "MAX_RETRIES_EXCEEDED":
            return "max_retries_exceeded"
        return "retry"

    builder = StateGraph(DriftGuardState)
    builder.add_node("diff_analyzer", diff_analyzer_node)
    builder.add_node("technical_writer", technical_writer_node)
    builder.add_node("fact_checker_critic", fact_checker_critic_node)

    builder.add_edge(START, "diff_analyzer")
    builder.add_conditional_edges(
        "diff_analyzer",
        route_after_diff,
        {
            "no_drift": END,
            "has_drift": "technical_writer",
        },
    )
    builder.add_edge("technical_writer", "fact_checker_critic")
    builder.add_conditional_edges(
        "fact_checker_critic",
        route_after_verification,
        {
            "approved": END,
            "retry": "technical_writer",
            "max_retries_exceeded": END,
        },
    )
    return builder.compile()


def run_drift_guard(
    spec_path: Path,
    diff_path: Path,
    manual_en_path: Path,
    manual_fr_path: Path,
    config: Optional[ProviderConfig] = None,
) -> PipelineResult:
    """Load inputs from disk, execute the LangGraph workflow, and return a PipelineResult."""
    spec_data = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    diff_text = diff_path.read_text(encoding="utf-8")
    manual_en = manual_en_path.read_text(encoding="utf-8")
    manual_fr = manual_fr_path.read_text(encoding="utf-8")
    return run_drift_guard_from_strings(
        spec_data=spec_data,
        diff_text=diff_text,
        manual_en=manual_en,
        manual_fr=manual_fr,
        source_spec_file=spec_path.name,
        config=config,
    )


def run_drift_guard_from_strings(
    spec_data: dict[str, Any],
    diff_text: str,
    manual_en: str,
    manual_fr: str,
    source_spec_file: str = "api_and_hardware_specs.yaml",
    config: Optional[ProviderConfig] = None,
) -> PipelineResult:
    """Execute the LangGraph workflow directly on in-memory specification and manual strings."""
    cfg = config or ProviderConfig()
    graph = build_drift_guard_graph(cfg)

    initial_state: DriftGuardState = {
        "spec_data": spec_data,
        "diff_text": diff_text,
        "source_spec_file": source_spec_file,
        "original_manual_en": manual_en,
        "original_manual_fr": manual_fr,
        "max_retries": cfg.max_retries,
        "retry_count": 0,
        "diff_analysis": None,
        "draft": None,
        "latest_verification": None,
        "verification_history": [],
        "metrics": [],
        "status": "PENDING",
    }

    t0 = time.perf_counter()
    final_state = graph.invoke(initial_state, config={"recursion_limit": 25})
    wall_ms = round((time.perf_counter() - t0) * 1000.0, 3)

    metrics: list[AgentStepMetric] = final_state["metrics"]
    total_tokens = sum(m.prompt_tokens + m.completion_tokens for m in metrics)
    total_cost = round(sum(m.estimated_cost_usd for m in metrics), 8)

    assert final_state["diff_analysis"] is not None
    status = final_state["status"]
    if status == "PENDING":
        status = "MAX_RETRIES_EXCEEDED"

    return PipelineResult(
        status=status,  # type: ignore[arg-type]
        diff_analysis=final_state["diff_analysis"],
        final_draft=final_state["draft"],
        verification_history=final_state["verification_history"],
        retry_count=final_state["retry_count"],
        metrics=metrics,
        total_latency_ms=wall_ms,
        total_tokens=total_tokens,
        total_cost_usd=total_cost,
    )
