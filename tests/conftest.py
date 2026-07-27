"""Shared fixtures and builders for the test suite."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from tools.data import CompanyFacts

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


@pytest.fixture()
def clean_company() -> CompanyFacts:
    """A gate-passing company with validated figures, for tool-layer tests.

    Deliberately a *fixture*, never a fallback: the tool layer's default provider
    has no data at all, and tests that assert refusals rely on that.
    """
    from tools.data import CompanyFacts  # local import: tests/ only, keeps engine tests dependency-free

    inputs = strong_inputs()
    market = replace(inputs.market, mcap_avg_12m=D("2400"))
    return CompanyFacts(
        ticker="AAAA",
        sector="Materials",
        inputs=replace(inputs, market=market),
        core_prohibited=False,
        filing_age_days=30,
        period_label="FY2025",
        source_note="fixture, not a real filing",
    )


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
