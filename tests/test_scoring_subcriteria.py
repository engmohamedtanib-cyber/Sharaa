"""Exhaustive band + missing-data coverage for every scoring sub-criterion.

Calls the private ``_pXY`` helpers directly so each band and each MISSING_DATA
branch is exercised (ENGINE_SPEC §4; CLAUDE.md §6 branch-coverage requirement).
"""

from __future__ import annotations

import pytest

from conftest import D, clean_financials
from engine import scoring as sc
from engine.types import (
    AuditOpinion,
    CapitalAllocation,
    FxResilience,
    Governance,
    History,
    MacroData,
    MarketData,
    Qualitative,
    RelatedPartyQuality,
    RoicCategory,
    ScoringInputs,
    Timeliness,
    Valuation,
)


def si(**groups) -> ScoringInputs:
    return ScoringInputs(
        financials=groups.get("financials", clean_financials()),
        market=groups.get("market", MarketData()),
        macro=groups.get("macro", MacroData()),
        history=groups.get("history", History()),
        valuation=groups.get("valuation", Valuation()),
        governance=groups.get("governance", Governance()),
        qualitative=groups.get("qualitative", Qualitative()),
        target_position_value=groups.get("target"),
    )


# ---- 1E ----------------------------------------------------------------
def test_1e_strong(cfg):
    h = History(worst_util_last4=(D("0.3"), D("0.27"), D("0.25"), D("0.24")), consecutive_compliant_quarters=8)
    assert sc._p1e(si(history=h), cfg).points == D("2")


def test_1e_stable(cfg):
    h = History(worst_util_last4=(D("0.3"), D("0.27"), D("0.25"), D("0.24")), consecutive_compliant_quarters=4)
    assert sc._p1e(si(history=h), cfg).points == D("1.5")


def test_1e_deteriorating(cfg):
    h = History(worst_util_last4=(D("0.24"), D("0.25"), D("0.27"), D("0.3")), consecutive_compliant_quarters=5)
    assert sc._p1e(si(history=h), cfg).points == D("0.75")


def test_1e_insufficient_streak(cfg):
    h = History(worst_util_last4=(D("0.3"), D("0.27"), D("0.25"), D("0.24")), consecutive_compliant_quarters=2)
    assert sc._p1e(si(history=h), cfg).points == D("0")


def test_1e_missing_history(cfg):
    assert sc._p1e(si(history=History(worst_util_last4=(D("0.3"),))), cfg).band_matched == "MISSING_DATA"


# ---- 2A ----------------------------------------------------------------
def test_2a_net_cash(cfg):
    fin = clean_financials(cash_and_equivalents=D("500"), time_deposits=D("0"))
    assert sc._p2a(si(financials=fin), cfg).band_matched == "net_cash"


@pytest.mark.parametrize("debt,ebitda,expected", [("100", "150", "4"), ("250", "150", "3"), ("400", "150", "1.5"), ("550", "150", "0.5"), ("700", "150", "0")])
def test_2a_bands(cfg, debt, ebitda, expected):
    fin = clean_financials(short_term_borrowings=D(debt), long_term_borrowings=D("0"),
                           cash_and_equivalents=D("0"), time_deposits=D("0"), operating_profit=D(ebitda),
                           depreciation=D("0"), amortisation=D("0"))
    assert sc._p2a(si(financials=fin), cfg).points == D(expected)


def test_2a_missing_ebitda(cfg):
    fin = clean_financials(short_term_borrowings=D("100"), cash_and_equivalents=D("0"),
                           time_deposits=D("0"), operating_profit=None)
    assert sc._p2a(si(financials=fin), cfg).band_matched == "MISSING_DATA"


def test_2a_nonpositive_ebitda(cfg):
    fin = clean_financials(short_term_borrowings=D("100"), cash_and_equivalents=D("0"),
                           time_deposits=D("0"), operating_profit=D("-10"), depreciation=D("0"), amortisation=D("0"))
    assert sc._p2a(si(financials=fin), cfg).points == D("0")


