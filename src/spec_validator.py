"""Deterministic specification, Markdown section, and LaTeX validator."""

from __future__ import annotations

import re
from typing import Any, Optional

from src.schemas import (
    ChangeType,
    CriticVerificationReport,
    DiffAnalysisReport,
    DocumentationDraft,
    FactCheckError,
    SpecParameterChange,
)

_SENTINEL = object()

_WRONG_UNITS_BY_CANONICAL: dict[str, tuple[str, ...]] = {
    "V DC": ("mV", "kV", "V AC", "VAC", "A", "W"),
    "°C": ("°F", "F", "K", "Kelvin"),
    "Hz": ("kHz", "MHz", "GHz", "rpm", "req/min"),
    "req/min": ("req/s", "req/hr", "rps", "Hz"),
}

_UNIT_HINTS: dict[str, str] = {
    "nominal_voltage_v": "voltage_unit",
    "max_operating_temp_c": "temp_unit",
    "min_operating_temp_c": "temp_unit",
    "sampling_rate_hz": "rate_unit",
    "rate_limit_rpm": "rate_limit_unit",
}

_COMPANION_CHECK_KEYS: dict[str, tuple[str, ...]] = {
    "hardware.power": ("max_current_a", "efficiency_factor"),
    "hardware.thermal": ("min_operating_temp_c",),
    "hardware.telemetry": ("adc_resolution_bits",),
}


