"""Subagent 1: DiffAnalyzerAgent — parses unified diffs into Pydantic SpecParameterChange lists."""

from __future__ import annotations

import re
import time
from typing import Any, Optional

import yaml

from src.llm_provider import estimate_cost_usd, estimate_tokens
from src.schemas import (
    AgentStepMetric,
    ChangeType,
    DiffAnalysisReport,
    ProviderConfig,
    SpecParameterChange,
)
from src.spec_validator import resolve_unit_for_path, split_markdown_sections

_KEY_VAL_RE = re.compile(r"^(\s*)([A-Za-z0-9_.-]+):\s*(.*)$")


def _parse_yaml_scalar(raw: str) -> Any:
    raw_stripped = raw.strip()
    if not raw_stripped:
        return None
    try:
        return yaml.safe_load(raw_stripped)
    except yaml.YAMLError:
        return raw_stripped.strip("\"'")


def _update_stack(stack: list[tuple[int, str]], indent: int, key: str) -> list[tuple[int, str]]:
    new_stack = [(i, k) for (i, k) in stack if i < indent]
    new_stack.append((indent, key))
    return new_stack


def _map_sections_for_change(
    yaml_path: str,
    old_value: Any,
    endpoint: Optional[str],
    manual_en: str,
    manual_fr: str,
) -> tuple[list[str], list[str]]:
    """Map a YAML parameter change to exact affected EN and FR Markdown headings."""
    _, en_sections = split_markdown_sections(manual_en)
    _, fr_sections = split_markdown_sections(manual_fr)

    domain_keywords: dict[str, tuple[str, ...]] = {
        "hardware.power": ("power", "electrical", "voltage", "électrique", "alimentation", "tension"),
        "hardware.thermal": ("thermal", "environmental", "temp", "thermique", "environnemental"),
        "hardware.telemetry": ("telemetry", "acquisition", "sampling", "télémétrique", "échantillonnage"),
        "hardware.mechanical": ("mechanical", "mounting", "torque", "mécanique", "montage"),
        "api": ("api", "rest", "endpoint", "ingest", "référence"),
    }

    matched_keywords: tuple[str, ...] = ()
    for prefix, kws in domain_keywords.items():
        if yaml_path.startswith(prefix):
            matched_keywords = kws
            break

    def _find_matching_headings(sections: list[tuple[str, str]]) -> list[str]:
        matched: list[str] = []
        old_token = str(old_value) if old_value is not None else ""
        for heading, body in sections:
            haystack = f"{heading}\n{body}".lower()
            if old_token and re.search(rf"(?<![\d.]){re.escape(old_token)}(?![\d.])", body):
                matched.append(heading)
            elif endpoint and endpoint.lower() in haystack:
                matched.append(heading)
            elif matched_keywords and any(kw in heading.lower() for kw in matched_keywords):
                matched.append(heading)
        return matched

    return _find_matching_headings(en_sections), _find_matching_headings(fr_sections)


