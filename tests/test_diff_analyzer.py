"""Unit tests for Subagent 1: DiffAnalyzerAgent."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from src.agents.diff_analyzer import DiffAnalyzerAgent
from src.schemas import ChangeType, DiffAnalysisReport

DATA_DIR = Path("data")


@pytest.fixture
def repo_inputs():
    spec_data = yaml.safe_load((DATA_DIR / "api_and_hardware_specs.yaml").read_text(encoding="utf-8"))
    diff_text = (DATA_DIR / "changes.diff").read_text(encoding="utf-8")
    manual_en = (DATA_DIR / "docs" / "user_manual_en.md").read_text(encoding="utf-8")
    manual_fr = (DATA_DIR / "docs" / "user_manual_fr.md").read_text(encoding="utf-8")
    return spec_data, diff_text, manual_en, manual_fr


def test_diff_analyzer_extracts_all_5_critical_changes(repo_inputs) -> None:
    spec_data, diff_text, manual_en, manual_fr = repo_inputs
    agent = DiffAnalyzerAgent()
    report, metric = agent.analyze(diff_text, spec_data, manual_en, manual_fr)

    assert report.total_changes == 5
    assert metric.node_name == "diff_analyzer"
    assert metric.prompt_tokens > 0
    assert metric.completion_tokens > 0

    by_path = {c.yaml_path: c for c in report.changes}
    expected_paths = {
        "hardware.power.nominal_voltage_v",
        "hardware.thermal.max_operating_temp_c",
        "hardware.telemetry.sampling_rate_hz",
        "api.endpoints.telemetry_ingest.rate_limit_rpm",
        "api.endpoints.telemetry_ingest.query_params.compression_codec",
    }
    assert set(by_path.keys()) == expected_paths

    volt = by_path["hardware.power.nominal_voltage_v"]
    assert volt.change_type == ChangeType.MODIFIED
    assert volt.old_value == 24.0
    assert volt.new_value == 28.0
    assert volt.unit == "V DC"
    assert volt.affected_sections_en == ["## 2. Electrical & Power Specifications"]
    assert volt.affected_sections_fr == ["## 2. Spécifications Électriques et d'Alimentation"]

    temp = by_path["hardware.thermal.max_operating_temp_c"]
    assert temp.change_type == ChangeType.MODIFIED
    assert temp.old_value == 70.0
    assert temp.new_value == 85.0
    assert temp.unit == "°C"
    assert temp.affected_sections_en == ["## 3. Environmental & Thermal Limits"]
    assert temp.affected_sections_fr == ["## 3. Limites Environnementales et Thermiques"]

    rate = by_path["hardware.telemetry.sampling_rate_hz"]
    assert rate.change_type == ChangeType.MODIFIED
    assert rate.old_value == 500
    assert rate.new_value == 1000
    assert rate.unit == "Hz"
    assert rate.affected_sections_en == ["## 4. Telemetry Acquisition & Filtering"]
    assert rate.affected_sections_fr == ["## 4. Acquisition Télémétrique et Filtrage"]

    rpm = by_path["api.endpoints.telemetry_ingest.rate_limit_rpm"]
    assert rpm.change_type == ChangeType.MODIFIED
    assert rpm.old_value == 120
    assert rpm.new_value == 300
    assert rpm.unit == "req/min"
    assert rpm.endpoint == "/v2/telemetry/ingest"
    assert rpm.affected_sections_en == ["## 5. REST API Reference"]
    assert rpm.affected_sections_fr == ["## 5. Référence API REST"]

    codec = by_path["api.endpoints.telemetry_ingest.query_params.compression_codec"]
    assert codec.change_type == ChangeType.ADDED
    assert codec.extra_metadata["default"] == "zstd"
    assert codec.extra_metadata["allowed_values"] == ["none", "gzip", "zstd"]
    assert codec.affected_sections_en == ["## 5. REST API Reference"]
    assert codec.affected_sections_fr == ["## 5. Référence API REST"]


def test_diff_analyzer_handles_empty_diff(repo_inputs) -> None:
    spec_data, _, manual_en, manual_fr = repo_inputs
    agent = DiffAnalyzerAgent()
    report, _ = agent.analyze("", spec_data, manual_en, manual_fr)

    assert report.total_changes == 0
    assert report.changes == []
    assert report.affected_manuals == []


def test_diff_analysis_report_enforces_count_invariant() -> None:
    with pytest.raises(ValidationError):
        DiffAnalysisReport(
            source_spec_file="api_and_hardware_specs.yaml",
            total_changes=2,
            changes=[],
            affected_manuals=[],
        )
