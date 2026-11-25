"""Pluggable LLM provider interface, deterministic offline backend, and token/cost accounting."""

from __future__ import annotations

import math
import os
import re
from typing import Any, Protocol

from src.schemas import FactCheckError, ProviderConfig, SpecParameterChange

# Transparent reference pricing (USD per 1M tokens)
MODEL_PRICING_USD_PER_1M: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "claude-3-5-sonnet": (3.00, 15.00),
    "gemini-2.5-flash": (0.15, 0.60),
}


def estimate_tokens(text: str) -> int:
    """Estimate token count deterministically (~4 chars per token, minimum 1 for non-empty)."""
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))


def estimate_cost_usd(
    prompt_tokens: int, completion_tokens: int, model_name: str = "gpt-4o-mini"
) -> float:
    """Compute estimated USD cost from prompt/completion tokens and model pricing."""
    in_rate, out_rate = MODEL_PRICING_USD_PER_1M.get(model_name, (0.15, 0.60))
    cost = (prompt_tokens / 1_000_000.0) * in_rate + (
        completion_tokens / 1_000_000.0
    ) * out_rate
    return round(cost, 8)


class WriterBackend(Protocol):
    """Protocol for rewriting an affected documentation section."""

    def rewrite_section(
        self,
        language: str,
        section_heading: str,
        original_body: str,
        changes: list[SpecParameterChange],
        prior_errors: list[FactCheckError],
        attempt: int,
    ) -> tuple[str, int, int]:
        """Return (updated_body, prompt_tokens, completion_tokens)."""
        ...


def _format_scalar(val: Any) -> str:
    if isinstance(val, bool):
        return "true" if val else "false"
    return str(val)


def _apply_scalar_replacement(body: str, old_val: Any, new_val: Any) -> str:
    old_str = _format_scalar(old_val)
    new_str = _format_scalar(new_val)
    pattern = rf"(?<![\d.]){re.escape(old_str)}(?![\d.])"
    return re.sub(pattern, new_str, body)


def _append_added_param_row(
    body: str, language: str, change: SpecParameterChange
) -> str:
    """Insert a newly added query parameter row into the Markdown Query Parameters table."""
    if change.parameter_name in body:
        return body

    meta = change.extra_metadata
    if not meta and isinstance(change.new_value, dict):
        meta = change.new_value

    param_type = str(meta.get("type", "string"))
    required = "true" if meta.get("required", False) else "false"
    default_val = str(meta.get("default", "—"))
    allowed = meta.get("allowed_values", [])
    allowed_str = ", ".join(str(x) for x in allowed) if isinstance(allowed, list) and allowed else "—"
    desc = str(meta.get("description", ""))
    if language == "fr" and "Payload compression algorithm" in desc:
        desc = "Algorithme de compression de charge utile pour les rafales haute fréquence"

    new_row = (
        f"| `{change.parameter_name}` | `{param_type}` | `{required}` | "
        f"`{default_val}` | `{allowed_str}` | {desc} |"
    )

    lines = body.splitlines(keepends=True)
    last_table_idx = -1
    for idx, line in enumerate(lines):
        if line.lstrip().startswith("|") and "|" in line[1:]:
            last_table_idx = idx

    if last_table_idx != -1:
        line_ending = "\n" if lines[last_table_idx].endswith("\n") else ""
        if not lines[last_table_idx].endswith("\n"):
            lines[last_table_idx] = lines[last_table_idx] + "\n"
            line_ending = "\n"
        lines.insert(last_table_idx + 1, new_row + line_ending)
        return "".join(lines)

    return body.rstrip("\n") + "\n\n" + new_row + "\n"


def _apply_fault_injections(
    body: str,
    language: str,
    section_heading: str,
    faults: list[str],
) -> str:
    """Inject controlled synthetic faults to test FactCheckerCriticAgent catch & retry loops."""
    if not faults:
        return body

    updated = body
    for fault in faults:
        if fault == "wrong_voltage" and "28.0" in updated:
            updated = re.sub(r"(?<![\d.])28\.0(?![\d.])", "26.5", updated)
        elif fault == "stale_temp" and "85.0" in updated:
            updated = re.sub(r"(?<![\d.])85\.0(?![\d.])", "70.0", updated)
        elif fault == "wrong_freq_unit" and "Hz" in updated:
            updated = updated.replace("`Hz`", "`kHz`").replace(r"\text{Hz}", r"\text{kHz}")
        elif fault == "missing_codec" and "compression_codec" in updated:
            updated = "\n".join(
                line for line in updated.splitlines() if "compression_codec" not in line
            ) + ("\n" if updated.endswith("\n") else "")
        elif fault == "broken_latex" and "$$" in updated:
            updated = updated.replace("$$P_{\\text{max}}", "$P_{\\text{max}}", 1)
        elif fault == "corrupt_companion_current" and "2.5" in updated:
            updated = re.sub(r"(?<![\d.])2\.5(?![\d.])", "3.5", updated)
    return updated