# ---- 2B ----------------------------------------------------------------
def test_2b_no_finance_cost(cfg):
    fin = clean_financials(finance_cost=None)
    assert sc._p2b(si(financials=fin), cfg).band_matched == "no_finance_cost"


@pytest.mark.parametrize("op,fc,expected", [("110", "10", "4"), ("70", "10", "3"), ("40", "10", "2"), ("18", "10", "1"), ("5", "10", "0")])
def test_2b_bands(cfg, op, fc, expected):
    fin = clean_financials(operating_profit=D(op), finance_cost=D(fc))
    assert sc._p2b(si(financials=fin), cfg).points == D(expected)


def test_2b_missing_op(cfg):
    fin = clean_financials(operating_profit=None, finance_cost=D("10"))
    assert sc._p2b(si(financials=fin), cfg).band_matched == "MISSING_DATA"


# ---- 2C ----------------------------------------------------------------
@pytest.mark.parametrize("ca,cl,inv,expected", [
    ("300", "100", "30", "4"),   # CR 3, QR 2.7
    ("170", "100", "60", "3"),   # CR 1.7, QR 1.1
    ("130", "100", "80", "2"),   # CR 1.3
    ("110", "100", "80", "1"),   # CR 1.1
    ("90", "100", "10", "0"),    # CR 0.9
])
def test_2c_tiers(cfg, ca, cl, inv, expected):
    fin = clean_financials(total_current_assets=D(ca), total_current_liabilities=D(cl), inventory=D(inv))
    assert sc._p2c(si(financials=fin), cfg).points == D(expected)


def test_2c_missing(cfg):
    fin = clean_financials(total_current_assets=None)
    assert sc._p2c(si(financials=fin), cfg).band_matched == "MISSING_DATA"


# ---- 2D ----------------------------------------------------------------
@pytest.mark.parametrize("ocf,ni,expected", [("130", "100", "4"), ("110", "100", "3.5"), ("90", "100", "2.5"), ("70", "100", "1"), ("50", "100", "0")])
def test_2d_bands(cfg, ocf, ni, expected):
    assert sc._p2d(si(history=History(ocf_ttm=D(ocf), net_income_ttm=D(ni))), cfg).points == D(expected)


def test_2d_missing(cfg):
    assert sc._p2d(si(history=History(ocf_ttm=D("100"))), cfg).band_matched == "MISSING_DATA"


def test_2d_nonpositive_ni(cfg):
    assert sc._p2d(si(history=History(ocf_ttm=D("100"), net_income_ttm=D("-1"))), cfg).points == D("0")


# ---- 2E ----------------------------------------------------------------
@pytest.mark.parametrize("fx,expected", [
    (FxResilience.HEDGED, "3"), (FxResilience.BALANCED, "2"),
    (FxResilience.SMALL_LIABILITY, "1"), (FxResilience.EXPOSED, "0"),
])
def test_2e_categories(cfg, fx, expected):
    assert sc._p2e(si(qualitative=Qualitative(fx_resilience=fx)), cfg).points == D(expected)


def test_2e_unknown_missing(cfg):
    assert sc._p2e(si(qualitative=Qualitative(fx_resilience=FxResilience.UNKNOWN)), cfg).band_matched == "MISSING_DATA"
    assert sc._p2e(si(), cfg).band_matched == "MISSING_DATA"


# ---- 3A ----------------------------------------------------------------
@pytest.mark.parametrize("ni,ocf,expected", [("50", "150", "4"), ("100", "80", "3"), ("100", "50", "2"), ("150", "60", "1"), ("300", "50", "0")])
def test_3a_bands(cfg, ni, ocf, expected):
    fin = clean_financials(net_profit_attributable=D(ni), operating_cash_flow=D(ocf), total_assets=D("1000"))
    h = History(prior_total_assets=D("1000"))
    assert sc._p3a(si(financials=fin, history=h), cfg).points == D(expected)


def test_3a_missing_prior(cfg):
    assert sc._p3a(si(history=History()), cfg).band_matched == "MISSING_DATA"


def test_3a_zero_avg_assets(cfg):
    fin = clean_financials(total_assets=D("0"), net_profit_attributable=D("10"), operating_cash_flow=D("10"))
    assert sc._p3a(si(financials=fin, history=History(prior_total_assets=D("0"))), cfg).band_matched == "MISSING_DATA"


