"""Deterministic unit tests for spec_validator and FactCheckerCriticAgent."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.agents.diff_analyzer import DiffAnalyzerAgent
from src.agents.technical_writer import TechnicalWriterAgent
from src.schemas import DocumentationDraft, ProviderConfig
from src.spec_validator import (
    reassemble_markdown_sections,
    split_markdown_sections,
    validate_latex_blocks,
    verify_draft_against_spec,
)

DATA_DIR = Path("data")


@pytest.fixture
def clean_artifacts():
    spec_data = yaml.safe_load((DATA_DIR / "api_and_hardware_specs.yaml").read_text(encoding="utf-8"))
    diff_text = (DATA_DIR / "changes.diff").read_text(encoding="utf-8")
    manual_en = (DATA_DIR / "docs" / "user_manual_en.md").read_text(encoding="utf-8")
    manual_fr = (DATA_DIR / "docs" / "user_manual_fr.md").read_text(encoding="utf-8")

    analysis, _ = DiffAnalyzerAgent().analyze(diff_text, spec_data, manual_en, manual_fr)
    draft, _ = TechnicalWriterAgent().write(manual_en, manual_fr, analysis, attempt=1)
    return spec_data, manual_en, manual_fr, analysis, draft


def test_markdown_section_split_round_trip(clean_artifacts) -> None:
    _, manual_en, manual_fr, _, _ = clean_artifacts
    for manual in (manual_en, manual_fr):
        preamble, sections = split_markdown_sections(manual)
        assert len(sections) == 6
        assert reassemble_markdown_sections(preamble, sections) == manual


def test_validate_latex_blocks_detects_unbalanced_delimiters() -> None:
    assert validate_latex_blocks("Inline $V = 28.0$ and display $$P = V \\times I$$") == []
    assert len(validate_latex_blocks("Broken inline $V = 28.0")) == 1
    assert len(validate_latex_blocks("Broken display $$P = V \\times I")) == 1
    assert len(validate_latex_blocks("Unbalanced braces $V_{\\text{nom} = 28.0$")) == 1


def test_valid_draft_passes_with_zero_hallucinations(clean_artifacts) -> None:
    spec_data, manual_en, manual_fr, analysis, draft = clean_artifacts
    report = verify_draft_against_spec(manual_en, manual_fr, draft, analysis, spec_data, attempt=1)

    assert report.approved is True
    assert report.checked_parameters == 5
    assert report.numeric_hallucinations_found == 0
    assert report.errors == []


def test_validator_rejects_wrong_numeric_value(clean_artifacts) -> None:
    spec_data, manual_en, manual_fr, analysis, draft = clean_artifacts
    corrupted = DocumentationDraft(
        revision=1,
        updated_manual_en=draft.updated_manual_en.replace("28.0", "26.5"),
        updated_manual_fr=draft.updated_manual_fr,
        section_rewrites=draft.section_rewrites,
    )
    report = verify_draft_against_spec(manual_en, manual_fr, corrupted, analysis, spec_data)

    assert report.approved is False
    assert report.numeric_hallucinations_found >= 1
    assert any(e.error_code == "VALUE_MISMATCH" and e.language == "en" for e in report.errors)


def test_validator_rejects_wrong_unit(clean_artifacts) -> None:
    spec_data, manual_en, manual_fr, analysis, draft = clean_artifacts
    corrupted = DocumentationDraft(
        revision=1,
        updated_manual_en=draft.updated_manual_en.replace("`Hz`", "`kHz`").replace(
            r"\text{Hz}", r"\text{kHz}"
        ),
        updated_manual_fr=draft.updated_manual_fr,
        section_rewrites=draft.section_rewrites,
    )
    report = verify_draft_against_spec(manual_en, manual_fr, corrupted, analysis, spec_data)

    assert report.approved is False
    assert report.numeric_hallucinations_found >= 1
    assert any(e.error_code == "UNIT_MISMATCH" and e.language == "en" for e in report.errors)


def test_validator_rejects_stale_old_value_remaining(clean_artifacts) -> None:
    spec_data, manual_en, manual_fr, analysis, draft = clean_artifacts
    corrupted = DocumentationDraft(
        revision=1,
        updated_manual_en=draft.updated_manual_en,
        updated_manual_fr=draft.updated_manual_fr.replace("85.0", "70.0"),
        section_rewrites=draft.section_rewrites,
    )
    report = verify_draft_against_spec(manual_en, manual_fr, corrupted, analysis, spec_data)

    assert report.approved is False
    assert any(
        e.error_code == "STALE_VALUE_REMAINING" and e.language == "fr" for e in report.errors
    )


def test_validator_rejects_missing_added_api_parameter(clean_artifacts) -> None:
    spec_data, manual_en, manual_fr, analysis, draft = clean_artifacts
    stripped_en = "\n".join(
        line for line in draft.updated_manual_en.splitlines() if "compression_codec" not in line
    ) + "\n"
    corrupted = DocumentationDraft(
        revision=1,
        updated_manual_en=stripped_en,
        updated_manual_fr=draft.updated_manual_fr,
        section_rewrites=draft.section_rewrites,
    )
    report = verify_draft_against_spec(manual_en, manual_fr, corrupted, analysis, spec_data)

    assert report.approved is False
    assert any(e.error_code == "MISSING_PARAMETER" and e.language == "en" for e in report.errors)


def test_validator_rejects_corrupted_companion_value(clean_artifacts) -> None:
    spec_data, manual_en, manual_fr, analysis, draft = clean_artifacts
    corrupted = DocumentationDraft(
        revision=1,
        updated_manual_en=draft.updated_manual_en.replace("2.5", "3.5"),
        updated_manual_fr=draft.updated_manual_fr,
        section_rewrites=draft.section_rewrites,
    )
    report = verify_draft_against_spec(manual_en, manual_fr, corrupted, analysis, spec_data)

    assert report.approved is False
    assert any(
        e.error_code == "SPEC_VALUE_HALLUCINATION"
        and e.yaml_path == "hardware.power.max_current_a"
        for e in report.errors
    )


def test_validator_rejects_broken_latex(clean_artifacts) -> None:
    spec_data, manual_en, manual_fr, analysis, draft = clean_artifacts
    corrupted = DocumentationDraft(
        revision=1,
        updated_manual_en=draft.updated_manual_en.replace("$$P_{\\text{max}}", "$P_{\\text{max}}", 1),
        updated_manual_fr=draft.updated_manual_fr,
        section_rewrites=draft.section_rewrites,
    )
    report = verify_draft_against_spec(manual_en, manual_fr, corrupted, analysis, spec_data)

    assert report.approved is False
    assert any(e.error_code == "LATEX_SYNTAX_BROKEN" for e in report.errors)


def test_validator_rejects_modified_untouched_section(clean_artifacts) -> None:
    spec_data, manual_en, manual_fr, analysis, draft = clean_artifacts
    corrupted = DocumentationDraft(
        revision=1,
        updated_manual_en=draft.updated_manual_en.replace("1.45 kg", "1.99 kg"),
        updated_manual_fr=draft.updated_manual_fr,
        section_rewrites=draft.section_rewrites,
    )
    report = verify_draft_against_spec(manual_en, manual_fr, corrupted, analysis, spec_data)

    assert report.approved is False
    assert any(e.error_code == "UNTOUCHED_SECTION_MODIFIED" for e in report.errors)