def flatten_spec_yaml(spec_data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Flatten nested YAML mappings into dot-separated key paths."""
    flat: dict[str, Any] = {}
    for key, value in spec_data.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            flat.update(flatten_spec_yaml(value, path))
        else:
            flat[path] = value
    return flat


def get_spec_value(spec_data: dict[str, Any], yaml_path: str) -> Any:
    """Retrieve a value from nested spec_data by dot-separated yaml_path."""
    current: Any = spec_data
    for segment in yaml_path.split("."):
        if not isinstance(current, dict) or segment not in current:
            return _SENTINEL
        current = current[segment]
    return current


def resolve_unit_for_path(spec_data: dict[str, Any], yaml_path: str) -> Optional[str]:
    """Resolve the canonical engineering unit from sibling *_unit keys in spec_data."""
    parts = yaml_path.split(".")
    leaf = parts[-1]
    parent_path = ".".join(parts[:-1])
    parent = get_spec_value(spec_data, parent_path) if parent_path else spec_data
    if not isinstance(parent, dict):
        return None

    hinted_key = _UNIT_HINTS.get(leaf)
    if hinted_key and isinstance(parent.get(hinted_key), str):
        return str(parent[hinted_key])

    unit_keys = [k for k in parent if str(k).endswith("_unit") and isinstance(parent[k], str)]
    if len(unit_keys) == 1:
        return str(parent[unit_keys[0]])
    return None


def split_markdown_sections(markdown_text: str) -> tuple[str, list[tuple[str, str]]]:
    """Split Markdown document into (preamble, [(heading_line, body_with_newlines), ...]).

    Preserves exact bytes so reassemble_markdown_sections(preamble, sections)
    round-trips byte-identically.
    """
    matches = list(re.finditer(r"^## [^\r\n]*", markdown_text, flags=re.MULTILINE))
    if not matches:
        return markdown_text, []

    preamble = markdown_text[: matches[0].start()]
    sections: list[tuple[str, str]] = []
    for idx, match in enumerate(matches):
        heading = match.group(0)
        body_start = match.end()
        body_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(markdown_text)
        sections.append((heading, markdown_text[body_start:body_end]))
    return preamble, sections


def reassemble_markdown_sections(
    preamble: str, sections: list[tuple[str, str]]
) -> str:
    """Reassemble Markdown sections into a complete document."""
    return preamble + "".join(f"{heading}{body}" for heading, body in sections)


def _check_balanced_braces(expr: str) -> bool:
    depth = 0
    idx = 0
    while idx < len(expr):
        ch = expr[idx]
        if ch == "\\" and idx + 1 < len(expr):
            idx += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                return False
        idx += 1
    return depth == 0


def validate_latex_blocks(text: str) -> list[str]:
    """Validate that display ($$...$$) and inline ($...$) LaTeX delimiters and braces balance."""
    errors: list[str] = []

    display_positions = [m.start() for m in re.finditer(r"(?<!\\)\$\$", text)]
    if len(display_positions) % 2 != 0:
        errors.append("Unclosed display math delimiter '$$' detected.")
        return errors

    for i in range(0, len(display_positions), 2):
        block = text[display_positions[i] + 2 : display_positions[i + 1]]
        if not _check_balanced_braces(block):
            errors.append(f"Unbalanced braces in display LaTeX block: $${block.strip()}$$")

    scrubbed = re.sub(r"(?<!\\)\$\$.*?(?<!\\)\$\$", "", text, flags=re.DOTALL)
    inline_positions = [m.start() for m in re.finditer(r"(?<!\\)\$", scrubbed)]
    if len(inline_positions) % 2 != 0:
        errors.append("Unclosed inline math delimiter '$' detected.")
        return errors

    for i in range(0, len(inline_positions), 2):
        expr = scrubbed[inline_positions[i] + 1 : inline_positions[i + 1]]
        if not _check_balanced_braces(expr):
            errors.append(f"Unbalanced braces in inline LaTeX expression: ${expr.strip()}$")

    return errors


def _format_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _contains_exact_number(text: str, value: Any) -> bool:
    """Check whether exact numeric value appears with proper numeric boundaries."""
    val_str = _format_scalar(value)
    pattern = rf"(?<![\d.]){re.escape(val_str)}(?![\d.])"
    return re.search(pattern, text) is not None


def _detect_wrong_unit_near_value(
    section_body: str, value: Any, canonical_unit: str
) -> Optional[str]:
    """Detect whether value appears paired with an incorrect engineering unit."""
    wrong_units = _WRONG_UNITS_BY_CANONICAL.get(canonical_unit, ())
    if not wrong_units:
        return None
    val_str = re.escape(_format_scalar(value))
    alt_units = "|".join(re.escape(u) for u in wrong_units)
    # Match Markdown table row like: | ... | `1000` | `kHz` |
    table_pattern = rf"\|\s*`?{val_str}`?\s*\|\s*`?({alt_units})`?\s*\|"
    match = re.search(table_pattern, section_body)
    if match:
        return match.group(1)

    # Match inline prose or LaTeX like: 1000 kHz, 1000\,kHz, 1000\,\text{kHz}, 85.0 °F
    inline_pattern = (
        rf"(?<![\d.]){val_str}(?![\d.])"
        rf"(?:\s+|\\,|\s*`?\s*|\s*\\text\{{|\s*\^\{{\\circ\}}\\text\{{)?"
        rf"({alt_units})(?=\b|\}}|`|\\|\s|$)"
    )
    match = re.search(inline_pattern, section_body)
    if match:
        return match.group(1)
    return None


def _check_canonical_unit_present(
    section_body: str, value: Any, canonical_unit: str
) -> bool:
    """Verify the canonical unit appears in the section alongside the new value."""
    if canonical_unit in section_body:
        return True
    # Also accept LaTeX degree Celsius form for °C
    if canonical_unit == "°C" and r"^{\circ}\text{C}" in section_body:
        return True
    return False


def _validate_modified_change(
    change: SpecParameterChange,
    truth_val: Any,
    truth_unit: Optional[str],
    lang: str,
    heading: str,
    body: str,
) -> tuple[list[FactCheckError], int]:
    """Validate a modified scalar parameter against ground-truth spec values."""
    errors: list[FactCheckError] = []
    hallucinations = 0
    expected_str = f"{_format_scalar(truth_val)} {truth_unit}".strip() if truth_unit else _format_scalar(truth_val)

    # 1. Check stale old value remaining
    if change.old_value is not None and _format_scalar(change.old_value) != _format_scalar(truth_val):
        if _contains_exact_number(body, change.old_value):
            hallucinations += 1
            errors.append(
                FactCheckError(
                    error_code="STALE_VALUE_REMAINING",
                    language=lang,  # type: ignore[arg-type]
                    section_heading=heading,
                    yaml_path=change.yaml_path,
                    expected_value=expected_str,
                    found_value=_format_scalar(change.old_value),
                    message=(
                        f"Stale value '{change.old_value}' for '{change.yaml_path}' "
                        f"still present in {lang.upper()} section '{heading}'. "
                        f"Must be updated to '{expected_str}'."
                    ),
                )
            )

    # 2. Check exact new value present
    if not _contains_exact_number(body, truth_val):
        hallucinations += 1
        errors.append(
            FactCheckError(
                error_code="VALUE_MISMATCH",
                language=lang,  # type: ignore[arg-type]
                section_heading=heading,
                yaml_path=change.yaml_path,
                expected_value=expected_str,
                found_value="missing or altered numeric value",
                message=(
                    f"Ground-truth value '{_format_scalar(truth_val)}' for "
                    f"'{change.yaml_path}' not found in {lang.upper()} section '{heading}'."
                ),
            )
        )

    # 3. Check unit alignment
    if truth_unit:
        wrong_unit = _detect_wrong_unit_near_value(body, truth_val, truth_unit)
        if wrong_unit is not None:
            hallucinations += 1
            errors.append(
                FactCheckError(
                    error_code="UNIT_MISMATCH",
                    language=lang,  # type: ignore[arg-type]
                    section_heading=heading,
                    yaml_path=change.yaml_path,
                    expected_value=truth_unit,
                    found_value=wrong_unit,
                    message=(
                        f"Unit mismatch for '{change.yaml_path}' in {lang.upper()} "
                        f"section '{heading}': found '{wrong_unit}', expected '{truth_unit}'."
                    ),
                )
            )
        elif not _check_canonical_unit_present(body, truth_val, truth_unit):
            errors.append(
                FactCheckError(
                    error_code="UNIT_MISMATCH",
                    language=lang,  # type: ignore[arg-type]
                    section_heading=heading,
                    yaml_path=change.yaml_path,
                    expected_value=truth_unit,
                    found_value="missing canonical unit",
                    message=(
                        f"Canonical unit '{truth_unit}' for '{change.yaml_path}' "
                        f"is missing in {lang.upper()} section '{heading}'."
                    ),
                )
            )

    return errors, hallucinations


def _validate_added_change(
    change: SpecParameterChange,
    truth_val: Any,
    lang: str,
    heading: str,
    body: str,
) -> tuple[list[FactCheckError], int]:
    """Validate an added specification parameter (e.g., new API query param)."""
    errors: list[FactCheckError] = []
    hallucinations = 0

    if change.parameter_name not in body:
        errors.append(
            FactCheckError(
                error_code="MISSING_PARAMETER",
                language=lang,  # type: ignore[arg-type]
                section_heading=heading,
                yaml_path=change.yaml_path,
                expected_value=change.parameter_name,
                found_value="absent",
                message=(
                    f"Added parameter '{change.parameter_name}' ({change.yaml_path}) "
                    f"is missing from {lang.upper()} section '{heading}'."
                ),
            )
        )
        return errors, hallucinations

    if isinstance(truth_val, dict):
        default_val = truth_val.get("default")
        if default_val is not None and str(default_val) not in body:
            errors.append(
                FactCheckError(
                    error_code="VALUE_MISMATCH",
                    language=lang,  # type: ignore[arg-type]
                    section_heading=heading,
                    yaml_path=f"{change.yaml_path}.default",
                    expected_value=str(default_val),
                    found_value="missing default value",
                    message=(
                        f"Default value '{default_val}' for '{change.parameter_name}' "
                        f"not documented in {lang.upper()} section '{heading}'."
                    ),
                )
            )
        allowed = truth_val.get("allowed_values", [])
        for item in allowed:
            if str(item) not in body:
                errors.append(
                    FactCheckError(
                        error_code="VALUE_MISMATCH",
                        language=lang,  # type: ignore[arg-type]
                        section_heading=heading,
                        yaml_path=f"{change.yaml_path}.allowed_values",
                        expected_value=", ".join(str(x) for x in allowed),
                        found_value=f"missing '{item}'",
                        message=(
                            f"Allowed value '{item}' for '{change.parameter_name}' "
                            f"is missing in {lang.upper()} section '{heading}'."
                        ),
                    )
                )
                break
    elif truth_val is not None and str(truth_val) not in body:
        hallucinations += 1
        errors.append(
            FactCheckError(
                error_code="VALUE_MISMATCH",
                language=lang,  # type: ignore[arg-type]
                section_heading=heading,
                yaml_path=change.yaml_path,
                expected_value=str(truth_val),
                found_value="missing",
                message=(
                    f"Added value '{truth_val}' for '{change.yaml_path}' missing in "
                    f"{lang.upper()} section '{heading}'."
                ),
            )
        )

    return errors, hallucinations


def verify_draft_against_spec(
    original_en: str,
    original_fr: str,
    draft: DocumentationDraft,
    diff_analysis: DiffAnalysisReport,
    spec_data: dict[str, Any],
    attempt: int = 1,
) -> CriticVerificationReport:
    """Deterministically verify EN/FR documentation drafts against api_and_hardware_specs.yaml."""
    errors: list[FactCheckError] = []
    hallucinations = 0

    affected_en = {
        sec
        for change in diff_analysis.changes
        for sec in change.affected_sections_en
    }
    affected_fr = {
        sec
        for change in diff_analysis.changes
        for sec in change.affected_sections_fr
    }

    manual_pairs = [
        ("en", original_en, draft.updated_manual_en, affected_en),
        ("fr", original_fr, draft.updated_manual_fr, affected_fr),
    ]

    section_maps: dict[str, dict[str, str]] = {"en": {}, "fr": {}}

    for lang, orig_text, updated_text, affected_headings in manual_pairs:
        orig_preamble, orig_sections = split_markdown_sections(orig_text)
        upd_preamble, upd_sections = split_markdown_sections(updated_text)
        orig_map = dict(orig_sections)
        upd_map = dict(upd_sections)
        section_maps[lang] = upd_map

        if orig_preamble != upd_preamble:
            errors.append(
                FactCheckError(
                    error_code="UNTOUCHED_SECTION_MODIFIED",
                    language=lang,  # type: ignore[arg-type]
                    section_heading="<preamble>",
                    yaml_path="<document>",
                    expected_value="byte-identical preamble",
                    found_value="modified preamble",
                    message=f"Document title/preamble was unexpectedly modified in {lang.upper()} manual.",
                )
            )

        for heading, orig_body in orig_sections:
            upd_body = upd_map.get(heading)
            if upd_body is None:
                errors.append(
                    FactCheckError(
                        error_code="UNTOUCHED_SECTION_MODIFIED",
                        language=lang,  # type: ignore[arg-type]
                        section_heading=heading,
                        yaml_path="<document>",
                        expected_value=heading,
                        found_value="missing section",
                        message=f"Section '{heading}' was deleted from {lang.upper()} manual.",
                    )
                )
                continue

            if heading not in affected_headings:
                if upd_body != orig_body:
                    errors.append(
                        FactCheckError(
                            error_code="UNTOUCHED_SECTION_MODIFIED",
                            language=lang,  # type: ignore[arg-type]
                            section_heading=heading,
                            yaml_path="<document>",
                            expected_value="byte-identical section",
                            found_value="modified untouched section",
                            message=(
                                f"Untouched section '{heading}' in {lang.upper()} manual was altered."
                            ),
                        )
                    )
            else:
                latex_errors = validate_latex_blocks(upd_body)
                for lerr in latex_errors:
                    errors.append(
                        FactCheckError(
                            error_code="LATEX_SYNTAX_BROKEN",
                            language=lang,  # type: ignore[arg-type]
                            section_heading=heading,
                            yaml_path="<latex>",
                            expected_value="balanced $ / $$ and {} delimiters",
                            found_value=lerr,
                            message=f"LaTeX syntax error in {lang.upper()} '{heading}': {lerr}",
                        )
                    )

    checked_params = 0
    for change in diff_analysis.changes:
        checked_params += 1
        truth_val = get_spec_value(spec_data, change.yaml_path)
        if truth_val is _SENTINEL:
            errors.append(
                FactCheckError(
                    error_code="SPEC_VALUE_HALLUCINATION",
                    language="en",
                    section_heading="<spec>",
                    yaml_path=change.yaml_path,
                    expected_value="valid YAML path in spec",
                    found_value="missing path",
                    message=f"Path '{change.yaml_path}' does not exist in ground-truth YAML spec.",
                )
            )
            hallucinations += 1
            continue

        truth_unit = resolve_unit_for_path(spec_data, change.yaml_path) or change.unit
        parent_group = ".".join(change.yaml_path.split(".")[:-1])
        companion_keys = _COMPANION_CHECK_KEYS.get(parent_group, ())

        for lang, headings in (
            ("en", change.affected_sections_en),
            ("fr", change.affected_sections_fr),
        ):
            for heading in headings:
                body = section_maps[lang].get(heading, "")
                if not body:
                    continue

                if change.change_type == ChangeType.MODIFIED:
                    param_errors, param_halluc = _validate_modified_change(
                        change, truth_val, truth_unit, lang, heading, body
                    )
                    errors.extend(param_errors)
                    hallucinations += param_halluc
                elif change.change_type == ChangeType.ADDED:
                    param_errors, param_halluc = _validate_added_change(
                        change, truth_val, lang, heading, body
                    )
                    errors.extend(param_errors)
                    hallucinations += param_halluc

                # Verify companion parameters in the same YAML group remain uncorrupted
                for comp_key in companion_keys:
                    comp_path = f"{parent_group}.{comp_key}"
                    comp_val = get_spec_value(spec_data, comp_path)
                    if comp_val is not _SENTINEL and not _contains_exact_number(body, comp_val):
                        hallucinations += 1
                        errors.append(
                            FactCheckError(
                                error_code="SPEC_VALUE_HALLUCINATION",
                                language=lang,  # type: ignore[arg-type]
                                section_heading=heading,
                                yaml_path=comp_path,
                                expected_value=_format_scalar(comp_val),
                                found_value="altered or missing companion value",
                                message=(
                                    f"Companion spec value '{comp_path}={comp_val}' "
                                    f"was corrupted or dropped in {lang.upper()} '{heading}'."
                                ),
                            )
                        )

    approved = len(errors) == 0
    if approved:
        summary = (
            f"APPROVED on attempt {attempt}: verified {checked_params} spec changes "
            f"across EN/FR manuals with 0 numeric hallucinations and intact LaTeX/untouched sections."
        )
    else:
        summary = (
            f"REJECTED on attempt {attempt}: detected {len(errors)} error(s) "
            f"({hallucinations} numeric hallucination(s)) across {checked_params} checked parameters."
        )

    return CriticVerificationReport(
        approved=approved,
        attempt=attempt,
        checked_parameters=checked_params,
        numeric_hallucinations_found=hallucinations,
        errors=errors,
        summary=summary,
    )
