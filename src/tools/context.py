"""Everything a tool call needs, assembled once at the boundary.

Engine functions are pure and receive their config as arguments (``CLAUDE.md``
§5). Tools are the layer where that config is *fetched* — so the fetching all
happens here, once, and a tool receives a fully-built context rather than
reaching for a file or a clock mid-call.

``now`` is a field, not a call. A routine captures the time once at wake-up and
passes the same value into every tool it invokes, so a run that straddles
midnight cannot record two different dates for one logical action.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from config_loader import load_policy, load_thresholds, load_universe
from engine.config import Thresholds
from engine.policy import InvestmentPolicy
from engine.universe import Universe
from store.jsonl_decisions import JsonlDecisionStore
from store.jsonl_ledger import JsonlLedgerStore
from store.jsonl_orders import JsonlOrderStore
from tools.audit import AuditLog
from tools.data import CompanyDataProvider, NoDataProvider

#: Repo root = parent of src/.
_ROOT = Path(__file__).resolve().parent.parent.parent

DEFAULT_LEDGER_PATH = _ROOT / "memory" / "portfolio" / "ledger.jsonl"
DEFAULT_ORDERS_PATH = _ROOT / "memory" / "portfolio" / "orders.jsonl"
DEFAULT_DECISIONS_PATH = _ROOT / "memory" / "decisions" / "journal.jsonl"
DEFAULT_AUDIT_PATH = _ROOT / "memory" / "audit" / "tool_calls.jsonl"


@dataclass(frozen=True)
class ToolContext:
    """Ambient state for one conversation turn or one routine run."""

    now: str                                   # ISO-8601 UTC, captured once by the caller
    ledger: JsonlLedgerStore
    orders: JsonlOrderStore
    decisions: JsonlDecisionStore
    audit: AuditLog
    cfg: Thresholds
    policy: InvestmentPolicy
    universe: Universe
    #: Prices are supplied, never fetched here. An empty mapping means "no market
    #: data available", which tools must surface rather than paper over.
    prices: dict[str, Decimal] = field(default_factory=dict)
    #: Validated company data. The default has none, on purpose.
    data: CompanyDataProvider = field(default_factory=NoDataProvider)

    @property
    def today(self) -> str:
        """The date part of ``now``, for event ``occurred_at`` values."""
        return self.now[:10]


def build_context(
    now: str,
    *,
    ledger_path: str | Path | None = None,
    orders_path: str | Path | None = None,
    decisions_path: str | Path | None = None,
    audit_path: str | Path | None = None,
    prices: dict[str, Decimal] | None = None,
    data: CompanyDataProvider | None = None,
) -> ToolContext:
    """Assemble a context from the repository's config and state files.

    ``now`` is mandatory and has no default: a default would be a clock read,
    and a clock read here would make every downstream result depend on when it
    was computed rather than on what it was computed from.
    """
    if not now.strip():
        raise ValueError("build_context requires an explicit `now` (ISO-8601 UTC)")
    return ToolContext(
        now=now,
        ledger=JsonlLedgerStore(ledger_path or DEFAULT_LEDGER_PATH),
        orders=JsonlOrderStore(orders_path or DEFAULT_ORDERS_PATH),
        decisions=JsonlDecisionStore(decisions_path or DEFAULT_DECISIONS_PATH),
        audit=AuditLog(audit_path or DEFAULT_AUDIT_PATH),
        cfg=load_thresholds(),
        policy=load_policy(),
        universe=load_universe(),
        prices=dict(prices or {}),
        data=data or NoDataProvider(),
    )
