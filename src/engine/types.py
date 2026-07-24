"""Domain types for the deterministic layers.

Enums mirror the database ENUMs in ``db/schema.sql`` exactly. Input bundles
are the reconciled, validated values the engine consumes — the engine never
sees a raw extraction, only figures that have passed validation.

Pure module: definitions only, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from common.decimals import ZERO


# ======================================================================
# ENUMS (mirror db/schema.sql)
# ======================================================================
class PeriodType(StrEnum):
    Q1 = "Q1"
    H1 = "H1"
    NINE_M = "9M"
    FY = "FY"


class DataStatus(StrEnum):
    PENDING = "PENDING"
    VALIDATED = "VALIDATED"
    INSUFFICIENT = "INSUFFICIENT"
    CONFLICT = "CONFLICT"


class ShariahStatus(StrEnum):
    GREEN = "GREEN"
    AMBER = "AMBER"
    ORANGE = "ORANGE"
    RED = "RED"
    DATA_INSUFFICIENT = "DATA_INSUFFICIENT"


class BreachType(StrEnum):
    NONE = "NONE"
    TYPE_1_ACTIVITY = "TYPE_1_ACTIVITY"
    TYPE_2_STRUCTURAL = "TYPE_2_STRUCTURAL"
    TYPE_3_DENOMINATOR = "TYPE_3_DENOMINATOR"
    TYPE_4_DATA = "TYPE_4_DATA"


class DecisionType(StrEnum):
    BUY = "BUY"
    ADD = "ADD"
    HOLD = "HOLD"
    HOLD_FROZEN = "HOLD_FROZEN"
    REDUCE = "REDUCE"
    SELL = "SELL"
    REMOVE = "REMOVE"
    NO_ACTION = "NO_ACTION"


class WatchlistName(StrEnum):
    HIGH_CONVICTION = "HIGH_CONVICTION"
    BUY_ON_PULLBACK = "BUY_ON_PULLBACK"
    HOLD = "HOLD"
    REMOVE = "REMOVE"


# Status severity for worst() (GREEN best ... RED/DATA_INSUFFICIENT worst).
_SEVERITY: dict[ShariahStatus, int] = {
    ShariahStatus.GREEN: 0,
    ShariahStatus.AMBER: 1,
    ShariahStatus.ORANGE: 2,
    ShariahStatus.RED: 3,
    ShariahStatus.DATA_INSUFFICIENT: 4,
}


def status_severity(s: ShariahStatus) -> int:
    return _SEVERITY[s]


def worst_status(statuses: list[ShariahStatus]) -> ShariahStatus:
    """Worst (most severe) status. Averaging is forbidden (ENGINE_SPEC §2.7)."""
    if not statuses:
        raise ValueError("worst_status requires at least one status")
    return max(statuses, key=status_severity)


# Categorical inputs for qualitative sub-criteria. These arrive already
# classified from structured/extracted data — the engine only maps
# category -> points. No judgement happens in the engine.
class FxResilience(StrEnum):
    HEDGED = "hedged"                    # net FX asset or natural hedge -> 3
    BALANCED = "balanced"                # -> 2
    SMALL_LIABILITY = "small_liability"  # net FX liability < 20% equity -> 1
    EXPOSED = "exposed"                  # -> 0
    UNKNOWN = "unknown"                  # note absent -> 0 + MISSING_DATA


class AuditOpinion(StrEnum):
    CLEAN_IMMATERIAL_RPT = "clean_immaterial_rpt"  # -> 2
    CLEAN_MATERIAL_RPT = "clean_material_rpt"      # -> 1
    EMPHASIS = "emphasis"                          # emphasis of matter -> 0.5
    ADVERSE = "adverse"                            # qualified/adverse/disclaimer -> 0 + VETO


class RoicCategory(StrEnum):
    EXCEEDS_STABLE = "exceeds_stable"  # exceeds cost of capital and stable/rising -> 1
    MARGINAL = "marginal"              # -> 0.5
    BELOW = "below"                    # -> 0
    UNKNOWN = "unknown"


class Timeliness(StrEnum):
    ON_TIME_FULL = "on_time_full"
    ON_TIME_THIN = "on_time_thin"
    OCCASIONAL_DELAY = "occasional_delay"
    REPEATED = "repeated"


class RelatedPartyQuality(StrEnum):
    IMMATERIAL_DISCLOSED = "immaterial_disclosed"
    MATERIAL_ARMS_LENGTH = "material_arms_length"
    MATERIAL_OPAQUE = "material_opaque"


class CapitalAllocation(StrEnum):
    CONSISTENT = "consistent"
    ELSE = "else"


# ======================================================================
# FINANCIAL INPUTS (reconciled, validated)
# ======================================================================
@dataclass(frozen=True)
class Financials:
    """Reconciled line items for a single filing, normalised to EGP units.

    Every field is optional; ``None`` means "not present in this filing".
    Additive components (debt/asset lines) are treated as zero when summed —
    a company that never issued bonds legitimately has no bond line. Truly
    *required* figures (denominators, total_revenue, total_assets) are checked
    by each screen; their absence yields a DATA_INSUFFICIENT result rather
    than a silent zero.
    """

    # --- Balance sheet ---
    total_assets: Decimal | None = None
    total_liabilities: Decimal | None = None
    total_equity: Decimal | None = None
    cash_and_equivalents: Decimal | None = None
    time_deposits: Decimal | None = None
    treasury_bills_and_bonds: Decimal | None = None
    conventional_money_market: Decimal | None = None
    accounts_receivable: Decimal | None = None
    inventory: Decimal | None = None
    total_current_assets: Decimal | None = None
    total_current_liabilities: Decimal | None = None
    short_term_borrowings: Decimal | None = None
    long_term_borrowings: Decimal | None = None
    bank_overdraft: Decimal | None = None
    bonds_payable: Decimal | None = None
    lease_liabilities_finance: Decimal | None = None
    islamic_financing: Decimal | None = None

    # --- Income statement ---
    total_revenue: Decimal | None = None
    cost_of_sales: Decimal | None = None
    gross_profit: Decimal | None = None
    operating_profit: Decimal | None = None
    depreciation: Decimal | None = None
    amortisation: Decimal | None = None
    finance_cost: Decimal | None = None
    interest_income: Decimal | None = None
    income_from_conventional_investments: Decimal | None = None
    other_non_permissible_income: Decimal | None = None
    fx_gain_loss: Decimal | None = None
    other_income: Decimal | None = None
    share_of_associates: Decimal | None = None
    profit_before_tax: Decimal | None = None
    income_tax: Decimal | None = None
    net_profit_attributable: Decimal | None = None
    eps_basic: Decimal | None = None

    # --- Cash flow ---
    operating_cash_flow: Decimal | None = None
    capex: Decimal | None = None
    dividends_paid: Decimal | None = None
    closing_cash: Decimal | None = None

    # --- Segment (Screen A) ---
    non_permissible_revenue: Decimal | None = None

    def get0(self, name: str) -> Decimal:
        """Additive-component accessor: value, or zero when absent."""
        v = getattr(self, name)
        return ZERO if v is None else v


@dataclass(frozen=True)
class MarketData:
    """Market/technical inputs for one company as of a date."""

    close_price: Decimal | None = None
    shares_out: Decimal | None = None
    market_cap: Decimal | None = None
    mcap_avg_12m: Decimal | None = None   # screening denominator (§2.6)
    adtv_60d: Decimal | None = None
    ma_50: Decimal | None = None
    ma_200: Decimal | None = None
    ma_200_rising: bool | None = None
    rsi_14: Decimal | None = None
    rel_strength_6m_vs_egx30: Decimal | None = None  # e.g. +0.08 = +8%


@dataclass(frozen=True)
class MacroData:
    tbill_1y_yield: Decimal | None = None   # for ERP (Pillar 4)
    cpi_yoy: Decimal | None = None          # deflates Pillar 5


@dataclass(frozen=True)
class Governance:
    """Structured governance facts (pre-classified, no engine judgement)."""

    independent_fraction: Decimal | None = None
    chair_is_ceo: bool | None = None
    free_float: Decimal | None = None
    clean_minority_record: bool | None = None
    timeliness: Timeliness | None = None
    related_party: RelatedPartyQuality | None = None
    capital_allocation: CapitalAllocation | None = None


@dataclass(frozen=True)
class History:
    """Prior-period series needed by streak / CAGR / volatility criteria.

    Lists are ordered oldest -> newest. Absence yields MISSING_DATA on the
    dependent sub-criterion, never an estimate.
    """

    worst_util_last4: tuple[Decimal, ...] = ()      # 1E slope
    consecutive_compliant_quarters: int = 0          # 1E / §3.5
    prior_total_assets: Decimal | None = None        # 3A avg assets
    receivables_growth: Decimal | None = None        # 3B (needs >=5q upstream)
    revenue_growth: Decimal | None = None            # 3B
    operating_margins_8q: tuple[Decimal, ...] = ()   # 3D
    ev_ebitda_median_5y: Decimal | None = None       # 4B
    sector_median_ev_ebitda: Decimal | None = None   # 4B fallback
    nominal_revenue_cagr_3y: Decimal | None = None   # 5A
    nominal_eps_cagr_3y: Decimal | None = None       # 5B
    physical_volume_growth: Decimal | None = None    # 5C
    ocf_ttm: Decimal | None = None                   # 2D
    net_income_ttm: Decimal | None = None            # 2D


@dataclass(frozen=True)
class Valuation:
    """Valuation-market inputs for Pillar 4 and the decision engine."""

    pe_ratio: Decimal | None = None
    ev_ebitda: Decimal | None = None
    pb_ratio: Decimal | None = None
    roe: Decimal | None = None
    dividend_yield: Decimal | None = None
    payout_of_fcf: Decimal | None = None
    dividend_sustainable: bool | None = None
    sector_median_pe: Decimal | None = None


@dataclass(frozen=True)
class Qualitative:
    """Categorical inputs for sub-criteria that are not numerically derivable."""

    fx_resilience: FxResilience | None = None
    audit_opinion: AuditOpinion | None = None
    roic: RoicCategory | None = None


@dataclass(frozen=True)
class ScoringInputs:
    """Everything the 100-point model needs for one (company, filing)."""

    financials: Financials
    market: MarketData = field(default_factory=MarketData)
    macro: MacroData = field(default_factory=MacroData)
    governance: Governance = field(default_factory=Governance)
    history: History = field(default_factory=History)
    valuation: Valuation = field(default_factory=Valuation)
    qualitative: Qualitative = field(default_factory=Qualitative)
    target_position_value: Decimal | None = None  # for 6B liquidity