# ---- 3B ----------------------------------------------------------------
@pytest.mark.parametrize("rg,revg,expected", [("0.05", "0.10", "3"), ("0.11", "0.10", "2"), ("0.14", "0.10", "1"), ("0.20", "0.10", "0")])
def test_3b_bands(cfg, rg, revg, expected):
    assert sc._p3b(si(history=History(receivables_growth=D(rg), revenue_growth=D(revg))), cfg).points == D(expected)


def test_3b_missing(cfg):
    assert sc._p3b(si(history=History(receivables_growth=D("0.1"))), cfg).band_matched == "MISSING_DATA"


def test_3b_nonpositive_revenue_growth(cfg):
    assert sc._p3b(si(history=History(receivables_growth=D("0.1"), revenue_growth=D("0"))), cfg).points == D("0")


# ---- 3C ----------------------------------------------------------------
@pytest.mark.parametrize("interest,pbt,expected", [("2", "100", "3"), ("10", "100", "2"), ("20", "100", "1"), ("40", "100", "0")])
def test_3c_bands(cfg, interest, pbt, expected):
    fin = clean_financials(interest_income=D(interest), other_income=D("0"), fx_gain_loss=D("0"),
                           share_of_associates=D("0"), profit_before_tax=D(pbt))
    assert sc._p3c(si(financials=fin), cfg).points == D(expected)


def test_3c_missing_nonpositive_pbt(cfg):
    fin = clean_financials(profit_before_tax=D("-1"))
    assert sc._p3c(si(financials=fin), cfg).band_matched == "MISSING_DATA"


# ---- 3D ----------------------------------------------------------------
@pytest.mark.parametrize("margins,expected", [
    (("0.10",) * 8, "3"),
    (("0.10", "0.14", "0.10", "0.14", "0.10", "0.14", "0.10", "0.14"), "2"),  # stdev 0.02
    (("0.06", "0.14", "0.06", "0.14", "0.06", "0.14", "0.06", "0.14"), "1"),  # stdev 0.04
    (("0.00", "0.20", "0.00", "0.20", "0.00", "0.20", "0.00", "0.20"), "0"),  # stdev 0.10
])
def test_3d_bands(cfg, margins, expected):
    h = History(operating_margins_8q=tuple(D(m) for m in margins))
    assert sc._p3d(si(history=h), cfg).points == D(expected)


def test_3d_insufficient_history(cfg):
    assert sc._p3d(si(history=History(operating_margins_8q=(D("0.1"),))), cfg).band_matched == "MISSING_DATA"


# ---- 3E ----------------------------------------------------------------
@pytest.mark.parametrize("op,expected", [
    (AuditOpinion.CLEAN_IMMATERIAL_RPT, "2"), (AuditOpinion.CLEAN_MATERIAL_RPT, "1"),
    (AuditOpinion.EMPHASIS, "0.5"),
])
def test_3e_categories(cfg, op, expected):
    sub, veto = sc._p3e(si(qualitative=Qualitative(audit_opinion=op)), cfg)
    assert sub.points == D(expected) and veto == []


def test_3e_adverse_veto(cfg):
    sub, veto = sc._p3e(si(qualitative=Qualitative(audit_opinion=AuditOpinion.ADVERSE)), cfg)
    assert sub.points == D("0") and veto == [sc.VETO_AUDIT]


def test_3e_missing(cfg):
    sub, veto = sc._p3e(si(), cfg)
    assert sub.band_matched == "MISSING_DATA" and veto == []


# ---- 4A ----------------------------------------------------------------
@pytest.mark.parametrize("pe,tbill,expected", [("2", "0.19", "4"), ("4", "0.13", "3"), ("6", "0.11", "2"), ("6", "0.16", "1"), ("6", "0.18", "0")])
def test_4a_bands(cfg, pe, tbill, expected):
    inp = si(valuation=Valuation(pe_ratio=D(pe)), macro=MacroData(tbill_1y_yield=D(tbill)))
    assert sc._p4a(inp, cfg).points == D(expected)


