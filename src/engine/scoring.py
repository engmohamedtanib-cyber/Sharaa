"""Seven-pillar, 100-point scoring model (ENGINE_SPEC §4).

Gate first (``CLAUDE.md`` R7): only ``GREEN``/``AMBER`` + ``VALIDATED`` filings
are scored; everything else returns ``None`` (never zero). Any sub-criterion
whose inputs are unavailable scores **0** with ``band_matched='MISSING_DATA'``
— never an estimate (R3). Bands are evaluated top-down, first match wins.

Pure module. Two runs on identical input produce identical output.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from common.decimals import ONE, ZERO
from engine.bands import match
from engine.config import Thresholds
from engine.shariah import GateResult, interest_bearing_debt
from engine.stats import linear_slope, population_stdev
from engine.types import (
    AuditOpinion,
    CapitalAllocation,
    DataStatus,
    FxResilience,
    RelatedPartyQuality,
    RoicCategory,
    ScoringInputs,
    ShariahStatus,
    Timeliness,
)

MISSING = "MISSING_DATA"

# Veto identifiers surfaced from scoring (consumed by decisions.py §5.2).
VETO_AUDIT = "AUDIT_OPINION_ADVERSE"
VETO_LIQUIDITY = "LIQUIDITY_6B"


@dataclass(frozen=True)
class SubScore:
    subcriterion: str
    pillar: int
    points: Decimal
    points_max: Decimal
    metric_value: Decimal | None
    band_matched: str
    rationale: str


@dataclass(frozen=True)
class ScoreResult:
    subscores: tuple[SubScore, ...]
    pillar_totals: dict[str, Decimal]
    total: Decimal
    band: str
    vetoes: tuple[str, ...]
    alerts: tuple[str, ...]

    def sub(self, code: str) -> SubScore:
        for s in self.subscores:
            if s.subcriterion == code:
                return s
        raise KeyError(code)


# ----------------------------------------------------------------------
# small helpers
# ----------------------------------------------------------------------
def _award(
    code: str,
    pillar: int,
    points: Decimal,
    pmax: Decimal,
    metric: Decimal | None,
    band: str,
    rationale: str,
) -> SubScore:
    return SubScore(code, pillar, points, pmax, metric, band, rationale)


def _missing(code: str, pillar: int, pmax: Decimal, why: str) -> SubScore:
    return _award(code, pillar, ZERO, pmax, None, MISSING, why)


def _band_award(
    code: str,
    pillar: int,
    value: Decimal | None,
    cfg: Thresholds,
    pkey: str,
    rationale_metric: str,
) -> SubScore:
    """Score a purely band-driven sub-criterion from ``config`` bands."""
    pmax = cfg.points_max(pkey, code)
    if value is None:
        return _missing(code, pillar, pmax, f"{rationale_metric} unavailable")
    bands = cfg.bands(pkey, code)
    band = match(value, bands)
    if band is None:
        default = Decimal(str(cfg.sub(pkey, code).get("default", 0)))
        return _award(code, pillar, default, pmax, value, "default", f"{rationale_metric}={value} (below all bands)")
    pts = band.payload["points"]
    return _award(code, pillar, pts, pmax, value, band.label(), f"{rationale_metric}={value}")


# ======================================================================
# PILLAR 1 — Shariah Headroom (25)  (§4.1)
# ======================================================================
def _p1(inp: ScoringInputs, gate: GateResult, cfg: Thresholds) -> list[SubScore]:
    a = gate.screen("A").ratio
    c = gate.screen("C").utilisation
    d = gate.screen("D").utilisation
    e = gate.screen("E").utilisation
    subs = [
        _band_award("1A", 1, a, cfg, "P1", "Screen A ratio"),
        _band_award("1B", 1, c, cfg, "P1", "Screen C utilisation"),
        _band_award("1C", 1, d, cfg, "P1", "Screen D utilisation"),
        _band_award("1D", 1, e, cfg, "P1", "Screen E utilisation"),
        _p1e(inp, cfg),
    ]
    return subs


def _p1e(inp: ScoringInputs, cfg: Thresholds) -> SubScore:
    pmax = cfg.points_max("P1", "1E")
    params = cfg.sub_params("P1", "1E")
    pts = cfg.sub_points("P1", "1E")
    strong_q = int(params["strong_streak_quarters"])
    stable_q = int(params["stable_streak_quarters"])
    hist_req = int(params["history_required"])
    slope_max = Decimal(str(params["improving_slope_max"]))

    streak = inp.history.consecutive_compliant_quarters
    series = inp.history.worst_util_last4
    if len(series) < hist_req:
        return _missing("1E", 1, pmax, "fewer than 4 filings of utilisation history")
    slope = linear_slope(series)
    improving = slope <= slope_max
    if streak >= strong_q and improving:
        return _award("1E", 1, pts["strong"], pmax, slope, "strong", f"streak={streak}, slope={slope} improving")
    if streak >= stable_q and improving:
        return _award("1E", 1, pts["stable"], pmax, slope, "stable", f"streak={streak}, slope={slope} improving/flat")
    if streak >= stable_q:
        return _award(
            "1E", 1, pts["deteriorating"], pmax, slope, "deteriorating", f"streak={streak}, slope={slope} rising"
        )
    return _award("1E", 1, pts["insufficient"], pmax, slope, "insufficient", f"streak={streak} (<{stable_q})")


# ======================================================================
# PILLAR 2 — Financial Strength (20)  (§4.2)
# ======================================================================
def _ebitda(inp: ScoringInputs) -> Decimal | None:
    op = inp.financials.operating_profit
    if op is None:
        return None
    return op + inp.financials.get0("depreciation") + inp.financials.get0("amortisation")


def _net_debt(inp: ScoringInputs) -> Decimal:
    fin = inp.financials
    return (
        interest_bearing_debt(fin)
        + fin.get0("islamic_financing")
        - fin.get0("cash_and_equivalents")
        - fin.get0("time_deposits")
    )


def _p2(inp: ScoringInputs, cfg: Thresholds) -> list[SubScore]:
    return [
        _p2a(inp, cfg),
        _p2b(inp, cfg),
        _p2c(inp, cfg),
        _p2d(inp, cfg),
        _p2e(inp, cfg),
    ]


def _p2a(inp: ScoringInputs, cfg: Thresholds) -> SubScore:
    pmax = cfg.points_max("P2", "2A")
    net_cash_pts = cfg.sub_params("P2", "2A")["net_cash_points"]
    nd = _net_debt(inp)
    if nd < ZERO:
        return _award("2A", 2, Decimal(str(net_cash_pts)), pmax, nd, "net_cash", "net cash position")
    ebitda = _ebitda(inp)
    if ebitda is None:
        return _missing("2A", 2, pmax, "operating_profit unavailable for EBITDA")
    if ebitda <= ZERO:
        return _award("2A", 2, ZERO, pmax, None, "default", "non-positive EBITDA with net debt")
    ratio = nd / ebitda
    band = match(ratio, cfg.bands("P2", "2A"))
    if band is None:
        return _award("2A", 2, ZERO, pmax, ratio, "default", f"net debt/EBITDA={ratio}")
    return _award("2A", 2, band.payload["points"], pmax, ratio, band.label(), f"net debt/EBITDA={ratio}")


def _p2b(inp: ScoringInputs, cfg: Thresholds) -> SubScore:
    pmax = cfg.points_max("P2", "2B")
    no_fc_pts = cfg.sub_params("P2", "2B")["no_finance_cost_points"]
    fin = inp.financials
    fc = fin.finance_cost
    if fc is None or fc == ZERO:
        return _award("2B", 2, Decimal(str(no_fc_pts)), pmax, None, "no_finance_cost", "no finance cost")
    if fin.operating_profit is None:
        return _missing("2B", 2, pmax, "operating_profit unavailable")
    ratio = fin.operating_profit / fc
    band = match(ratio, cfg.bands("P2", "2B"))
    if band is None:
        return _award("2B", 2, ZERO, pmax, ratio, "default", f"EBIT/finance cost={ratio}")
    return _award("2B", 2, band.payload["points"], pmax, ratio, band.label(), f"EBIT/finance cost={ratio}")


def _p2c(inp: ScoringInputs, cfg: Thresholds) -> SubScore:
    pmax = cfg.points_max("P2", "2C")
    p = cfg.sub_params("P2", "2C")
    pts = cfg.sub_points("P2", "2C")
    fin = inp.financials
    ca, cl = fin.total_current_assets, fin.total_current_liabilities
    if ca is None or cl is None or cl == ZERO:
        return _missing("2C", 2, pmax, "current assets/liabilities unavailable")
    cr = ca / cl
    qr = (ca - fin.get0("inventory")) / cl
    if cr > p["cr_high"] and qr > p["qr_high"]:
        return _award("2C", 2, pts["tier4"], pmax, cr, "tier4", f"CR={cr}, QR={qr}")
    if cr > p["cr_mid"] and qr > p["qr_mid"]:
        return _award("2C", 2, pts["tier3"], pmax, cr, "tier3", f"CR={cr}, QR={qr}")
    if cr > p["cr_low"]:
        return _award("2C", 2, pts["tier2"], pmax, cr, "tier2", f"CR={cr}")
    if cr > p["cr_min"]:
        return _award("2C", 2, pts["tier1"], pmax, cr, "tier1", f"CR={cr}")
    return _award("2C", 2, ZERO, pmax, cr, "default", f"CR={cr}")


def _p2d(inp: ScoringInputs, cfg: Thresholds) -> SubScore:
    pmax = cfg.points_max("P2", "2D")
    ocf, ni = inp.history.ocf_ttm, inp.history.net_income_ttm
    if ocf is None or ni is None:
        return _missing("2D", 2, pmax, "TTM OCF/net income unavailable")
    if ni <= ZERO:
        return _award("2D", 2, ZERO, pmax, None, "default", "non-positive TTM net income")
    ratio = ocf / ni
    band = match(ratio, cfg.bands("P2", "2D"))
    if band is None:
        return _award("2D", 2, ZERO, pmax, ratio, "default", f"OCF/NI={ratio}")
    return _award("2D", 2, band.payload["points"], pmax, ratio, band.label(), f"OCF/NI={ratio}")


def _p2e(inp: ScoringInputs, cfg: Thresholds) -> SubScore:
    pmax = cfg.points_max("P2", "2E")
    pts = cfg.sub_points("P2", "2E")
    fx = inp.qualitative.fx_resilience
    if fx is None or fx is FxResilience.UNKNOWN:
        return _missing("2E", 2, pmax, "FX exposure note absent")
    mapping = {
        FxResilience.HEDGED: pts["hedged"],
        FxResilience.BALANCED: pts["balanced"],
        FxResilience.SMALL_LIABILITY: pts["small_liability"],
        FxResilience.EXPOSED: pts["exposed"],
    }
    return _award("2E", 2, mapping[fx], pmax, None, fx.value, f"FX resilience: {fx.value}")


# ======================================================================
# PILLAR 3 — Earnings Quality (15)  (§4.3)
# ======================================================================
def _p3(inp: ScoringInputs, cfg: Thresholds) -> tuple[list[SubScore], list[str]]:
    subs = [_p3a(inp, cfg), _p3b(inp, cfg), _p3c(inp, cfg), _p3d(inp, cfg)]
    e, veto = _p3e(inp, cfg)
    subs.append(e)
    return subs, veto


def _p3a(inp: ScoringInputs, cfg: Thresholds) -> SubScore:
    pmax = cfg.points_max("P3", "3A")
    fin = inp.financials
    ni, ocf, ta = fin.net_profit_attributable, fin.operating_cash_flow, fin.total_assets
    prior_ta = inp.history.prior_total_assets
    if ni is None or ocf is None or ta is None or prior_ta is None:
        return _missing("3A", 3, pmax, "net income / OCF / total assets (t, t-1) unavailable")
    avg_ta = (ta + prior_ta) / Decimal(2)
    if avg_ta == ZERO:
        return _missing("3A", 3, pmax, "average total assets is zero")
    accrual = (ni - ocf) / avg_ta
    band = match(accrual, cfg.bands("P3", "3A"))
    if band is None:
        return _award("3A", 3, ZERO, pmax, accrual, "default", f"accrual ratio={accrual}")
    return _award("3A", 3, band.payload["points"], pmax, accrual, band.label(), f"accrual ratio={accrual}")


def _p3b(inp: ScoringInputs, cfg: Thresholds) -> SubScore:
    pmax = cfg.points_max("P3", "3B")
    rg, revg = inp.history.receivables_growth, inp.history.revenue_growth
    if rg is None or revg is None:
        return _missing("3B", 3, pmax, "receivables/revenue growth history (>=5q) unavailable")
    if revg <= ZERO:
        return _award("3B", 3, ZERO, pmax, None, "default", "revenue not growing")
    ratio = rg / revg
    band = match(ratio, cfg.bands("P3", "3B"))
    if band is None:
        return _award("3B", 3, ZERO, pmax, ratio, "default", f"rec growth/rev growth={ratio}")
    return _award("3B", 3, band.payload["points"], pmax, ratio, band.label(), f"rec growth/rev growth={ratio}")


def _p3c(inp: ScoringInputs, cfg: Thresholds) -> SubScore:
    pmax = cfg.points_max("P3", "3C")
    fin = inp.financials
    pbt = fin.profit_before_tax
    if pbt is None or pbt <= ZERO:
        return _missing("3C", 3, pmax, "non-positive or missing PBT")
    non_op = (
        fin.get0("interest_income")
        + fin.get0("fx_gain_loss")
        + fin.get0("other_income")
        + fin.get0("share_of_associates")
    )
    ratio = non_op / pbt
    band = match(ratio, cfg.bands("P3", "3C"))
    if band is None:
        return _award("3C", 3, ZERO, pmax, ratio, "default", f"non-operating/PBT={ratio}")
    return _award("3C", 3, band.payload["points"], pmax, ratio, band.label(), f"non-operating/PBT={ratio}")


def _p3d(inp: ScoringInputs, cfg: Thresholds) -> SubScore:
    pmax = cfg.points_max("P3", "3D")
    req = int(cfg.sub_params("P3", "3D")["history_required"])
    margins = inp.history.operating_margins_8q
    if len(margins) < req:
        return _missing("3D", 3, pmax, f"fewer than {req} quarters of operating margin")
    sd = population_stdev(margins)
    band = match(sd, cfg.bands("P3", "3D"))
    if band is None:
        return _award("3D", 3, ZERO, pmax, sd, "default", f"margin stdev={sd}")
    return _award("3D", 3, band.payload["points"], pmax, sd, band.label(), f"margin stdev={sd}")


def _p3e(inp: ScoringInputs, cfg: Thresholds) -> tuple[SubScore, list[str]]:
    pmax = cfg.points_max("P3", "3E")
    pts = cfg.sub_points("P3", "3E")
    op = inp.qualitative.audit_opinion
    if op is None:
        return _missing("3E", 3, pmax, "audit opinion unknown"), []
    if op is AuditOpinion.ADVERSE:
        return _award("3E", 3, ZERO, pmax, None, "adverse", "qualified/adverse/disclaimer opinion"), [VETO_AUDIT]
    mapping = {
        AuditOpinion.CLEAN_IMMATERIAL_RPT: pts["clean_immaterial_rpt"],
        AuditOpinion.CLEAN_MATERIAL_RPT: pts["clean_material_rpt"],
        AuditOpinion.EMPHASIS: pts["emphasis"],
    }
    return _award("3E", 3, mapping[op], pmax, None, op.value, f"audit: {op.value}"), []


# ======================================================================
# PILLAR 4 — Valuation (15)  (§4.4)
# ======================================================================
def _p4(inp: ScoringInputs, cfg: Thresholds) -> list[SubScore]:
    return [_p4a(inp, cfg), _p4b(inp, cfg), _p4c(inp, cfg), _p4d(inp, cfg), _p4e(inp, cfg)]


def _p4a(inp: ScoringInputs, cfg: Thresholds) -> SubScore:
    pmax = cfg.points_max("P4", "4A")
    pe = inp.valuation.pe_ratio
    tbill = inp.macro.tbill_1y_yield
    if pe is None or tbill is None:
        return _missing("4A", 4, pmax, "P/E or 1y T-bill yield unavailable")
    if pe <= ZERO:
        return _award("4A", 4, ZERO, pmax, None, "default", "loss-making (P/E <= 0)")
    erp = (ONE / pe) - tbill
    band = match(erp, cfg.bands("P4", "4A"))
    if band is None:
        return _award("4A", 4, ZERO, pmax, erp, "default", f"ERP={erp}")
    return _award("4A", 4, band.payload["points"], pmax, erp, band.label(), f"ERP={erp}")


def _p4b(inp: ScoringInputs, cfg: Thresholds) -> SubScore:
    pmax = cfg.points_max("P4", "4B")
    ev = inp.valuation.ev_ebitda
    median = inp.history.ev_ebitda_median_5y
    substituted = False
    if median is None:
        median = inp.history.sector_median_ev_ebitda
        substituted = True
    if ev is None or median is None or median == ZERO:
        return _missing("4B", 4, pmax, "EV/EBITDA or 5y/sector median unavailable")
    discount = (median - ev) / median
    band = match(discount, cfg.bands("P4", "4B"))
    note = "sector-median substituted" if substituted else "own 5y median"
    if band is None:
        return _award("4B", 4, ZERO, pmax, discount, "default", f"EV/EBITDA discount={discount} ({note})")
    return _award(
        "4B", 4, band.payload["points"], pmax, discount, band.label(), f"EV/EBITDA discount={discount} ({note})"
    )


def _p4c(inp: ScoringInputs, cfg: Thresholds) -> SubScore:
    pmax = cfg.points_max("P4", "4C")
    p = cfg.sub_params("P4", "4C")
    pts = cfg.sub_points("P4", "4C")
    pb, roe = inp.valuation.pb_ratio, inp.valuation.roe
    if pb is None or roe is None:
        return _missing("4C", 4, pmax, "P/B or ROE unavailable")
    justified = roe / Decimal(str(p["cost_of_equity"]))
    if pb < justified:
        return _award("4C", 4, pts["below"], pmax, pb, "below", f"P/B={pb} < justified {justified}")
    if pb <= justified * (ONE + Decimal(str(p["within_pct"]))):
        return _award("4C", 4, pts["within"], pmax, pb, "within", f"P/B={pb} within 20% of {justified}")
    if pb <= justified * (ONE + Decimal(str(p["near_pct"]))):
        return _award("4C", 4, pts["near"], pmax, pb, "near", f"P/B={pb} within 50% of {justified}")
    return _award("4C", 4, pts["above"], pmax, pb, "above", f"P/B={pb} well above justified {justified}")


def _p4d(inp: ScoringInputs, cfg: Thresholds) -> SubScore:
    pmax = cfg.points_max("P4", "4D")
    fin = inp.financials
    ocf, mcap = fin.operating_cash_flow, inp.market.market_cap
    capex = fin.capex
    if ocf is None or capex is None or mcap is None or mcap == ZERO:
        return _missing("4D", 4, pmax, "OCF / capex / market cap unavailable")
    fcf_yield = (ocf - capex) / mcap
    band = match(fcf_yield, cfg.bands("P4", "4D"))
    if band is None:
        return _award("4D", 4, ZERO, pmax, fcf_yield, "default", f"FCF yield={fcf_yield}")
    return _award("4D", 4, band.payload["points"], pmax, fcf_yield, band.label(), f"FCF yield={fcf_yield}")


def _p4e(inp: ScoringInputs, cfg: Thresholds) -> SubScore:
    pmax = cfg.points_max("P4", "4E")
    p = cfg.sub_params("P4", "4E")
    pts = cfg.sub_points("P4", "4E")
    dy = inp.valuation.dividend_yield
    if dy is None:
        return _missing("4E", 4, pmax, "dividend yield unavailable")
    payout = inp.valuation.payout_of_fcf
    sustainable = inp.valuation.dividend_sustainable
    if dy > Decimal(str(p["high_yield"])) and payout is not None and payout < Decimal(str(p["high_payout_of_fcf_max"])):
        return _award(
            "4E", 4, pts["tier2"], pmax, dy, "tier2", f"yield={dy}, payout<{p['high_payout_of_fcf_max']} of FCF"
        )
    if dy > Decimal(str(p["mid_yield"])) and sustainable:
        return _award("4E", 4, pts["tier1_5"], pmax, dy, "tier1_5", f"yield={dy}, sustainable")
    if dy > Decimal(str(p["low_yield"])):
        return _award("4E", 4, pts["tier1"], pmax, dy, "tier1", f"yield={dy}")
    if dy > ZERO:
        return _award("4E", 4, pts["any"], pmax, dy, "any", f"yield={dy}")
    return _award("4E", 4, pts["none"], pmax, dy, "none", "no dividend")


# ======================================================================
# PILLAR 5 — Growth, Real (10)  (§4.5)
# ======================================================================
def _real_growth(nominal: Decimal, cpi: Decimal) -> Decimal:
    return ((ONE + nominal) / (ONE + cpi)) - ONE


def _p5(inp: ScoringInputs, cfg: Thresholds) -> list[SubScore]:
    return [_p5a(inp, cfg), _p5b(inp, cfg), _p5c(inp, cfg), _p5d(inp, cfg)]


def _p5_real(code: str, nominal: Decimal | None, cpi: Decimal | None, cfg: Thresholds, label: str) -> SubScore:
    pmax = cfg.points_max("P5", code)
    if nominal is None or cpi is None:
        # Never fall back to nominal (§4.5).
        return _missing(code, 5, pmax, f"{label} nominal growth or CPI unavailable")
    real = _real_growth(nominal, cpi)
    band = match(real, cfg.bands("P5", code))
    if band is None:
        return _award(code, 5, ZERO, pmax, real, "default", f"real {label} CAGR={real}")
    return _award(code, 5, band.payload["points"], pmax, real, band.label(), f"real {label} CAGR={real}")


def _p5a(inp: ScoringInputs, cfg: Thresholds) -> SubScore:
    return _p5_real("5A", inp.history.nominal_revenue_cagr_3y, inp.macro.cpi_yoy, cfg, "revenue")


def _p5b(inp: ScoringInputs, cfg: Thresholds) -> SubScore:
    return _p5_real("5B", inp.history.nominal_eps_cagr_3y, inp.macro.cpi_yoy, cfg, "EPS")


def _p5c(inp: ScoringInputs, cfg: Thresholds) -> SubScore:
    pmax = cfg.points_max("P5", "5C")
    vol = inp.history.physical_volume_growth
    if vol is None:
        return _missing("5C", 5, pmax, "physical volume growth not disclosed")
    band = match(vol, cfg.bands("P5", "5C"))
    if band is None:
        return _award("5C", 5, ZERO, pmax, vol, "default", f"volume growth={vol} (declining)")
    return _award("5C", 5, band.payload["points"], pmax, vol, band.label(), f"volume growth={vol}")


def _p5d(inp: ScoringInputs, cfg: Thresholds) -> SubScore:
    pmax = cfg.points_max("P5", "5D")
    pts = cfg.sub_points("P5", "5D")
    roic = inp.qualitative.roic
    if roic is None or roic is RoicCategory.UNKNOWN:
        return _missing("5D", 5, pmax, "ROIC vs cost of capital unavailable")
    mapping = {
        RoicCategory.EXCEEDS_STABLE: pts["exceeds_stable"],
        RoicCategory.MARGINAL: pts["marginal"],
        RoicCategory.BELOW: pts["below"],
    }
    return _award("5D", 5, mapping[roic], pmax, None, roic.value, f"ROIC: {roic.value}")


# ======================================================================
# PILLAR 6 — Technical Strength & Tradability (10)  (§4.6)
# ======================================================================
def _p6(inp: ScoringInputs, cfg: Thresholds) -> tuple[list[SubScore], list[str]]:
    a = _p6a(inp, cfg)
    b, veto = _p6b(inp, cfg)
    c = _p6c(inp, cfg)
    d = _p6d(inp, cfg)
    return [a, b, c, d], veto


def _p6a(inp: ScoringInputs, cfg: Thresholds) -> SubScore:
    pmax = cfg.points_max("P6", "6A")
    pts = cfg.sub_points("P6", "6A")
    tol = Decimal(str(cfg.sub_params("P6", "6A")["below_tolerance"]))
    price, ma200 = inp.market.close_price, inp.market.ma_200
    if price is None or ma200 is None or ma200 == ZERO:
        return _missing("6A", 6, pmax, "price or 200-DMA unavailable")
    ratio = price / ma200
    if price > ma200:
        if inp.market.ma_200_rising:
            return _award("6A", 6, pts["above_rising"], pmax, ratio, "above_rising", "above rising 200-DMA")
        return _award("6A", 6, pts["above_flat"], pmax, ratio, "above_flat", "above flat 200-DMA")
    if price >= ma200 * (ONE - tol):
        return _award("6A", 6, pts["near_below"], pmax, ratio, "near_below", "below 200-DMA by <=10%")
    return _award("6A", 6, pts["far_below"], pmax, ratio, "far_below", "well below 200-DMA")


def _p6b(inp: ScoringInputs, cfg: Thresholds) -> tuple[SubScore, list[str]]:
    """6B — liquidity. Hard veto only on *genuine* illiquidity.

    ``ENGINE_SPEC`` §5.2.7 phrases the veto as "6B = 0". We fire the liquidity
    veto only when the position/ADTV ratio is *computable and exceeds the
    veto threshold* — i.e. actually untradeable. Missing ADTV data yields
    MISSING_DATA with no veto; that path is a data-sufficiency concern handled
    by validation/the gate, not a reason to permanently remove a company.
    """
    pmax = cfg.points_max("P6", "6B")
    veto_above = Decimal(str(cfg.sub("P6", "6B")["veto_above"]))
    adtv, target = inp.market.adtv_60d, inp.target_position_value
    if adtv is None or adtv == ZERO or target is None:
        return _missing("6B", 6, pmax, "ADTV(60) or target position size unavailable"), []
    ratio = target / adtv
    band = match(ratio, cfg.bands("P6", "6B"))
    if band is None:
        # exceeds all bands -> illiquid -> 0 + veto
        return _award("6B", 6, ZERO, pmax, ratio, "veto", f"position/ADTV={ratio} > {veto_above}"), [VETO_LIQUIDITY]
    return _award("6B", 6, band.payload["points"], pmax, ratio, band.label(), f"position/ADTV={ratio}"), []


def _p6c(inp: ScoringInputs, cfg: Thresholds) -> SubScore:
    pmax = cfg.points_max("P6", "6C")
    p = cfg.sub_params("P6", "6C")
    pts = cfg.sub_points("P6", "6C")
    ma50, ma200, rsi = inp.market.ma_50, inp.market.ma_200, inp.market.rsi_14
    if ma50 is None or ma200 is None or rsi is None:
        return _missing("6C", 6, pmax, "MA50/MA200/RSI unavailable")
    if rsi > Decimal(str(p["rsi_overbought"])) or rsi < Decimal(str(p["rsi_oversold"])):
        return _award("6C", 6, pts["extreme"], pmax, rsi, "extreme", f"RSI={rsi} extreme")
    cond_ma = ma50 > ma200
    cond_rsi = Decimal(str(p["rsi_low"])) <= rsi <= Decimal(str(p["rsi_high"]))
    count = int(cond_ma) + int(cond_rsi)
    if count == 2:
        return _award("6C", 6, pts["both"], pmax, rsi, "both", f"MA50>MA200 and RSI={rsi} healthy")
    if count == 1:
        return _award("6C", 6, pts["either"], pmax, rsi, "either", f"one of MA-cross / RSI-band met (RSI={rsi})")
    return _award("6C", 6, ZERO, pmax, rsi, "neither", f"neither condition met (RSI={rsi})")


def _p6d(inp: ScoringInputs, cfg: Thresholds) -> SubScore:
    pmax = cfg.points_max("P6", "6D")
    near = Decimal(str(cfg.sub_params("P6", "6D")["near_pct"]))
    pts = cfg.sub_points("P6", "6D")
    rs = inp.market.rel_strength_6m_vs_egx30
    if rs is None:
        return _missing("6D", 6, pmax, "6-month relative strength unavailable")
    if rs > near:
        return _award("6D", 6, pts["outperforming"], pmax, rs, "outperforming", f"RS={rs} vs EGX30")
    if rs >= -near:
        return _award("6D", 6, pts["near"], pmax, rs, "near", f"RS={rs} within +/-5%")
    return _award("6D", 6, pts["underperforming"], pmax, rs, "underperforming", f"RS={rs} lagging")


# ======================================================================
# PILLAR 7 — Governance (5)  (§4.7)
# ======================================================================
def _p7(inp: ScoringInputs, cfg: Thresholds) -> list[SubScore]:
    return [_p7a(inp, cfg), _p7b(inp, cfg), _p7c(inp, cfg), _p7d(inp, cfg), _p7e(inp, cfg)]


def _p7a(inp: ScoringInputs, cfg: Thresholds) -> SubScore:
    pmax = cfg.points_max("P7", "7A")
    pts = cfg.sub_points("P7", "7A")
    t = inp.governance.timeliness
    if t is None:
        return _missing("7A", 7, pmax, "filing timeliness unknown")
    mapping = {
        Timeliness.ON_TIME_FULL: pts["on_time_full"],
        Timeliness.ON_TIME_THIN: pts["on_time_thin"],
        Timeliness.OCCASIONAL_DELAY: pts["occasional_delay"],
        Timeliness.REPEATED: pts["repeated"],
    }
    return _award("7A", 7, mapping[t], pmax, None, t.value, f"timeliness: {t.value}")


def _p7b(inp: ScoringInputs, cfg: Thresholds) -> SubScore:
    pmax = cfg.points_max("P7", "7B")
    min_frac = Decimal(str(cfg.sub_params("P7", "7B")["min_independent_fraction"]))
    pts = cfg.sub_points("P7", "7B")
    frac = inp.governance.independent_fraction
    if frac is None:
        return _missing("7B", 7, pmax, "board independence unknown")
    if frac >= min_frac and inp.governance.chair_is_ceo is False:
        return _award(
            "7B", 7, pts["independent_split"], pmax, frac, "independent_split", ">=1/3 independent, chair!=CEO"
        )
    if frac >= min_frac:
        return _award("7B", 7, pts["independent_only"], pmax, frac, "independent_only", ">=1/3 independent")
    return _award("7B", 7, pts["weak"], pmax, frac, "weak", f"independent fraction={frac}")


def _p7c(inp: ScoringInputs, cfg: Thresholds) -> SubScore:
    pmax = cfg.points_max("P7", "7C")
    p = cfg.sub_params("P7", "7C")
    pts = cfg.sub_points("P7", "7C")
    ff = inp.governance.free_float
    if ff is None:
        return _missing("7C", 7, pmax, "free float unknown")
    if ff > Decimal(str(p["high_float"])) and inp.governance.clean_minority_record:
        return _award("7C", 7, pts["high_clean"], pmax, ff, "high_clean", f"free float={ff}, clean record")
    if Decimal(str(p["mid_float"])) <= ff <= Decimal(str(p["high_float"])):
        return _award("7C", 7, pts["mid"], pmax, ff, "mid", f"free float={ff}")
    return _award("7C", 7, pts["low"], pmax, ff, "low", f"free float={ff}")


def _p7d(inp: ScoringInputs, cfg: Thresholds) -> SubScore:
    pmax = cfg.points_max("P7", "7D")
    pts = cfg.sub_points("P7", "7D")
    rpt = inp.governance.related_party
    if rpt is None:
        return _missing("7D", 7, pmax, "related-party quality unknown")
    mapping = {
        RelatedPartyQuality.IMMATERIAL_DISCLOSED: pts["immaterial_disclosed"],
        RelatedPartyQuality.MATERIAL_ARMS_LENGTH: pts["material_arms_length"],
        RelatedPartyQuality.MATERIAL_OPAQUE: pts["material_opaque"],
    }
    return _award("7D", 7, mapping[rpt], pmax, None, rpt.value, f"RPT: {rpt.value}")


def _p7e(inp: ScoringInputs, cfg: Thresholds) -> SubScore:
    pmax = cfg.points_max("P7", "7E")
    pts = cfg.sub_points("P7", "7E")
    ca = inp.governance.capital_allocation
    if ca is CapitalAllocation.CONSISTENT:
        return _award("7E", 7, pts["consistent"], pmax, None, "consistent", "consistent, rational allocation")
    return _award("7E", 7, pts["else"], pmax, None, "else", "capital allocation not established")


# ======================================================================
# Assembly
# ======================================================================
def _classify_band(total: Decimal, cfg: Thresholds) -> str:
    band = match(total, cfg.score_bands())
    if band is None:
        raise AssertionError("score bands must cover the whole 0..100 range")
    return str(band.payload["band"])


def assert_pillar_maxima(cfg: Thresholds) -> None:
    """Guard: pillar maxima sum to exactly 100 (ENGINE_SPEC §4.8)."""
    maxima = cfg.pillar_maxima()
    total = sum(maxima.values(), ZERO)
    if total != cfg.total_max:
        raise AssertionError(f"pillar maxima sum to {total}, expected {cfg.total_max}")


def score_company(
    inp: ScoringInputs,
    gate: GateResult,
    cfg: Thresholds,
    *,
    data_status: DataStatus = DataStatus.VALIDATED,
) -> ScoreResult | None:
    """Score a gate-passing, validated company. Returns ``None`` otherwise.

    Gate first (R7): a company that is not GREEN/AMBER or not VALIDATED is
    never scored — the caller must route it to the decision engine's Shariah
    override, not to a score of zero.
    """
    assert_pillar_maxima(cfg)
    if data_status is not DataStatus.VALIDATED:
        return None
    if gate.overall_status not in (ShariahStatus.GREEN, ShariahStatus.AMBER):
        return None

    subs: list[SubScore] = []
    vetoes: list[str] = []
    alerts: list[str] = []

    subs += _p1(inp, gate, cfg)
    subs += _p2(inp, cfg)
    p3, p3_veto = _p3(inp, cfg)
    subs += p3
    vetoes += p3_veto
    subs += _p4(inp, cfg)
    subs += _p5(inp, cfg)
    p6, p6_veto = _p6(inp, cfg)
    subs += p6
    vetoes += p6_veto
    subs += _p7(inp, cfg)

    pillar_totals = {f"P{p}": ZERO for p in range(1, 8)}
    for s in subs:
        pillar_totals[f"P{s.pillar}"] += s.points
    total = sum(pillar_totals.values(), ZERO)
    band = _classify_band(total, cfg)

    return ScoreResult(
        subscores=tuple(subs),
        pillar_totals=pillar_totals,
        total=total,
        band=band,
        vetoes=tuple(vetoes),
        alerts=tuple(alerts),
    )