class DiffAnalyzerAgent:
    """Parses unified specification diffs and maps technical drift to manual sections."""

    def __init__(self, config: Optional[ProviderConfig] = None) -> None:
        self.config = config or ProviderConfig()

    def analyze(
        self,
        diff_text: str,
        spec_data: dict[str, Any],
        manual_en: str,
        manual_fr: str,
        source_spec_file: str = "api_and_hardware_specs.yaml",
    ) -> tuple[DiffAnalysisReport, AgentStepMetric]:
        t0 = time.perf_counter()

        if not diff_text.strip():
            elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 3)
            report = DiffAnalysisReport(
                source_spec_file=source_spec_file,
                total_changes=0,
                changes=[],
                affected_manuals=[],
            )
            metric = AgentStepMetric(
                node_name="diff_analyzer",
                attempt=1,
                latency_ms=elapsed_ms,
                prompt_tokens=estimate_tokens(diff_text),
                completion_tokens=estimate_tokens(report.model_dump_json()),
                estimated_cost_usd=estimate_cost_usd(
                    estimate_tokens(diff_text),
                    estimate_tokens(report.model_dump_json()),
                    self.config.model_name,
                ),
            )
            return report, metric

        changes: list[SpecParameterChange] = []
        stack: list[tuple[int, str]] = []
        pending_removals: dict[str, tuple[Any, Optional[str]]] = {}
        added_blocks: dict[str, dict[str, Any]] = {}
        active_added_root: Optional[tuple[int, str]] = None

        lines = diff_text.splitlines()
        for raw_line in lines:
            if raw_line.startswith(("diff ", "index ", "--- ", "+++ ", "@@ ")):
                if raw_line.startswith("@@ "):
                    # Reset active added block across hunks; preserve stack only inside hunk
                    active_added_root = None
                continue

            if not raw_line:
                continue

            prefix = raw_line[0]
            content = raw_line[1:] if prefix in (" ", "-", "+") else raw_line

            # List items inside an added YAML block (e.g. allowed_values)
            if prefix == "+" and content.lstrip().startswith("- ") and active_added_root is not None:
                root_indent, root_path = active_added_root
                list_val = _parse_yaml_scalar(content.lstrip()[2:])
                # Attach to last key inside added_blocks[root_path]
                if root_path in added_blocks and added_blocks[root_path]:
                    last_subkey = list(added_blocks[root_path].keys())[-1]
                    if not isinstance(added_blocks[root_path][last_subkey], list):
                        added_blocks[root_path][last_subkey] = []
                    added_blocks[root_path][last_subkey].append(list_val)
                continue

            kv_match = _KEY_VAL_RE.match(content)
            if not kv_match:
                continue

            indent = len(kv_match.group(1))
            key = kv_match.group(2)
            val_raw = kv_match.group(3).strip()

            if prefix == " ":
                active_added_root = None
                stack = _update_stack(stack, indent, key)
                continue

            if prefix == "-":
                active_added_root = None
                stack = _update_stack(stack, indent, key)
                full_path = ".".join(k for _, k in stack)
                old_val = _parse_yaml_scalar(val_raw)
                pending_removals[full_path] = (old_val, resolve_unit_for_path(spec_data, full_path))
                continue

            if prefix == "+":
                stack = _update_stack(stack, indent, key)
                full_path = ".".join(k for _, k in stack)

                # Paired modification (- old / + new)
                if full_path in pending_removals:
                    old_val, unit = pending_removals.pop(full_path)
                    new_val = _parse_yaml_scalar(val_raw)
                    endpoint = self._infer_endpoint(full_path, spec_data)
                    en_secs, fr_secs = _map_sections_for_change(
                        full_path, old_val, endpoint, manual_en, manual_fr
                    )
                    changes.append(
                        SpecParameterChange(
                            change_id=f"CHG-{len(changes) + 1:03d}",
                            yaml_path=full_path,
                            parameter_name=key,
                            change_type=ChangeType.MODIFIED,
                            old_value=old_val,
                            new_value=new_val,
                            unit=unit,
                            endpoint=endpoint,
                            affected_sections_en=en_secs,
                            affected_sections_fr=fr_secs,
                            severity="critical",
                            rationale=(
                                f"Specification parameter '{full_path}' changed from "
                                f"'{old_val}' to '{new_val}'{f' ({unit})' if unit else ''}."
                            ),
                        )
                    )
                    continue

                # Newly added nested YAML block (e.g. query_params.compression_codec:)
                if val_raw == "" and active_added_root is None:
                    active_added_root = (indent, full_path)
                    added_blocks[full_path] = {}
                elif active_added_root is not None and indent > active_added_root[0]:
                    root_path = active_added_root[1]
                    added_blocks[root_path][key] = _parse_yaml_scalar(val_raw) if val_raw else []
                else:
                    # Standalone added scalar
                    active_added_root = None
                    new_val = _parse_yaml_scalar(val_raw)
                    endpoint = self._infer_endpoint(full_path, spec_data)
                    en_secs, fr_secs = _map_sections_for_change(
                        full_path, None, endpoint, manual_en, manual_fr
                    )
                    changes.append(
                        SpecParameterChange(
                            change_id=f"CHG-{len(changes) + 1:03d}",
                            yaml_path=full_path,
                            parameter_name=key,
                            change_type=ChangeType.ADDED,
                            old_value=None,
                            new_value=new_val,
                            unit=resolve_unit_for_path(spec_data, full_path),
                            endpoint=endpoint,
                            affected_sections_en=en_secs,
                            affected_sections_fr=fr_secs,
                            severity="high",
                            rationale=f"Added new specification field '{full_path}' = '{new_val}'.",
                        )
                    )

        # Finalize any grouped added YAML blocks
        for block_path, metadata in added_blocks.items():
            param_name = block_path.split(".")[-1]
            endpoint = self._infer_endpoint(block_path, spec_data)
            en_secs, fr_secs = _map_sections_for_change(
                block_path, None, endpoint, manual_en, manual_fr
            )
            changes.append(
                SpecParameterChange(
                    change_id=f"CHG-{len(changes) + 1:03d}",
                    yaml_path=block_path,
                    parameter_name=param_name,
                    change_type=ChangeType.ADDED,
                    old_value=None,
                    new_value=metadata,
                    unit=None,
                    endpoint=endpoint,
                    extra_metadata=metadata,
                    affected_sections_en=en_secs,
                    affected_sections_fr=fr_secs,
                    severity="critical",
                    rationale=(
                        f"Added new parameter '{param_name}' at '{block_path}' "
                        f"(default={metadata.get('default')}, allowed={metadata.get('allowed_values')})."
                    ),
                )
            )

        # Finalize any unpaired removals
        for rem_path, (old_val, unit) in pending_removals.items():
            param_name = rem_path.split(".")[-1]
            endpoint = self._infer_endpoint(rem_path, spec_data)
            en_secs, fr_secs = _map_sections_for_change(
                rem_path, old_val, endpoint, manual_en, manual_fr
            )
            changes.append(
                SpecParameterChange(
                    change_id=f"CHG-{len(changes) + 1:03d}",
                    yaml_path=rem_path,
                    parameter_name=param_name,
                    change_type=ChangeType.REMOVED,
                    old_value=old_val,
                    new_value=None,
                    unit=unit,
                    endpoint=endpoint,
                    affected_sections_en=en_secs,
                    affected_sections_fr=fr_secs,
                    severity="high",
                    rationale=f"Removed specification field '{rem_path}' (was '{old_val}').",
                )
            )

        affected_manuals: list[str] = []
        if any(c.affected_sections_en for c in changes):
            affected_manuals.append("docs/user_manual_en.md")
        if any(c.affected_sections_fr for c in changes):
            affected_manuals.append("docs/user_manual_fr.md")

        report = DiffAnalysisReport(
            source_spec_file=source_spec_file,
            total_changes=len(changes),
            changes=changes,
            affected_manuals=affected_manuals,
        )

        p_tok = estimate_tokens(diff_text)
        c_tok = estimate_tokens(report.model_dump_json())
        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 3)
        metric = AgentStepMetric(
            node_name="diff_analyzer",
            attempt=1,
            latency_ms=elapsed_ms,
            prompt_tokens=p_tok,
            completion_tokens=c_tok,
            estimated_cost_usd=estimate_cost_usd(p_tok, c_tok, self.config.model_name),
        )
        return report, metric

    @staticmethod
    def _infer_endpoint(yaml_path: str, spec_data: dict[str, Any]) -> Optional[str]:
        parts = yaml_path.split(".")
        if len(parts) >= 3 and parts[0] == "api" and parts[1] == "endpoints":
            ep_key = parts[2]
            ep_obj = spec_data.get("api", {}).get("endpoints", {}).get(ep_key, {})
            if isinstance(ep_obj, dict) and ep_obj.get("path"):
                return str(ep_obj["path"])
        return None