def test_4a_loss_making(cfg):
    inp = si(valuation=Valuation(pe_ratio=D("-2")), macro=MacroData(tbill_1y_yield=D("0.19")))
    assert sc._p4a(inp, cfg).points == D("0")


def test_4a_missing(cfg):
    assert sc._p4a(si(valuation=Valuation(pe_ratio=D("5"))), cfg).band_matched == "MISSING_DATA"


# ---- 4B ----------------------------------------------------------------
@pytest.mark.parametrize("ev,median,expected", [("4", "7", "3"), ("5.5", "7", "2"), ("7", "7", "1"), ("8.3", "7", "0.5"), ("20", "7", "0")])
def test_4b_bands(cfg, ev, median, expected):
    inp = si(valuation=Valuation(ev_ebitda=D(ev)), history=History(ev_ebitda_median_5y=D(median)))
    assert sc._p4b(inp, cfg).points == D(expected)


def test_4b_sector_substitution(cfg):
    inp = si(valuation=Valuation(ev_ebitda=D("4")), history=History(sector_median_ev_ebitda=D("7")))
    assert "sector-median substituted" in sc._p4b(inp, cfg).rationale


def test_4b_missing(cfg):
    assert sc._p4b(si(valuation=Valuation(ev_ebitda=D("4"))), cfg).band_matched == "MISSING_DATA"


# ---- 4C ----------------------------------------------------------------
@pytest.mark.parametrize("pb,roe,expected", [("1.0", "0.25", "3"), ("1.3", "0.25", "2"), ("1.8", "0.25", "1"), ("3.0", "0.25", "0")])
def test_4c_tiers(cfg, pb, roe, expected):
    assert sc._p4c(si(valuation=Valuation(pb_ratio=D(pb), roe=D(roe))), cfg).points == D(expected)


def test_4c_missing(cfg):
    assert sc._p4c(si(valuation=Valuation(pb_ratio=D("1"))), cfg).band_matched == "MISSING_DATA"


# ---- 4D ----------------------------------------------------------------
@pytest.mark.parametrize("ocf,capex,mcap,expected", [("400", "0", "2000", "3"), ("250", "0", "2000", "2"), ("150", "0", "2000", "1"), ("50", "0", "2000", "0.5"), ("0", "0", "2000", "0")])
def test_4d_bands(cfg, ocf, capex, mcap, expected):
    fin = clean_financials(operating_cash_flow=D(ocf), capex=D(capex))
    inp = si(financials=fin, market=MarketData(market_cap=D(mcap)))
    assert sc._p4d(inp, cfg).points == D(expected)


def test_4d_missing(cfg):
    fin = clean_financials(capex=None)
    assert sc._p4d(si(financials=fin, market=MarketData(market_cap=D("2000"))), cfg).band_matched == "MISSING_DATA"


# ---- 4E ----------------------------------------------------------------
def test_4e_tier2(cfg):
    v = Valuation(dividend_yield=D("0.09"), payout_of_fcf=D("0.5"))
    assert sc._p4e(si(valuation=v), cfg).points == D("2")


def test_4e_tier1_5(cfg):
    v = Valuation(dividend_yield=D("0.06"), dividend_sustainable=True)
    assert sc._p4e(si(valuation=v), cfg).points == D("1.5")


def test_4e_tier1(cfg):
    assert sc._p4e(si(valuation=Valuation(dividend_yield=D("0.04"))), cfg).points == D("1")


def test_4e_any(cfg):
    assert sc._p4e(si(valuation=Valuation(dividend_yield=D("0.01"))), cfg).points == D("0.5")


def test_4e_none(cfg):
    assert sc._p4e(si(valuation=Valuation(dividend_yield=D("0"))), cfg).points == D("0")


def test_4e_missing(cfg):
    assert sc._p4e(si(), cfg).band_matched == "MISSING_DATA"


