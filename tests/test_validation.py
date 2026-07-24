"""Validators V1-V9, status derivation, and the type-level gate (§6)."""

from __future__ import annotations

from conftest import D, clean_financials
from engine.types import DataStatus, Financials
from validation.agreement import v9_dual_extraction_agreement
from validation.arithmetic import (
    v1_balance_sheet_identity,
    v2_income_statement_chain,
    v3_cash_flow_tie_out,
    v4_borrowings_component_sum,
)
from validation.continuity import v5_comparative_continuity, v6_cumulative_monotonicity
from validation.plausibility import v7_magnitude_plausibility, v8_segment_reconciliation
from validation.runner import ValidationInputs, certify, validate


# ---- V1 balance sheet identity --------------------------------------
def test_v1_passes_on_balanced():
    assert v1_balance_sheet_identity(clean_financials()).passed is True


def test_v1_fails_on_5pct_error():
    fin = clean_financials(total_equity=D("440"))  # 1000 vs 600+440=1040
    r = v1_balance_sheet_identity(fin)
    assert r.passed is False and r.is_critical


def test_v1_not_applicable_when_missing():
    r = v1_balance_sheet_identity(Financials(total_assets=D("1000")))
    assert r.applicable is False


# ---- V2 income chain -------------------------------------------------
def test_v2_passes_on_consistent_chain():
    assert v2_income_statement_chain(clean_financials()).passed is True


def test_v2_fails_on_gross_profit_break():
    fin = clean_financials(gross_profit=D("150"))  # 500-300=200 != 150
    assert v2_income_statement_chain(fin).passed is False


# ---- V3 cash tie-out -------------------------------------------------
def test_v3_fails_on_cash_mismatch():
    fin = clean_financials(closing_cash=D("80"))  # BS cash 60
    assert v3_cash_flow_tie_out(fin).passed is False


# ---- V4 borrowings sum -----------------------------------------------
def test_v4_reconciles_components():
    fin = clean_financials()  # st 40 + lt 60 = 100
    assert v4_borrowings_component_sum(fin, D("100")).passed is True
    assert v4_borrowings_component_sum(fin, D("130")).passed is False


def test_v4_not_applicable_without_disclosed_total():
    assert v4_borrowings_component_sum(clean_financials(), None).applicable is False


# ---- V5 comparative continuity --------------------------------------
def test_v5_flags_restatement():
    r = v5_comparative_continuity({"total_assets": D("1000")}, {"total_assets": D("1100")})
    assert r.passed is False and r.warning is True


def test_v5_passes_within_tolerance():
    r = v5_comparative_continuity({"total_assets": D("1000")}, {"total_assets": D("1000.5")})
    assert r.passed is True


# ---- V6 cumulative monotonicity (non-critical, warns) ---------------
def test_v6_flags_decrease_but_does_not_fail():
    r = v6_cumulative_monotonicity([D("100"), D("90")])
    assert r.passed is True and r.warning is True and r.is_critical is False


# ---- V7 magnitude / scale error -------------------------------------
def test_v7_detects_scale_error():
    fin = clean_financials(total_revenue=D("0.5"))
    r = v7_magnitude_plausibility(fin, market_cap=D("2000000"), close_price=None, shares_out=None)
    assert r.passed is False


def test_v7_shares_consistency():
    fin = clean_financials()
    # market_cap/price = 100/... implied shares; give inconsistent shares_out
    r = v7_magnitude_plausibility(fin, market_cap=D("1200"), close_price=D("12"), shares_out=D("50"))
    # implied = 100 shares; 50 is beyond 5% -> fail
    assert r.passed is False


# ---- V8 segment reconciliation --------------------------------------
def test_v8_reconciles_segments():
    assert v8_segment_reconciliation([D("300"), D("200")], D("500")).passed is True
    assert v8_segment_reconciliation([D("300"), D("100")], D("500")).passed is False


# ---- V9 dual-extraction agreement -----------------------------------
def test_v9_agrees():
    p = {"total_assets": D("1000")}
    assert v9_dual_extraction_agreement(p, dict(p), ["total_assets"]).passed is True


def test_v9_conflicts():
    r = v9_dual_extraction_agreement({"total_assets": D("1000")}, {"total_assets": D("1100")}, ["total_assets"])
    assert r.passed is False


def test_v9_one_sided_is_conflict():
    r = v9_dual_extraction_agreement({"total_assets": D("1000")}, {}, ["total_assets"])
    assert r.passed is False


# ---- runner status derivation ---------------------------------------
def _clean_inputs():
    fin = clean_financials()
    p = {"total_assets": D("1000"), "total_revenue": D("500")}
    return ValidationInputs(financials=fin, market_cap=D("2000"), extraction_pass1=p, extraction_pass2=dict(p))


def test_runner_validated(cfg):
    rep = validate(_clean_inputs(), cfg)
    assert rep.data_status is DataStatus.VALIDATED


def test_runner_insufficient_on_critical_fail(cfg):
    inp = _clean_inputs()
    inp = ValidationInputs(**{**inp.__dict__, "financials": clean_financials(total_equity=D("500"))})
    assert validate(inp, cfg).data_status is DataStatus.INSUFFICIENT


def test_runner_conflict_on_v9(cfg):
    inp = _clean_inputs()
    inp = ValidationInputs(**{**inp.__dict__, "extraction_pass2": {"total_assets": D("2000")}})
    assert validate(inp, cfg).data_status is DataStatus.CONFLICT


def test_runner_stale_is_insufficient(cfg):
    inp = _clean_inputs()
    inp = ValidationInputs(**{**inp.__dict__, "filing_age_days": 200})
    assert validate(inp, cfg).data_status is DataStatus.INSUFFICIENT


# ---- type-level gate -------------------------------------------------
def test_certify_only_for_validated(cfg):
    fin = clean_financials()
    good = validate(_clean_inputs(), cfg)
    assert certify(good, fin) is not None

    bad = ValidationInputs(**{**_clean_inputs().__dict__, "financials": clean_financials(total_equity=D("500"))})
    assert certify(validate(bad, cfg), fin) is None


# ---- property: screening status is always one of four (never null) --
def test_screening_status_is_one_of_four(cfg):
    from itertools import product

    from engine.shariah import run_gate
    from engine.types import ShariahStatus

    allowed = {
        ShariahStatus.GREEN, ShariahStatus.AMBER, ShariahStatus.ORANGE,
        ShariahStatus.RED, ShariahStatus.DATA_INSUFFICIENT,
    }
    for ar, mcap in product(["0", "50", "600"], [None, "500", "5000"]):
        fin = clean_financials(accounts_receivable=D(ar))
        gate = run_gate(fin, D(mcap) if mcap else None, cfg)
        assert gate.overall_status in allowed
