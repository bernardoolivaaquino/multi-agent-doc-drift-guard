"""End-to-end integration tests for the compiled LangGraph workflow."""

from __future__ import annotations

from pathlib import Path

import yaml

from src.graph import run_drift_guard, run_drift_guard_from_strings
from src.schemas import ProviderConfig
from src.spec_validator import split_markdown_sections

DATA_DIR = Path("data")


def test_end_to_end_langgraph_baseline_approval() -> None:
    result = run_drift_guard(
        spec_path=DATA_DIR / "api_and_hardware_specs.yaml",
        diff_path=DATA_DIR / "changes.diff",
        manual_en_path=DATA_DIR / "docs" / "user_manual_en.md",
        manual_fr_path=DATA_DIR / "docs" / "user_manual_fr.md",
    )

    assert result.status == "APPROVED"
    assert result.diff_analysis.total_changes == 5
    assert result.retry_count == 0
    assert len(result.verification_history) == 1
    assert result.verification_history[0].approved is True
    assert result.verification_history[0].numeric_hallucinations_found == 0
    assert result.final_draft is not None

    # Untouched sections (1 & 6) must remain byte-for-byte identical
    orig_en = (DATA_DIR / "docs" / "user_manual_en.md").read_text(encoding="utf-8")
    orig_fr = (DATA_DIR / "docs" / "user_manual_fr.md").read_text(encoding="utf-8")
    _, orig_en_secs = split_markdown_sections(orig_en)
    _, upd_en_secs = split_markdown_sections(result.final_draft.updated_manual_en)
    _, orig_fr_secs = split_markdown_sections(orig_fr)
    _, upd_fr_secs = split_markdown_sections(result.final_draft.updated_manual_fr)

    assert orig_en_secs[0] == upd_en_secs[0]
    assert orig_en_secs[5] == upd_en_secs[5]
    assert orig_fr_secs[0] == upd_fr_secs[0]
    assert orig_fr_secs[5] == upd_fr_secs[5]


def test_langgraph_self_heals_via_critic_retry_loop() -> None:
    cfg = ProviderConfig(
        max_retries=3,
        fault_injection={
            1: ["wrong_voltage", "stale_temp"],
            2: ["wrong_freq_unit", "missing_codec"],
        },
    )
    result = run_drift_guard(
        spec_path=DATA_DIR / "api_and_hardware_specs.yaml",
        diff_path=DATA_DIR / "changes.diff",
        manual_en_path=DATA_DIR / "docs" / "user_manual_en.md",
        manual_fr_path=DATA_DIR / "docs" / "user_manual_fr.md",
        config=cfg,
    )

    assert result.status == "APPROVED"
    assert result.retry_count == 2
    assert len(result.verification_history) == 3
    assert result.verification_history[0].approved is False
    assert result.verification_history[1].approved is False
    assert result.verification_history[2].approved is True
    assert result.verification_history[2].numeric_hallucinations_found == 0


def test_langgraph_enforces_max_3_retries() -> None:
    cfg = ProviderConfig(
        max_retries=3,
        fault_injection={
            1: ["wrong_voltage"],
            2: ["wrong_voltage"],
            3: ["wrong_voltage"],
            4: ["wrong_voltage"],
        },
    )
    result = run_drift_guard(
        spec_path=DATA_DIR / "api_and_hardware_specs.yaml",
        diff_path=DATA_DIR / "changes.diff",
        manual_en_path=DATA_DIR / "docs" / "user_manual_en.md",
        manual_fr_path=DATA_DIR / "docs" / "user_manual_fr.md",
        config=cfg,
    )

    assert result.status == "MAX_RETRIES_EXCEEDED"
    assert result.retry_count == 3
    assert len(result.verification_history) == 4
    assert all(not v.approved for v in result.verification_history)


def test_langgraph_short_circuits_on_empty_diff() -> None:
    spec_data = yaml.safe_load((DATA_DIR / "api_and_hardware_specs.yaml").read_text(encoding="utf-8"))
    manual_en = (DATA_DIR / "docs" / "user_manual_en.md").read_text(encoding="utf-8")
    manual_fr = (DATA_DIR / "docs" / "user_manual_fr.md").read_text(encoding="utf-8")

    result = run_drift_guard_from_strings(
        spec_data=spec_data,
        diff_text="",
        manual_en=manual_en,
        manual_fr=manual_fr,
    )

    assert result.status == "NO_DRIFT"
    assert result.diff_analysis.total_changes == 0
    assert result.final_draft is None
    assert result.verification_history == []
    assert result.retry_count == 0