# ---- 5A / 5B -----------------------------------------------------------
@pytest.mark.parametrize("nominal,expected", [("0.30", "4"), ("0.21", "3"), ("0.16", "2"), ("0.13", "1"), ("0.12", "0")])
def test_5a_bands(cfg, nominal, expected):
    inp = si(history=History(nominal_revenue_cagr_3y=D(nominal)), macro=MacroData(cpi_yoy=D("0.12")))
    assert sc._p5a(inp, cfg).points == D(expected)


def test_5a_missing_cpi(cfg):
    inp = si(history=History(nominal_revenue_cagr_3y=D("0.2")))
    assert sc._p5a(inp, cfg).band_matched == "MISSING_DATA"


def test_5b_band(cfg):
    inp = si(history=History(nominal_eps_cagr_3y=D("0.30")), macro=MacroData(cpi_yoy=D("0.12")))
    assert sc._p5b(inp, cfg).points == D("3")


# ---- 5C ----------------------------------------------------------------
@pytest.mark.parametrize("vol,expected", [("0.12", "2"), ("0.07", "1.5"), ("0.02", "1"), ("0", "0.5"), ("-0.05", "0")])
def test_5c_bands(cfg, vol, expected):
    assert sc._p5c(si(history=History(physical_volume_growth=D(vol))), cfg).points == D(expected)


def test_5c_missing(cfg):
    assert sc._p5c(si(), cfg).band_matched == "MISSING_DATA"


# ---- 5D ----------------------------------------------------------------
@pytest.mark.parametrize("roic,expected", [(RoicCategory.EXCEEDS_STABLE, "1"), (RoicCategory.MARGINAL, "0.5"), (RoicCategory.BELOW, "0")])
def test_5d_categories(cfg, roic, expected):
    assert sc._p5d(si(qualitative=Qualitative(roic=roic)), cfg).points == D(expected)


def test_5d_missing(cfg):
    assert sc._p5d(si(qualitative=Qualitative(roic=RoicCategory.UNKNOWN)), cfg).band_matched == "MISSING_DATA"


# ---- 6A ----------------------------------------------------------------
def test_6a_above_rising(cfg):
    m = MarketData(close_price=D("12"), ma_200=D("10"), ma_200_rising=True)
    assert sc._p6a(si(market=m), cfg).points == D("3")


def test_6a_above_flat(cfg):
    m = MarketData(close_price=D("12"), ma_200=D("10"), ma_200_rising=False)
    assert sc._p6a(si(market=m), cfg).points == D("2")


def test_6a_near_below(cfg):
    m = MarketData(close_price=D("9.5"), ma_200=D("10"))
    assert sc._p6a(si(market=m), cfg).points == D("1")


def test_6a_far_below(cfg):
    m = MarketData(close_price=D("8"), ma_200=D("10"))
    assert sc._p6a(si(market=m), cfg).points == D("0")


def test_6a_missing(cfg):
    assert sc._p6a(si(market=MarketData(close_price=D("10"))), cfg).band_matched == "MISSING_DATA"


# ---- 6B ----------------------------------------------------------------
@pytest.mark.parametrize("target,adtv,expected", [("1000", "200000", "4"), ("5000", "200000", "3"), ("9000", "200000", "2"), ("18000", "200000", "1")])
def test_6b_bands(cfg, target, adtv, expected):
    sub, veto = sc._p6b(si(market=MarketData(adtv_60d=D(adtv)), target=D(target)), cfg)
    assert sub.points == D(expected) and veto == []


def test_6b_veto(cfg):
    sub, veto = sc._p6b(si(market=MarketData(adtv_60d=D("200000")), target=D("30000")), cfg)
    assert sub.points == D("0") and veto == [sc.VETO_LIQUIDITY]


def test_6b_missing_no_veto(cfg):
    sub, veto = sc._p6b(si(market=MarketData(adtv_60d=None), target=D("1000")), cfg)
    assert sub.band_matched == "MISSING_DATA" and veto == []


# ---- 6C ----------------------------------------------------------------
def test_6c_extreme(cfg):
    assert sc._p6c(si(market=MarketData(ma_50=D("11"), ma_200=D("10"), rsi_14=D("80"))), cfg).points == D("0")


def test_6c_both(cfg):
    assert sc._p6c(si(market=MarketData(ma_50=D("11"), ma_200=D("10"), rsi_14=D("55"))), cfg).points == D("2")


