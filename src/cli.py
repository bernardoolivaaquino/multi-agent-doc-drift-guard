"""Typer + Rich CLI for Multi-Agent Doc Drift Guard."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from src.graph import run_drift_guard
from src.schemas import ProviderConfig

load_dotenv()

app = typer.Typer(
    name="doc-drift-guard",
    help="Detect technical spec drift from diffs and repair bilingual Markdown/LaTeX manuals with critic verification.",
    add_completion=False,
)
console = Console(legacy_windows=False)


def _render_summary(result) -> None:
    table = Table(title="Multi-Agent Doc Drift Guard - Execution Summary")
    table.add_column("Metric", style="bold cyan")
    table.add_column("Value", style="white")

    latest_ver = result.verification_history[-1] if result.verification_history else None
    hallucinations = latest_ver.numeric_hallucinations_found if latest_ver else 0

    table.add_row("Final Status", result.status)
    table.add_row("Changes Detected", str(result.diff_analysis.total_changes))
    table.add_row("Retries Used", str(result.retry_count))
    table.add_row("Numeric Hallucinations", str(hallucinations))
    table.add_row("Total Latency (ms)", f"{result.total_latency_ms:.3f}")
    table.add_row("Total Tokens", str(result.total_tokens))
    table.add_row("Estimated Cost (USD)", f"${result.total_cost_usd:.6f}")
    console.print(table)


@app.command()
def run(
    spec: Path = typer.Option(Path("data/api_and_hardware_specs.yaml"), help="Path to YAML ground-truth specs."),
    diff: Path = typer.Option(Path("data/changes.diff"), help="Path to unified diff file."),
    manual_en: Path = typer.Option(Path("data/docs/user_manual_en.md"), help="Path to EN manual."),
    manual_fr: Path = typer.Option(Path("data/docs/user_manual_fr.md"), help="Path to FR manual."),
    output_dir: Path = typer.Option(Path("output"), help="Directory for updated manuals and audit report."),
    provider: Literal["deterministic", "openai", "anthropic", "gemini"] = typer.Option(
        os.getenv("DRIFT_GUARD_PROVIDER", "deterministic"),  # type: ignore[arg-type]
        help="Writer backend provider.",
    ),
    model: str = typer.Option(os.getenv("DRIFT_GUARD_MODEL", "gpt-4o-mini"), help="Model identifier."),
    max_retries: int = typer.Option(3, help="Maximum critic-writer retry iterations."),
) -> None:
    """Run the LangGraph drift-guard workflow and write updated manuals + JSON report."""
    config = ProviderConfig(provider=provider, model_name=model, max_retries=max_retries)
    result = run_drift_guard(spec, diff, manual_en, manual_fr, config=config)

    output_dir.mkdir(parents=True, exist_ok=True)
    if result.final_draft is not None:
        (output_dir / "user_manual_en.md").write_text(
            result.final_draft.updated_manual_en, encoding="utf-8"
        )
        (output_dir / "user_manual_fr.md").write_text(
            result.final_draft.updated_manual_fr, encoding="utf-8"
        )
    (output_dir / "drift_guard_report.json").write_text(
        result.model_dump_json(indent=2), encoding="utf-8"
    )

    _render_summary(result)
    if result.status == "MAX_RETRIES_EXCEEDED":
        raise typer.Exit(code=1)


@app.command()
def check(
    spec: Path = typer.Option(Path("data/api_and_hardware_specs.yaml"), help="Path to YAML ground-truth specs."),
    diff: Path = typer.Option(Path("data/changes.diff"), help="Path to unified diff file."),
    manual_en: Path = typer.Option(Path("data/docs/user_manual_en.md"), help="Path to EN manual."),
    manual_fr: Path = typer.Option(Path("data/docs/user_manual_fr.md"), help="Path to FR manual."),
    max_retries: int = typer.Option(3, help="Maximum critic-writer retry iterations."),
) -> None:
    """Verify drift resolution without writing output files (CI/CD gate)."""
    config = ProviderConfig(max_retries=max_retries)
    result = run_drift_guard(spec, diff, manual_en, manual_fr, config=config)
    _render_summary(result)
    if result.status == "MAX_RETRIES_EXCEEDED":
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
