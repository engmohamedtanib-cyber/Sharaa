"""Plausibility validators V7-V8 (EXTRACTION_SPEC §6). Pure, no network."""

from __future__ import annotations

from decimal import Decimal

from common.decimals import ZERO
from engine.types import Financials
from validation.types import ValidationResult, within

# V7: revenue within 3 orders of magnitude of market cap.
_MAG_LOW = Decimal("0.001")    # 10^-3
_MAG_HIGH = Decimal("1000")    # 10^3
_SHARES_TOL = Decimal("0.05")  # 5%
TOL_SEGMENT = Decimal("0.01")  # 1%


def v7_magnitude_plausibility(
    fin: Financials,
    market_cap: Decimal | None,
    close_price: Decimal | None,
    shares_out: Decimal | None,
) -> ValidationResult:
    """V7: catches the 1000x unit-scale error (the highest-frequency mistake).

    - total_assets > 0
    - revenue within 3 orders of magnitude of market cap
    - shares_out consistent with market_cap / price within 5%
    Critical.
    """
    checks: list[str] = []
    ok = True

    if fin.total_assets is None or fin.total_assets <= ZERO:
        ok = False
        checks.append("total_assets not positive")

    ratio: Decimal | None = None
    if fin.total_revenue is not None and market_cap not in (None, ZERO):
        ratio = fin.total_revenue / market_cap
        if not (_MAG_LOW <= ratio <= _MAG_HIGH):
            ok = False
            checks.append(f"revenue/market_cap {ratio} outside [1e-3, 1e3] (scale error?)")

    if shares_out is not None and close_price not in (None, ZERO) and market_cap not in (None, ZERO):
        implied = market_cap / close_price
        if not within(shares_out, implied, _SHARES_TOL, denom=implied):
            ok = False
            checks.append(f"shares_out {shares_out} vs market_cap/price {implied} beyond 5%")

    applicable = market_cap is not None or fin.total_assets is not None
    return ValidationResult(
        "V7", "Magnitude plausibility", True, ok, applicable,
        actual=ratio,
        message="" if ok else "; ".join(checks),
    )


def v8_segment_reconciliation(
    segment_revenues: list[Decimal],
    total_revenue: Decimal | None,
) -> ValidationResult:
    """V8: sum of segment revenues == total_revenue within 1%. Non-critical.

    Applicable only when a segment note exists (segment_revenues non-empty).
    """
    if not segment_revenues or total_revenue in (None, ZERO):
        return ValidationResult(
            "V8", "Segment reconciliation", False, True, False,
            message="no segment note to reconcile",
        )
    seg_sum = sum(segment_revenues, ZERO)
    ok = within(seg_sum, total_revenue, TOL_SEGMENT, denom=total_revenue)
    return ValidationResult(
        "V8", "Segment reconciliation", False, ok, True,
        expected=total_revenue, actual=seg_sum, tolerance=TOL_SEGMENT,
        warning=not ok,
        message="" if ok else f"segment sum {seg_sum} != total_revenue {total_revenue}",
    )
