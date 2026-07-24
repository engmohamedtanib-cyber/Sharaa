"""Scoring model: gate-first, maxima, missing-data, vetoes (ENGINE_SPEC §4)."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from conftest import D, clean_financials, strong_inputs
from engine.scoring import (
    VETO_AUDIT,
    VETO_LIQUIDITY,
    assert_pillar_maxima,
    score_company,
)
from engine.shariah import run_gate
from engine.types import (
    AuditOpinion,
    DataStatus,
    FxResilience,
    ShariahStatus,
)


def test_pillar_maxima_sum_to_100(cfg):
    assert_pillar_maxima(cfg)  # raises if != 100


def test_gate_first_failing_company_not_scored(cfg):
    # Screen E RED via huge receivables -> gate fails -> None, not zero (R7).
    fin = clean_financials(accounts_receivable=D("600"))
    gate = run_gate(fin, D("1000"), cfg)
    assert gate.passed is False
    assert score_company(strong_inputs(fin), gate, cfg) is None


def test_unvalidated_never_scored(cfg):
    inp = strong_inputs()
    gate = run_gate(inp.financials, D("2400"), cfg)
    assert score_company(inp, gate, cfg, data_status=DataStatus.INSUFFICIENT) is None
    assert score_company(inp, gate, cfg, data_status=DataStatus.CONFLICT) is None


def test_max_company_scores_exceptional(cfg):
    inp = strong_inputs()
    gate = run_gate(inp.financials, inp.market.market_cap, cfg)
    res = score_company(inp, gate, cfg)
    assert res is not None
    assert res.total == D("93.25")
    assert res.band == "EXCEPTIONAL"
    assert res.pillar_totals["P1"] == D("25")
    assert res.pillar_totals["P2"] == D("20")
    assert res.pillar_totals["P3"] == D("15")
    assert res.pillar_totals["P6"] == D("10")
    assert res.pillar_totals["P7"] == D("5")


def test_missing_input_scores_zero_missing_data(cfg):
    inp = strong_inputs()
    # Remove FX resilience -> 2E MISSING_DATA, 0 points.
    inp = replace(inp, qualitative=replace(inp.qualitative, fx_resilience=FxResilience.UNKNOWN))
    gate = run_gate(inp.financials, inp.market.market_cap, cfg)
    res = score_company(inp, gate, cfg)
    sub = res.sub("2E")
    assert sub.points == D("0")
    assert sub.band_matched == "MISSING_DATA"


def test_total_scores_sum_to_pillars(cfg):
    inp = strong_inputs()
    gate = run_gate(inp.financials, inp.market.market_cap, cfg)
    res = score_company(inp, gate, cfg)
    assert res.total == sum(res.pillar_totals.values(), Decimal(0))


def test_audit_adverse_fires_veto_and_zero(cfg):
    inp = strong_inputs()
    inp = replace(inp, qualitative=replace(inp.qualitative, audit_opinion=AuditOpinion.ADVERSE))
    gate = run_gate(inp.financials, inp.market.market_cap, cfg)
    res = score_company(inp, gate, cfg)
    assert res.sub("3E").points == D("0")
    assert VETO_AUDIT in res.vetoes


def test_liquidity_veto_when_position_exceeds_adtv(cfg):
    inp = strong_inputs()
    # Target far exceeds ADTV -> 6B ratio > 0.10 -> veto.
    inp = replace(inp, target_position_value=D("100000"), market=replace(inp.market, adtv_60d=D("200000")))
    gate = run_gate(inp.financials, inp.market.market_cap, cfg)
    res = score_company(inp, gate, cfg)
    assert res.sub("6B").points == D("0")
    assert VETO_LIQUIDITY in res.vetoes


def test_loss_making_4a_scores_zero(cfg):

    inp = strong_inputs()
    inp = replace(inp, valuation=replace(inp.valuation, pe_ratio=D("-3")))
    gate = run_gate(inp.financials, inp.market.market_cap, cfg)
    res = score_company(inp, gate, cfg)
    assert res.sub("4A").points == D("0")


def test_missing_cpi_never_falls_back_to_nominal(cfg):
    inp = strong_inputs()
    inp = replace(inp, macro=replace(inp.macro, cpi_yoy=None))
    gate = run_gate(inp.financials, inp.market.market_cap, cfg)
    res = score_company(inp, gate, cfg)
    assert res.sub("5A").band_matched == "MISSING_DATA"
    assert res.sub("5B").band_matched == "MISSING_DATA"


def test_amber_company_still_scored(cfg):
    # Utilisation into AMBER band on Screen C but gate still passes.
    fin = clean_financials(short_term_borrowings=D("150"), long_term_borrowings=D("70"))
    gate = run_gate(fin, D("1000"), cfg)  # debt 220/1000=0.22; util 0.733 AMBER
    assert gate.overall_status is ShariahStatus.AMBER
    res = score_company(strong_inputs(fin), gate, cfg)
    assert res is not None