class DeterministicBackend:
    """Zero-dependency, offline deterministic technical writer with optional fault injection."""

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    def rewrite_section(
        self,
        language: str,
        section_heading: str,
        original_body: str,
        changes: list[SpecParameterChange],
        prior_errors: list[FactCheckError],
        attempt: int,
    ) -> tuple[str, int, int]:
        updated = original_body
        for change in changes:
            if change.change_type.value == "modified" and change.old_value is not None:
                updated = _apply_scalar_replacement(updated, change.old_value, change.new_value)
            elif change.change_type.value == "added":
                updated = _append_added_param_row(updated, language, change)

        active_faults = self.config.fault_injection.get(attempt, [])
        if active_faults:
            updated = _apply_fault_injections(updated, language, section_heading, active_faults)

        prompt_context = (
            f"LANG={language}\nHEADING={section_heading}\n"
            f"CHANGES={[c.model_dump() for c in changes]}\n"
            f"ERRORS={[e.model_dump() for e in prior_errors]}\n"
            f"BODY={original_body}"
        )
        prompt_tokens = estimate_tokens(prompt_context)
        completion_tokens = estimate_tokens(updated)
        return updated, prompt_tokens, completion_tokens


class LiveLLMBackend:
    """Optional live LLM adapter (OpenAI / Anthropic / Gemini) with deterministic fallback."""

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config
        self._fallback = DeterministicBackend(config)

    def _build_prompt(
        self,
        language: str,
        section_heading: str,
        original_body: str,
        changes: list[SpecParameterChange],
        prior_errors: list[FactCheckError],
    ) -> str:
        error_block = (
            "\n".join(f"- [{e.error_code}] {e.message}" for e in prior_errors)
            if prior_errors
            else "None"
        )
        changes_block = "\n".join(
            f"- {c.yaml_path}: {c.old_value} -> {c.new_value} (unit={c.unit}, meta={c.extra_metadata})"
            for c in changes
        )
        return (
            f"You are updating ONLY one Markdown section ({section_heading}) in language '{language}'.\n"
            f"Preserve all Markdown tables, LaTeX math ($...$ and $$...$$), and untouched numeric values exactly.\n"
            f"Changes to apply:\n{changes_block}\n\n"
            f"Critic errors from previous attempt to fix:\n{error_block}\n\n"
            f"Return ONLY the updated section body (do not repeat the ## heading).\n\n"
            f"Original section body:\n{original_body}"
        )

    def rewrite_section(
        self,
        language: str,
        section_heading: str,
        original_body: str,
        changes: list[SpecParameterChange],
        prior_errors: list[FactCheckError],
        attempt: int,
    ) -> tuple[str, int, int]:
        prompt = self._build_prompt(
            language, section_heading, original_body, changes, prior_errors
        )
        if self.config.provider == "openai" and os.getenv("OPENAI_API_KEY"):
            from openai import OpenAI  # type: ignore[import-not-found]

            client = OpenAI()
            resp = client.chat.completions.create(
                model=self.config.model_name,
                temperature=0.0,
                messages=[{"role": "user", "content": prompt}],
            )
            content = resp.choices[0].message.content or original_body
            p_tok = getattr(resp.usage, "prompt_tokens", estimate_tokens(prompt))
            c_tok = getattr(resp.usage, "completion_tokens", estimate_tokens(content))
            return content, int(p_tok), int(c_tok)

        if self.config.provider == "anthropic" and os.getenv("ANTHROPIC_API_KEY"):
            from anthropic import Anthropic  # type: ignore[import-not-found]

            client = Anthropic()
            resp = client.messages.create(
                model=self.config.model_name,
                max_tokens=2048,
                temperature=0.0,
                messages=[{"role": "user", "content": prompt}],
            )
            content = resp.content[0].text if resp.content else original_body
            p_tok = getattr(resp.usage, "input_tokens", estimate_tokens(prompt))
            c_tok = getattr(resp.usage, "output_tokens", estimate_tokens(content))
            return content, int(p_tok), int(c_tok)

        if self.config.provider == "gemini" and os.getenv("GEMINI_API_KEY"):
            from google import genai  # type: ignore[import-not-found]

            client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
            resp = client.models.generate_content(
                model=self.config.model_name,
                contents=prompt,
            )
            content = resp.text or original_body
            return content, estimate_tokens(prompt), estimate_tokens(content)

        # Safe fallback if key is missing
        return self._fallback.rewrite_section(
            language, section_heading, original_body, changes, prior_errors, attempt
        )


def create_writer_backend(config: ProviderConfig) -> WriterBackend:
    """Factory returning deterministic or live LLM writer backend."""
    if config.provider == "deterministic":
        return DeterministicBackend(config)
    return LiveLLMBackend(config)
