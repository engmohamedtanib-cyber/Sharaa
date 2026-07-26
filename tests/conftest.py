"""Shared fixtures and builders for the test suite."""

from __future__ import annotations

from decimal import Decimal

import pytest

from config_loader import load_thresholds
from engine.config import Thresholds
from engine.types import (
    AuditOpinion,
    CapitalAllocation,
    Financials,
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


def D(x: str) -> Decimal:
    return Decimal(x)


@pytest.fixture(scope="session")
def cfg() -> Thresholds:
    return load_thresholds()


def clean_financials(**overrides: object) -> Financials:
    """A balanced, internally-consistent filing (passes V1/V2/V3)."""
    base = dict(
        total_assets=D("1000"),
        total_liabilities=D("600"),
        total_equity=D("400"),
        total_revenue=D("500"),
        cost_of_sales=D("300"),
        gross_profit=D("200"),
        operating_profit=D("140"),
        profit_before_tax=D("120"),
        income_tax=D("24"),
        net_profit_attributable=D("96"),
        cash_and_equivalents=D("60"),
        time_deposits=D("50"),
        accounts_receivable=D("90"),
        short_term_borrowings=D("40"),
        long_term_borrowings=D("60"),
        interest_income=D("5"),
        operating_cash_flow=D("150"),
        capex=D("15"),
        total_current_assets=D("320"),
        total_current_liabilities=D("140"),
        inventory=D("70"),
        closing_cash=D("60"),
    )
    base.update(overrides)
    return Financials(**base)  # type: ignore[arg-type]


def strong_inputs(fin: Financials | None = None) -> ScoringInputs:
    """A maximal-quality company for full-scoring tests."""
    fin = fin or clean_financials()
    history = History(
        worst_util_last4=(D("0.30"), D("0.27"), D("0.25"), D("0.24")),
        consecutive_compliant_quarters=8,
        prior_total_assets=D("950"),
        receivables_growth=D("0.05"),
        revenue_growth=D("0.10"),
        operating_margins_8q=tuple(D(m) for m in ("0.27", "0.28", "0.27", "0.28", "0.27", "0.28", "0.27", "0.28")),
        ev_ebitda_median_5y=D("7"),
        nominal_revenue_cagr_3y=D("0.25"),
        nominal_eps_cagr_3y=D("0.25"),
        physical_volume_growth=D("0.12"),
        ocf_ttm=D("150"),
        net_income_ttm=D("96"),
    )
    return ScoringInputs(
        financials=fin,
        market=MarketData(
            market_cap=D("2400"), close_price=D("12"), adtv_60d=D("200000"),
            ma_50=D("11"), ma_200=D("10"), ma_200_rising=True, rsi_14=D("55"),
            rel_strength_6m_vs_egx30=D("0.08"),
        ),
        macro=MacroData(tbill_1y_yield=D("0.19"), cpi_yoy=D("0.12")),
        history=history,
        valuation=Valuation(
            pe_ratio=D("5"), ev_ebitda=D("4"), pb_ratio=D("1.0"), roe=D("0.25"),
            dividend_yield=D("0.09"), payout_of_fcf=D("0.5"), dividend_sustainable=True,
            sector_median_pe=D("7"),
        ),
        governance=Governance(
            independent_fraction=D("0.40"), chair_is_ceo=False, free_float=D("0.35"),
            clean_minority_record=True, timeliness=Timeliness.ON_TIME_FULL,
            related_party=RelatedPartyQuality.IMMATERIAL_DISCLOSED,
            capital_allocation=CapitalAllocation.CONSISTENT,
        ),
        qualitative=Qualitative(
            fx_resilience=FxResilience.HEDGED, audit_opinion=AuditOpinion.CLEAN_IMMATERIAL_RPT,
            roic=RoicCategory.EXCEEDS_STABLE,
        ),
        target_position_value=D("1000"),
    )


# ----------------------------------------------------------------------
# Suite size, for the cross-artefact consistency check
# ----------------------------------------------------------------------
_COLLECTION: dict[str, object] = {}


def pytest_collection_modifyitems(session, config, items):
    """Record the suite size and whether this was a whole-suite run.

    A targeted run (``pytest tests/test_one.py``) collects a subset, and
    comparing a documented total against a subset would fail for the wrong
    reason — so the count is only offered when the full suite was collected.
    """
    _COLLECTION["count"] = len(items)
    _COLLECTION["full"] = not config.option.file_or_dir


@pytest.fixture(scope="session")
def collected_test_count() -> int | None:
    """Size of the suite, or ``None`` when only part of it was collected."""
    return _COLLECTION["count"] if _COLLECTION.get("full") else None  # type: ignore[return-value]
