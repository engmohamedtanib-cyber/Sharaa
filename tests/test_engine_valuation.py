"""Fair value and ERP (ENGINE_SPEC §4.4, §5.4)."""

from __future__ import annotations

from conftest import D
from engine.valuation import (
    equity_risk_premium,
    fair_value,
    justified_pe_fair_value,
    valuation_gap,
)


def test_erp_positive():
    # P/E 5 -> earnings yield 0.20 ; minus 0.19 T-bill = 0.01
    assert equity_risk_premium(D("5"), D("0.19")) == D("0.01")


def test_erp_none_when_loss_making():
    assert equity_risk_premium(D("-3"), D("0.19")) is None


def test_justified_pe_undefined_when_r_le_g():
    assert justified_pe_fair_value(D("0.5"), D("0.10"), D("0.10"), D("2")) is None
    assert justified_pe_fair_value(D("0.5"), D("0.15"), D("0.10"), D("2")) is None


def test_fair_value_takes_lower_of_methods():
    # justified: 0.5*(1.03)/(0.12-0.03)=5.722*eps2=11.44 ; peer: 6*2=12 -> lower 11.44
    fv = fair_value(payout_ratio=D("0.5"), g_real=D("0.03"), r_real=D("0.12"), eps_ttm=D("2"), sector_median_pe=D("6"))
    assert fv is not None and fv < D("12")


def test_fair_value_none_when_no_method():
    assert fair_value(eps_ttm=D("2")) is None


def test_valuation_gap_positive_is_discount():
    assert valuation_gap(D("100"), D("75")) == D("0.25")


def test_valuation_gap_none_when_no_fair_value():
    assert valuation_gap(None, D("75")) is None