def test_6c_either(cfg):
    assert sc._p6c(si(market=MarketData(ma_50=D("9"), ma_200=D("10"), rsi_14=D("55"))), cfg).points == D("1")


def test_6c_neither(cfg):
    assert sc._p6c(si(market=MarketData(ma_50=D("9"), ma_200=D("10"), rsi_14=D("30"))), cfg).points == D("0")


def test_6c_missing(cfg):
    assert sc._p6c(si(market=MarketData(ma_50=D("11"))), cfg).band_matched == "MISSING_DATA"


# ---- 6D ----------------------------------------------------------------
@pytest.mark.parametrize("rs,expected", [("0.08", "1"), ("0.00", "0.5"), ("-0.08", "0")])
def test_6d_bands(cfg, rs, expected):
    assert sc._p6d(si(market=MarketData(rel_strength_6m_vs_egx30=D(rs))), cfg).points == D(expected)


def test_6d_missing(cfg):
    assert sc._p6d(si(), cfg).band_matched == "MISSING_DATA"


# ---- 7A ----------------------------------------------------------------
@pytest.mark.parametrize("t,expected", [
    (Timeliness.ON_TIME_FULL, "1.5"), (Timeliness.ON_TIME_THIN, "1"),
    (Timeliness.OCCASIONAL_DELAY, "0.5"), (Timeliness.REPEATED, "0"),
])
def test_7a(cfg, t, expected):
    assert sc._p7a(si(governance=Governance(timeliness=t)), cfg).points == D(expected)


def test_7a_missing(cfg):
    assert sc._p7a(si(), cfg).band_matched == "MISSING_DATA"


# ---- 7B ----------------------------------------------------------------
def test_7b_split(cfg):
    g = Governance(independent_fraction=D("0.4"), chair_is_ceo=False)
    assert sc._p7b(si(governance=g), cfg).points == D("1")


def test_7b_only(cfg):
    g = Governance(independent_fraction=D("0.4"), chair_is_ceo=True)
    assert sc._p7b(si(governance=g), cfg).points == D("0.5")


def test_7b_weak(cfg):
    assert sc._p7b(si(governance=Governance(independent_fraction=D("0.2"))), cfg).points == D("0")


def test_7b_missing(cfg):
    assert sc._p7b(si(), cfg).band_matched == "MISSING_DATA"


# ---- 7C ----------------------------------------------------------------
def test_7c_high_clean(cfg):
    g = Governance(free_float=D("0.35"), clean_minority_record=True)
    assert sc._p7c(si(governance=g), cfg).points == D("1")


def test_7c_mid(cfg):
    assert sc._p7c(si(governance=Governance(free_float=D("0.20"))), cfg).points == D("0.5")


def test_7c_low(cfg):
    assert sc._p7c(si(governance=Governance(free_float=D("0.10"))), cfg).points == D("0")


def test_7c_missing(cfg):
    assert sc._p7c(si(), cfg).band_matched == "MISSING_DATA"


# ---- 7D ----------------------------------------------------------------
@pytest.mark.parametrize("rpt,expected", [
    (RelatedPartyQuality.IMMATERIAL_DISCLOSED, "1"),
    (RelatedPartyQuality.MATERIAL_ARMS_LENGTH, "0.5"),
    (RelatedPartyQuality.MATERIAL_OPAQUE, "0"),
])
def test_7d(cfg, rpt, expected):
    assert sc._p7d(si(governance=Governance(related_party=rpt)), cfg).points == D(expected)


def test_7d_missing(cfg):
    assert sc._p7d(si(), cfg).band_matched == "MISSING_DATA"


# ---- 7E ----------------------------------------------------------------
def test_7e_consistent(cfg):
    g = Governance(capital_allocation=CapitalAllocation.CONSISTENT)
    assert sc._p7e(si(governance=g), cfg).points == D("0.5")


def test_7e_else(cfg):
    g = Governance(capital_allocation=CapitalAllocation.ELSE)
    assert sc._p7e(si(governance=g), cfg).points == D("0")
