"""Benchmark suite measuring Change Detection Recall, Numeric Hallucination Rate, Latency, and Token Cost."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from rich.console import Console
from rich.table import Table

from src.graph import run_drift_guard_from_strings
from src.schemas import PipelineResult, ProviderConfig


@dataclass(frozen=True)
class BenchmarkScenario:
    """Definition of a benchmark evaluation scenario."""

    name: str
    description: str
    use_empty_diff: bool
    expected_changes: int
    expected_status: str
    fault_injection: dict[int, list[str]]


SCENARIOS: list[BenchmarkScenario] = [
    BenchmarkScenario(
        name="baseline_5_critical_changes",
        description="5 critical YAML changes across power, thermal, telemetry, and REST API (EN + FR)",
        use_empty_diff=False,
        expected_changes=5,
        expected_status="APPROVED",
        fault_injection={},
    ),
    BenchmarkScenario(
        name="self_healing_numeric_hallucination",
        description="Injects wrong voltage (26.5 V DC) + stale temp (70.0 °C) on attempt 1; critic forces repair",
        use_empty_diff=False,
        expected_changes=5,
        expected_status="APPROVED",
        fault_injection={1: ["wrong_voltage", "stale_temp"]},
    ),
    BenchmarkScenario(
        name="self_healing_multi_retry_unit_and_api",
        description="Injects unit error (kHz) on attempt 1 + missing compression_codec on attempt 2; converges on retry 2",
        use_empty_diff=False,
        expected_changes=5,
        expected_status="APPROVED",
        fault_injection={1: ["wrong_freq_unit"], 2: ["missing_codec"]},
    ),
    BenchmarkScenario(
        name="zero_drift_empty_diff",
        description="Empty diff short-circuits to NO_DRIFT with zero false positives and untouched manuals",
        use_empty_diff=True,
        expected_changes=0,
        expected_status="NO_DRIFT",
        fault_injection={},
    ),
    BenchmarkScenario(
        name="safety_guard_max_retries_exceeded",
        description="Persistent numeric hallucination across all 4 attempts; critic blocks approval at max_retries=3",
        use_empty_diff=False,
        expected_changes=5,
        expected_status="MAX_RETRIES_EXCEEDED",
        fault_injection={
            1: ["wrong_voltage"],
            2: ["wrong_voltage"],
            3: ["wrong_voltage"],
            4: ["wrong_voltage"],
        },
    ),
]


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, math.ceil((pct / 100.0) * len(ordered)) - 1))
    return ordered[rank]


def run_benchmark(
    data_dir: Path = Path("data"),
    output_json: Path = Path("evals/benchmark_results.json"),
    iterations: int = 5,
    model_name: str = "gpt-4o-mini",
) -> dict[str, Any]:
    """Run all benchmark scenarios and persist metrics to JSON."""
    spec_data = yaml.safe_load((data_dir / "api_and_hardware_specs.yaml").read_text(encoding="utf-8"))
    diff_text = (data_dir / "changes.diff").read_text(encoding="utf-8")
    manual_en = (data_dir / "docs" / "user_manual_en.md").read_text(encoding="utf-8")
    manual_fr = (data_dir / "docs" / "user_manual_fr.md").read_text(encoding="utf-8")

    scenario_reports: list[dict[str, Any]] = []

    for sc in SCENARIOS:
        latencies: list[float] = []
        last_result: PipelineResult | None = None
        sc_diff = "" if sc.use_empty_diff else diff_text
        cfg = ProviderConfig(
            provider="deterministic",
            model_name=model_name,
            max_retries=3,
            fault_injection=sc.fault_injection,
        )

        for _ in range(iterations):
            res = run_drift_guard_from_strings(
                spec_data=spec_data,
                diff_text=sc_diff,
                manual_en=manual_en,
                manual_fr=manual_fr,
                source_spec_file="api_and_hardware_specs.yaml",
                config=cfg,
            )
            latencies.append(res.total_latency_ms)
            last_result = res

        assert last_result is not None
        detected = last_result.diff_analysis.total_changes
        if sc.expected_changes > 0:
            recall_pct = round(min(100.0, (detected / sc.expected_changes) * 100.0), 2)
        else:
            recall_pct = 100.0 if detected == 0 else 0.0

        latest_ver = (
            last_result.verification_history[-1]
            if last_result.verification_history
            else None
        )
        initial_halluc = (
            last_result.verification_history[0].numeric_hallucinations_found
            if last_result.verification_history
            else 0
        )
        final_halluc = latest_ver.numeric_hallucinations_found if latest_ver else 0
        checked_slots = max(1, (latest_ver.checked_parameters * 2) if latest_ver else 1)

        # Published output hallucination rate: 0% whenever critic approves or blocks unsafe draft
        published_halluc_rate_pct = (
            round((final_halluc / checked_slots) * 100.0, 2)
            if last_result.status == "APPROVED"
            else 0.0
        )

        scenario_reports.append(
            {
                "scenario": sc.name,
                "description": sc.description,
                "expected_status": sc.expected_status,
                "actual_status": last_result.status,
                "expected_changes": sc.expected_changes,
                "detected_changes": detected,
                "recall_pct": recall_pct,
                "initial_hallucinations_caught": initial_halluc,
                "final_hallucinations_in_approved_output": (
                    final_halluc if last_result.status == "APPROVED" else 0
                ),
                "published_hallucination_rate_pct": published_halluc_rate_pct,
                "retries_used": last_result.retry_count,
                "mean_latency_ms": round(sum(latencies) / len(latencies), 3),
                "p95_latency_ms": round(_percentile(latencies, 95.0), 3),
                "total_tokens": last_result.total_tokens,
                "estimated_cost_usd": last_result.total_cost_usd,
            }
        )

    drift_scenarios = [r for r in scenario_reports if r["expected_changes"] > 0]
    avg_recall = round(
        sum(r["recall_pct"] for r in drift_scenarios) / len(drift_scenarios), 2
    )
    avg_published_halluc_rate = round(
        sum(r["published_hallucination_rate_pct"] for r in scenario_reports)
        / len(scenario_reports),
        2,
    )
    avg_latency_ms = round(
        sum(r["mean_latency_ms"] for r in scenario_reports) / len(scenario_reports), 3
    )
    total_tokens_all = sum(r["total_tokens"] for r in scenario_reports)
    total_cost_all = round(sum(r["estimated_cost_usd"] for r in scenario_reports), 8)

    summary = {
        "model_pricing_reference": model_name,
        "iterations_per_scenario": iterations,
        "aggregate": {
            "change_detection_recall_pct": avg_recall,
            "published_numeric_hallucination_rate_pct": avg_published_halluc_rate,
            "mean_latency_ms": avg_latency_ms,
            "combined_tokens_across_5_scenarios": total_tokens_all,
            "combined_cost_usd_across_5_scenarios": total_cost_all,
        },
        "scenarios": scenario_reports,
    }

    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    summary = run_benchmark()
    console = Console(legacy_windows=False)

    table = Table(title="Multi-Agent Doc Drift Guard - Benchmark Results")
    table.add_column("Scenario", style="bold cyan")
    table.add_column("Status", style="green")
    table.add_column("Recall (%)", justify="right")
    table.add_column("Pub. Halluc (%)", justify="right")
    table.add_column("Retries", justify="right")
    table.add_column("Mean Lat (ms)", justify="right")
    table.add_column("P95 Lat (ms)", justify="right")
    table.add_column("Tokens", justify="right")
    table.add_column("Est. Cost (USD)", justify="right")

    for row in summary["scenarios"]:
        table.add_row(
            row["scenario"],
            row["actual_status"],
            f"{row['recall_pct']:.1f}%",
            f"{row['published_hallucination_rate_pct']:.1f}%",
            str(row["retries_used"]),
            f"{row['mean_latency_ms']:.3f}",
            f"{row['p95_latency_ms']:.3f}",
            str(row["total_tokens"]),
            f"${row['estimated_cost_usd']:.6f}",
        )

    console.print(table)
    agg = summary["aggregate"]
    console.print(
        f"\nAggregate Recall: {agg['change_detection_recall_pct']:.1f}% | "
        f"Published Hallucination Rate: {agg['published_numeric_hallucination_rate_pct']:.1f}% | "
        f"Mean Latency: {agg['mean_latency_ms']:.3f} ms | "
        f"Combined Cost: ${agg['combined_cost_usd_across_5_scenarios']:.6f}"
    )


if __name__ == "__main__":
    main()
