"""Fair value and equity-risk-premium (ENGINE_SPEC §4.4, §5.4).

Calibrated to the Egyptian risk-free rate, not global multiples. Two fair-value
methods; the engine takes the **lower** (conservative). All pure Decimal.
"""

from __future__ import annotations

from decimal import Decimal

from common.decimals import ONE, safe_div


def earnings_yield(pe_ratio: Decimal) -> Decimal | None:
    """1 / P/E. ``None`` for a non-positive (loss-making) multiple."""
    if pe_ratio <= 0:
        return None
    return ONE / pe_ratio


def equity_risk_premium(pe_ratio: Decimal, tbill_1y_yield: Decimal) -> Decimal | None:
    """ERP = earnings_yield - 1y T-bill yield (§4.4). ``None`` if loss-making."""
    ey = earnings_yield(pe_ratio)
    if ey is None:
        return None
    return ey - tbill_1y_yield


def justified_pe_fair_value(
    payout_ratio: Decimal,
    g_real: Decimal,
    r_real: Decimal,
    eps_ttm: Decimal,
) -> Decimal | None:
    """Justified-P/E fair value in real terms (§5.4).

    ``justified_pe = payout * (1 + g_real) / (r_real - g_real)``.
    Undefined (returns ``None``) when ``r_real <= g_real`` — the engine must
    not emit a nonsensical number rather than admit the model does not apply.
    """
    if r_real <= g_real:
        return None
    justified_pe = payout_ratio * (ONE + g_real) / (r_real - g_real)
    return justified_pe * eps_ttm


def peer_relative_fair_value(sector_median_pe: Decimal, eps_ttm: Decimal) -> Decimal | None:
    """EGX-comparables fair value (never global peers, §5.4)."""
    if sector_median_pe <= 0:
        return None
    return sector_median_pe * eps_ttm


def fair_value(
    *,
    payout_ratio: Decimal | None = None,
    g_real: Decimal | None = None,
    r_real: Decimal | None = None,
    eps_ttm: Decimal | None = None,
    sector_median_pe: Decimal | None = None,
) -> Decimal | None:
    """Lower of the available fair-value methods (conservative, §5.4).

    Returns ``None`` when no method can be computed.
    """
    candidates: list[Decimal] = []
    if None not in (payout_ratio, g_real, r_real, eps_ttm):
        fv1 = justified_pe_fair_value(payout_ratio, g_real, r_real, eps_ttm)  # type: ignore[arg-type]
        if fv1 is not None:
            candidates.append(fv1)
    if sector_median_pe is not None and eps_ttm is not None:
        fv2 = peer_relative_fair_value(sector_median_pe, eps_ttm)
        if fv2 is not None:
            candidates.append(fv2)
    if not candidates:
        return None
    return min(candidates)


def valuation_gap(fair_value_est: Decimal | None, price: Decimal) -> Decimal | None:
    """(fair_value - price) / fair_value ; positive = discount (§5.3)."""
    if fair_value_est is None:
        return None
    return safe_div(fair_value_est - price, fair_value_est)
