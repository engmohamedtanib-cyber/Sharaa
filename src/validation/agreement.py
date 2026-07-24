"""Dual-extraction agreement validator V9 (EXTRACTION_SPEC §6). Pure."""

from __future__ import annotations

from decimal import Decimal

from validation.types import ValidationResult, rel_diff

TOL_AGREEMENT = Decimal("0.001")  # 0.1%


def v9_dual_extraction_agreement(
    pass1: dict[str, Decimal],
    pass2: dict[str, Decimal],
    critical_keys: list[str],
) -> ValidationResult:
    """V9: for every critical item, ``|p1 - p2| / max(|p1|,|p2|) < 0.1%``.

    Any critical disagreement => CONFLICT (routed to the exception queue).
    A critical key present in only one pass is itself a disagreement. Critical.
    """
    conflicts: list[str] = []
    worst: Decimal | None = None
    for key in critical_keys:
        p1, p2 = pass1.get(key), pass2.get(key)
        if p1 is None and p2 is None:
            continue  # absent in both -> a coverage concern, not a conflict
        if p1 is None or p2 is None:
            conflicts.append(f"{key}: present in only one pass")
            continue
        rd = rel_diff(p1, p2)
        if rd is not None and rd >= TOL_AGREEMENT:
            conflicts.append(f"{key}: {p1} vs {p2}")
            worst = rd if worst is None or rd > worst else worst
    ok = not conflicts
    return ValidationResult(
        "V9", "Dual-extraction agreement", True, ok, True,
        tolerance=TOL_AGREEMENT, actual=worst,
        message="" if ok else "critical disagreement: " + "; ".join(conflicts),
    )
