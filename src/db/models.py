"""Persistence records mirroring ``db/schema.sql``.

These dataclasses carry the same invariants the schema enforces so that the
in-memory backend behaves like Postgres for tests:
  * provenance columns on :class:`LineItem` are mandatory (R1)
  * ``page_no > 0`` and derived rows require a ``derivation`` string
  * decisions require substantive reason / falsification (§5.5)
Money is :class:`Decimal` throughout (never float, ``CLAUDE.md`` §5).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from engine.types import (
    BreachType,
    DataStatus,
    DecisionType,
    PeriodType,
    ShariahStatus,
    WatchlistName,
)


def new_id() -> str:
    return str(uuid.uuid4())


@dataclass(frozen=True)
class Company:
    egx_code: str
    name_en: str
    sector: str
    name_ar: str | None = None
    sub_sector: str | None = None
    isin: str | None = None
    listing_date: date | None = None
    is_active: bool = True
    ir_url: str | None = None
    filings_url: str | None = None
    fiscal_year_end: str = "12-31"
    id: str = field(default_factory=new_id)


@dataclass(frozen=True)
class Filing:
    company_id: str
    fiscal_year: int
    period: PeriodType
    period_end: date
    source_url: str
    file_sha256: str
    storage_path: str
    page_count: int
    is_scanned: bool
    language: str
    reported_scale: str
    currency: str = "EGP"
    audit_status: str = "AUDITED"
    data_status: DataStatus = DataStatus.PENDING
    status_reason: str | None = None
    id: str = field(default_factory=new_id)


@dataclass(frozen=True)
class LineItem:
    """The core provenance-bearing record (R1).

    ``filing_id``, ``page_no`` and ``extraction_run_id`` are mandatory. This
    mirrors the ``NOT NULL`` provenance columns — a figure without a source
    cannot be constructed.
    """

    filing_id: str
    statement: str
    item_key: str
    value_egp: Decimal
    raw_value: str
    raw_caption: str
    page_no: int
    extraction_run_id: str
    note_ref: str | None = None
    is_derived: bool = False
    derivation: str | None = None
    id: str = field(default_factory=new_id)

    def __post_init__(self) -> None:
        if self.page_no <= 0:
            raise ValueError("page_no must be > 0 (provenance constraint)")
        if self.is_derived and self.derivation is None:
            raise ValueError("derived line items require a derivation string")


@dataclass(frozen=True)
class ReconciledValue:
    filing_id: str
    item_key: str
    value_egp: Decimal
    agreement: bool
    page_no: int
    pass_1_value: Decimal | None = None
    pass_2_value: Decimal | None = None
    note_ref: str | None = None


@dataclass(frozen=True)
class ShariahScreen:
    company_id: str
    filing_id: str
    screen_code: str
    screen_name: str
    numerator: Decimal
    denominator: Decimal
    ratio: Decimal
    threshold: Decimal
    utilisation: Decimal
    status: ShariahStatus
    threshold_version: str
    standard: str = "AAOIFI"
    inputs_json: dict[str, object] = field(default_factory=dict)
    id: str = field(default_factory=new_id)

    def __post_init__(self) -> None:
        if self.denominator == 0:
            raise ValueError("denominator must be non-zero (schema CHECK)")


@dataclass(frozen=True)
class ShariahStatusHistory:
    company_id: str
    filing_id: str
    overall_status: ShariahStatus
    breach: BreachType = BreachType.NONE
    worst_screen_code: str | None = None
    worst_utilisation: Decimal | None = None
    consecutive_compliant_quarters: int = 0
    cure_window_opened_at: date | None = None
    cure_window_expires_at: date | None = None
    numerator_delta_pct: Decimal | None = None
    denominator_delta_pct: Decimal | None = None
    id: str = field(default_factory=new_id)


@dataclass(frozen=True)
class ScoreTotal:
    company_id: str
    filing_id: str
    p1_shariah: Decimal
    p2_financial: Decimal
    p3_earnings: Decimal
    p4_valuation: Decimal
    p5_growth: Decimal
    p6_technical: Decimal
    p7_governance: Decimal
    total: Decimal
    band: str
    rank_in_period: int | None = None

    def __post_init__(self) -> None:
        if not (0 <= self.total <= 100):
            raise ValueError("total must be within [0, 100] (schema CHECK)")


@dataclass(frozen=True)
class Decision:
    review_id: str
    company_id: str
    decision: DecisionType
    shariah_status: ShariahStatus
    trigger: str
    reason: str
    falsification_condition: str
    filing_id: str | None = None
    score: Decimal | None = None
    fair_value: Decimal | None = None
    price_at_decision: Decimal | None = None
    valuation_gap_pct: Decimal | None = None
    veto_fired: str | None = None
    confidence: str | None = None
    id: str = field(default_factory=new_id)

    def __post_init__(self) -> None:
        if len(self.reason.strip()) < 30:
            raise ValueError("reason must be >= 30 chars (schema CHECK)")
        if len(self.falsification_condition.strip()) < 20:
            raise ValueError("falsification_condition must be >= 20 chars (schema CHECK)")


@dataclass(frozen=True)
class WatchlistEntry:
    company_id: str
    review_id: str
    list_name: WatchlistName
    reason: str
    prev_list: WatchlistName | None = None
    trigger_price: Decimal | None = None
    quarters_on_list: int = 1
    id: str = field(default_factory=new_id)


@dataclass(frozen=True)
class PurificationRow:
    company_id: str
    filing_id: str
    impure_income: Decimal
    net_profit: Decimal
    purification_ratio: Decimal
    amount_due: Decimal
    dividends_received: Decimal = Decimal(0)
    settled_at: date | None = None
    settlement_ref: str | None = None
    id: str = field(default_factory=new_id)
