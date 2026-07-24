"""Screens, status classification, gate, breach typing (ENGINE_SPEC §2-3)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from conftest import D, clean_financials
from engine.shariah import (
    admission_test,
    classify_status,
    compliance_streak,
    diagnose_breach,
    interest_bearing_debt,
    is_core_activity_prohibited,
    run_gate,
    screen_a,
    screen_c,
)
from engine.types import BreachType, DataStatus, Financials, ShariahStatus


# ---- status band boundaries (§2.7) ----------------------------------
@pytest.mark.parametrize(
    "util,expected",
    [
        ("0.00", ShariahStatus.GREEN),
        ("0.65", ShariahStatus.GREEN),    # boundary -> GREEN (inclusive)
        ("0.650001", ShariahStatus.AMBER),
        ("0.85", ShariahStatus.AMBER),    # boundary -> AMBER
        ("0.850001", ShariahStatus.ORANGE),
        ("1.00", ShariahStatus.ORANGE),   # boundary -> ORANGE
        ("1.000001", ShariahStatus.RED),
    ],
)
def test_status_band_boundaries(cfg, util, expected):
    assert classify_status(D(util), cfg) is expected


def test_status_none_is_data_insufficient(cfg):
    assert classify_status(None, cfg) is ShariahStatus.DATA_INSUFFICIENT


# ---- Screen C excludes Islamic financing (§2.3, EXTRACTION_SPEC §3.2) --
def test_screen_c_excludes_islamic_financing():
    fin = Financials(
        short_term_borrowings=D("100"),
        long_term_borrowings=D("50"),
        islamic_financing=D("40"),
    )
    # 150 conventional - 40 islamic = 110
    assert interest_bearing_debt(fin) == D("110")


def test_screen_c_ratio(cfg):
    fin = clean_financials()
    r = screen_c(fin, D("1000"), cfg)
    # (40 + 60) / 1000 = 0.10 ; util = 0.10/0.30 = 0.333 -> GREEN
    assert r.numerator == D("100")
    assert r.ratio == D("0.1")
    assert r.status is ShariahStatus.GREEN
    assert r.ratio_total_assets == D("0.1")  # 100 / total_assets 1000


# ---- core-activity override (§2.1) ----------------------------------
def test_core_activity_prohibited_matches_substring():
    assert is_core_activity_prohibited("Conventional Banking", None, ["conventional bank"])
    assert not is_core_activity_prohibited("Real Estate", None, ["conventional bank"])


def test_screen_a_core_prohibited_forces_red(cfg):
    fin = clean_financials(non_permissible_revenue=D("0"))
    r = screen_a(fin, cfg, core_prohibited=True)
    assert r.status is ShariahStatus.RED  # regardless of a zero ratio


def test_screen_a_incidental_within_tolerance(cfg):
    fin = clean_financials(non_permissible_revenue=D("10"))  # 10/500 = 0.02
    r = screen_a(fin, cfg, core_prohibited=False)
    assert r.ratio == D("0.02")
    assert r.status is ShariahStatus.GREEN  # util 0.02/0.05 = 0.4


# ---- gate worst-of (§2.7) -------------------------------------------
def test_gate_takes_worst_status(cfg):
    # Push Screen E to RED via a huge receivable; others stay GREEN.
    fin = clean_financials(accounts_receivable=D("600"))  # 600/1000=0.6; util 0.6/0.49=1.22 RED
    gate = run_gate(fin, D("1000"), cfg)
    assert gate.overall_status is ShariahStatus.RED
    assert gate.worst_screen_code == "E"
    assert gate.passed is False


def test_gate_passes_when_all_green(cfg):
    gate = run_gate(clean_financials(), D("2000"), cfg)
    assert gate.overall_status is ShariahStatus.GREEN
    assert gate.passed is True


def test_gate_missing_denominator_is_data_insufficient(cfg):
    gate = run_gate(clean_financials(), None, cfg)
    assert gate.overall_status is ShariahStatus.DATA_INSUFFICIENT
    assert gate.passed is False


# ---- breach typing (§3) ---------------------------------------------
def test_breach_type1_on_activity(cfg):
    fin = clean_financials()
    gate = run_gate(fin, D("2000"), cfg, core_prohibited=True)
    b = diagnose_breach(gate, DataStatus.VALIDATED, 30, cfg)
    assert b.breach is BreachType.TYPE_1_ACTIVITY


def test_breach_type4_on_stale(cfg):
    gate = run_gate(clean_financials(), D("2000"), cfg)
    b = diagnose_breach(gate, DataStatus.VALIDATED, filing_age_days=200, cfg=cfg)
    assert b.breach is BreachType.TYPE_4_DATA


def test_breach_type4_on_unvalidated(cfg):
    gate = run_gate(clean_financials(), D("2000"), cfg)
    b = diagnose_breach(gate, DataStatus.INSUFFICIENT, 10, cfg)
    assert b.breach is BreachType.TYPE_4_DATA


def test_breach_none_when_compliant(cfg):
    gate = run_gate(clean_financials(), D("2000"), cfg)
    b = diagnose_breach(gate, DataStatus.VALIDATED, 30, cfg)
    assert b.breach is BreachType.NONE


def test_breach_type2_vs_type3_by_driver(cfg):
    # Force Screen C to RED, then supply a prior screen to diagnose the driver.
    fin = clean_financials(short_term_borrowings=D("500"), long_term_borrowings=D("200"))
    gate = run_gate(fin, D("1000"), cfg)  # debt 700/1000 = 0.7 util 2.33 RED
    prior_num_driven = {"C": _prior_screen(D("100"), D("1000"))}   # num jumped 100->700
    b2 = diagnose_breach(gate, DataStatus.VALIDATED, 30, cfg, prior_screens=prior_num_driven)
    assert b2.breach is BreachType.TYPE_2_STRUCTURAL

    prior_den_driven = {"C": _prior_screen(D("700"), D("3000"))}  # denom collapsed 3000->1000
    b3 = diagnose_breach(gate, DataStatus.VALIDATED, 30, cfg, prior_screens=prior_den_driven)
    assert b3.breach is BreachType.TYPE_3_DENOMINATOR


def test_breach_defaults_structural_without_prior(cfg):
    fin = clean_financials(short_term_borrowings=D("500"), long_term_borrowings=D("200"))
    gate = run_gate(fin, D("1000"), cfg)
    b = diagnose_breach(gate, DataStatus.VALIDATED, 30, cfg)
    assert b.breach is BreachType.TYPE_2_STRUCTURAL


def _prior_screen(numerator: Decimal, denominator: Decimal):
    from engine.shariah import ScreenResult

    return ScreenResult(
        code="C", name="Debt", numerator=numerator, denominator=denominator,
        ratio=numerator / denominator, threshold=D("0.30"),
        utilisation=(numerator / denominator) / D("0.30"), status=ShariahStatus.GREEN,
    )


# ---- admission test (§3.4) ------------------------------------------
def test_admission_accepts_numerator_driven():
    assert admission_test(ShariahStatus.RED, ShariahStatus.GREEN, D("-0.30"), D("-0.05"), None) is True


def test_admission_rejects_price_rally_only():
    # denominator-driven (mcap up) but total assets did not grow -> price rally
    assert admission_test(ShariahStatus.RED, ShariahStatus.AMBER, D("-0.02"), D("0.40"), D("-0.05")) is False


def test_admission_accepts_denominator_via_asset_growth():
    assert admission_test(ShariahStatus.RED, ShariahStatus.AMBER, D("-0.02"), D("0.40"), D("0.20")) is True


def test_admission_non_transition_is_true():
    assert admission_test(ShariahStatus.GREEN, ShariahStatus.GREEN, None, None, None) is True


def test_admission_missing_deltas_rejected():
    assert admission_test(ShariahStatus.RED, ShariahStatus.GREEN, None, None, None) is False


# ---- streak (§3.5) --------------------------------------------------
def test_compliance_streak_counts_leading_compliant():
    seq = [ShariahStatus.GREEN, ShariahStatus.AMBER, ShariahStatus.RED, ShariahStatus.GREEN]
    assert compliance_streak(seq) == 2


def test_compliance_streak_zero_when_latest_breached():
    assert compliance_streak([ShariahStatus.RED, ShariahStatus.GREEN]) == 0
