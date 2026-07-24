"""Continuity validators V5-V6 (EXTRACTION_SPEC §6). Pure, no network."""

from __future__ import annotations

from decimal import Decimal

from validation.types import ValidationResult, rel_diff

TOL_RESTATEMENT = Decimal("0.001")   # 0.1%


def v5_comparative_continuity(
    current_comparatives: dict[str, Decimal],
    stored_prior: dict[str, Decimal],
) -> ValidationResult:
    """V5: prior-period comparatives printed in this filing match the values
    stored from the prior filing, within 0.1%. Critical.

    A mismatch is flagged as a **possible restatement** — history is NEVER
    auto-overwritten. The mismatch fails the check (critical), routing the
    filing to the exception queue for a human to investigate/confirm.
    """
    keys = sorted(set(current_comparatives) & set(stored_prior))
    if not keys:
        return ValidationResult(
            "V5", "Comparative continuity", True, False, False,
            message="no overlapping comparatives to reconcile",
        )
    mismatches: list[str] = []
    worst: Decimal | None = None
    for k in keys:
        rd = rel_diff(current_comparatives[k], stored_prior[k])
        if rd is not None and rd >= TOL_RESTATEMENT:
            mismatches.append(f"{k}: {current_comparatives[k]} vs stored {stored_prior[k]}")
            worst = rd if worst is None or rd > worst else worst
    ok = not mismatches
    return ValidationResult(
        "V5", "Comparative continuity", True, ok, True,
        tolerance=TOL_RESTATEMENT, actual=worst,
        warning=not ok,
        message="" if ok else "possible restatement (not auto-overwritten): " + "; ".join(mismatches),
    )


def v6_cumulative_monotonicity(
    cumulative_series: list[Decimal],
    label: str = "cumulative",
) -> ValidationResult:
    """V6: cumulative revenue / OCF are non-decreasing Q1->H1->9M->FY within a
    fiscal year. Non-critical.

    A decrease is *flagged* (a genuinely negative quarter), not failed — the
    result records a warning but stays ``passed`` so it never sets INSUFFICIENT.
    """
    if len(cumulative_series) < 2:
        return ValidationResult(
            "V6", "Cumulative monotonicity", False, True, False,
            message=f"fewer than two {label} points",
        )
    decreases = [
        f"idx {i}: {cumulative_series[i - 1]} -> {cumulative_series[i]}"
        for i in range(1, len(cumulative_series))
        if cumulative_series[i] < cumulative_series[i - 1]
    ]
    has_flag = bool(decreases)
    return ValidationResult(
        "V6", "Cumulative monotonicity", False, True, True,
        warning=has_flag,
        message="" if not has_flag else f"{label} decreased (negative quarter?): " + "; ".join(decreases),
    )
