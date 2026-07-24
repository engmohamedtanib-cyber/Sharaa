"""Additional validator branch coverage (applicability + secondary legs)."""

from __future__ import annotations

from conftest import D, clean_financials
from engine.types import Financials
from validation.arithmetic import (
    v2_income_statement_chain,
    v3_cash_flow_tie_out,
)
from validation.plausibility import v7_magnitude_plausibility


def test_v2_not_applicable_without_components():
    r = v2_income_statement_chain(Financials(total_revenue=D("500")))
    assert r.applicable is False


def test_v2_pbt_leg_fails_on_large_gap():
    # gross-profit identity holds, but PBT-tax vs net_profit gap > 10% -> fail
    fin = clean_financials(profit_before_tax=D("120"), income_tax=D("24"), net_profit_attributable=D("50"))
    assert v2_income_statement_chain(fin).passed is False


def test_v2_pbt_leg_passes_small_minority():
    # small minority interest gap (< 10%) does not fail V2
    fin = clean_financials(profit_before_tax=D("120"), income_tax=D("24"), net_profit_attributable=D("94"))
    assert v2_income_statement_chain(fin).passed is True


def test_v3_not_applicable_without_closing_cash():
    r = v3_cash_flow_tie_out(clean_financials(closing_cash=None))
    assert r.applicable is False


def test_v7_total_assets_not_positive_fails():
    fin = clean_financials(total_assets=D("0"))
    r = v7_magnitude_plausibility(fin, market_cap=D("2000"), close_price=None, shares_out=None)
    assert r.passed is False
