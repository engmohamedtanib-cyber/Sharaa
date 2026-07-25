"""Cumulative-interim period derivation (EXTRACTION_SPEC §4)."""

from __future__ import annotations

import pytest

from conftest import D
from engine.types import PeriodType
from ingestion.periods import (
    PointInTimeError,
    Statement,
    derive_all_standalone,
    derive_standalone,
    is_cumulative,
    trailing_twelve_months,
)

CUM = {
    PeriodType.Q1: D("100"),
    PeriodType.H1: D("250"),
    PeriodType.NINE_M: D("400"),
    PeriodType.FY: D("600"),
}


def test_is_cumulative():
    assert is_cumulative(Statement.INCOME_STATEMENT) is True
    assert is_cumulative(Statement.CASH_FLOW) is True
    assert is_cumulative(Statement.BALANCE_SHEET) is False


@pytest.mark.parametrize(
    "quarter,expected",
    [("Q1", "100"), ("Q2", "150"), ("Q3", "150"), ("Q4", "200")],
)
def test_derive_standalone_quarters(quarter, expected):
    d = derive_standalone(quarter, CUM, Statement.INCOME_STATEMENT)
    assert d is not None
    assert d.value == D(expected)


def test_q1_is_not_derived():
    d = derive_standalone("Q1", CUM, Statement.INCOME_STATEMENT)
    assert d.is_derived is False
    assert "as reported" in d.derivation


def test_derived_quarters_carry_derivation_string():
    d = derive_standalone("Q2", CUM, Statement.INCOME_STATEMENT)
    assert d.is_derived is True
    assert d.derivation == "Q2 = H1 - Q1"


def test_missing_prior_returns_none_never_approximates():
    # H1 present but Q1 absent -> Q2 cannot be derived.
    partial = {PeriodType.H1: D("250")}
    assert derive_standalone("Q2", partial, Statement.INCOME_STATEMENT) is None


def test_missing_current_returns_none():
    partial = {PeriodType.Q1: D("100")}
    assert derive_standalone("Q2", partial, Statement.INCOME_STATEMENT) is None


def test_balance_sheet_never_subtracted():
    with pytest.raises(PointInTimeError):
        derive_standalone("Q2", CUM, Statement.BALANCE_SHEET)


def test_unknown_quarter_raises():
    with pytest.raises(KeyError):
        derive_standalone("Q5", CUM, Statement.INCOME_STATEMENT)


def test_derive_all_skips_underivable():
    partial = {PeriodType.Q1: D("100"), PeriodType.NINE_M: D("400")}
    out = derive_all_standalone(partial, Statement.INCOME_STATEMENT)
    assert set(out) == {"Q1"}          # Q2 needs H1; Q3 needs H1; Q4 needs FY
    assert out["Q1"].value == D("100")


def test_derive_all_full_year():
    out = derive_all_standalone(CUM, Statement.INCOME_STATEMENT)
    assert set(out) == {"Q1", "Q2", "Q3", "Q4"}
    assert sum(v.value for v in out.values()) == D("600")  # reconciles to FY


def test_negative_quarter_is_allowed():
    # A genuinely loss-making quarter must derive, not fail.
    cum = {PeriodType.Q1: D("100"), PeriodType.H1: D("80")}
    assert derive_standalone("Q2", cum, Statement.INCOME_STATEMENT).value == D("-20")


def test_ttm_requires_exactly_four_quarters():
    assert trailing_twelve_months([D("1"), D("2"), D("3")]) is None
    assert trailing_twelve_months([D("1"), D("2"), D("3"), D("4")]) == D("10")
    assert trailing_twelve_months([]) is None
