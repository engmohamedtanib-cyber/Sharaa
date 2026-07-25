"""Repository layer: a typed Protocol and an in-memory backend.

The in-memory backend lets the whole pipeline run on fixtures with no database
(``EGX_STORE_BACKEND=memory``). It reproduces the two invariants that matter
for correctness:
  * append-only history — screens / scores / decisions / line items are
    insert-only (there is simply no update/delete method)
  * a filing that is not ``VALIDATED`` can never be screened or scored
    (``guard_validated_only`` in ``db/schema.sql``)
"""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

from db.models import (
    Company,
    Decision,
    Filing,
    LineItem,
    PurificationRow,
    ReconciledValue,
    ScoreTotal,
    ShariahScreen,
    ShariahStatusHistory,
    WatchlistEntry,
)
from engine.ledger import LedgerEvent, PortfolioState, fold
from engine.types import DataStatus


class NotValidatedError(RuntimeError):
    """Raised when screening/scoring is attempted on a non-VALIDATED filing."""


@runtime_checkable
class Repository(Protocol):
    # reference
    def upsert_company(self, company: Company) -> None: ...
    def get_company(self, company_id: str) -> Company | None: ...
    def list_companies(self) -> list[Company]: ...

    # filings
    def add_filing(self, filing: Filing) -> None: ...
    def get_filing(self, filing_id: str) -> Filing | None: ...
    def set_data_status(self, filing_id: str, status: DataStatus, reason: str | None) -> None: ...
    def filing_exists(self, file_sha256: str) -> bool: ...

    # extraction / provenance
    def add_line_item(self, item: LineItem) -> None: ...
    def line_items(self, filing_id: str) -> list[LineItem]: ...
    def add_reconciled_value(self, value: ReconciledValue) -> None: ...

    # append-only history
    def append_screen(self, screen: ShariahScreen) -> None: ...
    def append_status_history(self, row: ShariahStatusHistory) -> None: ...
    def append_score_total(self, total: ScoreTotal) -> None: ...
    def append_decision(self, decision: Decision) -> None: ...
    def append_watchlist(self, entry: WatchlistEntry) -> None: ...
    def append_purification(self, row: PurificationRow) -> None: ...

    # portfolio ledger (append-only; state is a fold, never a stored balance)
    def append_ledger_event(self, event: LedgerEvent) -> LedgerEvent: ...
    def ledger_events(self) -> list[LedgerEvent]: ...
    def portfolio_state(self) -> PortfolioState: ...

    # queries
    def exception_queue(self) -> list[Filing]: ...


class InMemoryRepository:
    """Deterministic, dependency-free backend used for tests and dry runs."""

    def __init__(self) -> None:
        self._companies: dict[str, Company] = {}
        self._filings: dict[str, Filing] = {}
        self._line_items: list[LineItem] = []
        self._reconciled: list[ReconciledValue] = []
        self._screens: list[ShariahScreen] = []
        self._status_history: list[ShariahStatusHistory] = []
        self._score_totals: list[ScoreTotal] = []
        self._decisions: list[Decision] = []
        self._watchlist: list[WatchlistEntry] = []
        self._purification: list[PurificationRow] = []
        self._ledger: list[LedgerEvent] = []

    # ---- reference ----
    def upsert_company(self, company: Company) -> None:
        self._companies[company.id] = company

    def get_company(self, company_id: str) -> Company | None:
        return self._companies.get(company_id)

    def list_companies(self) -> list[Company]:
        return list(self._companies.values())

    # ---- filings ----
    def add_filing(self, filing: Filing) -> None:
        if any(f.file_sha256 == filing.file_sha256 for f in self._filings.values()):
            # idempotency via the unique file hash (scheduler safety)
            return
        self._filings[filing.id] = filing

    def get_filing(self, filing_id: str) -> Filing | None:
        return self._filings.get(filing_id)

    def set_data_status(self, filing_id: str, status: DataStatus, reason: str | None) -> None:
        f = self._filings[filing_id]
        # filings is a mutable table (not append-only history); replace the row.
        from dataclasses import replace

        self._filings[filing_id] = replace(f, data_status=status, status_reason=reason)

    def filing_exists(self, file_sha256: str) -> bool:
        return any(f.file_sha256 == file_sha256 for f in self._filings.values())

    # ---- extraction / provenance ----
    def add_line_item(self, item: LineItem) -> None:
        # LineItem.__post_init__ already enforces provenance.
        self._line_items.append(item)

    def line_items(self, filing_id: str) -> list[LineItem]:
        return [li for li in self._line_items if li.filing_id == filing_id]

    def add_reconciled_value(self, value: ReconciledValue) -> None:
        self._reconciled.append(value)

    # ---- append-only history (guarded) ----
    def _require_validated(self, filing_id: str) -> None:
        f = self._filings.get(filing_id)
        if f is None:
            raise NotValidatedError(f"unknown filing {filing_id}")
        if f.data_status is not DataStatus.VALIDATED:
            raise NotValidatedError(
                f"filing {filing_id} has data_status={f.data_status.value}; cannot screen or score"
            )

    def append_screen(self, screen: ShariahScreen) -> None:
        self._require_validated(screen.filing_id)
        self._screens.append(screen)

    def append_status_history(self, row: ShariahStatusHistory) -> None:
        self._status_history.append(row)

    def append_score_total(self, total: ScoreTotal) -> None:
        self._require_validated(total.filing_id)
        self._score_totals.append(total)

    def append_decision(self, decision: Decision) -> None:
        self._decisions.append(decision)

    def append_watchlist(self, entry: WatchlistEntry) -> None:
        self._watchlist.append(entry)

    def append_purification(self, row: PurificationRow) -> None:
        self._purification.append(row)

    # ---- portfolio ledger ----
    def append_ledger_event(self, event: LedgerEvent) -> LedgerEvent:
        """Append one event, assigning the next sequence number.

        The caller does not choose ``seq``: the store owns fold order, exactly
        as ``ledger_events.seq`` (BIGSERIAL) does in Postgres. Applying the
        event before storing it means an impossible movement — an unpayable buy,
        a sale of unheld shares — is rejected here rather than corrupting the
        log (ARCHITECTURE_V2 §6).
        """
        from dataclasses import replace as _replace

        numbered = _replace(event, seq=len(self._ledger) + 1)
        fold([numbered], initial=self.portfolio_state())  # raises if impossible
        self._ledger.append(numbered)
        return numbered

    def ledger_events(self) -> list[LedgerEvent]:
        return list(self._ledger)

    def portfolio_state(self) -> PortfolioState:
        """Derive current state. Never cached — the fold IS the balance."""
        return fold(self._ledger)

    # ---- queries ----
    def exception_queue(self) -> list[Filing]:
        return [
            f for f in self._filings.values()
            if f.data_status in (DataStatus.INSUFFICIENT, DataStatus.CONFLICT)
        ]

    def screens(self, filing_id: str) -> list[ShariahScreen]:
        return [s for s in self._screens if s.filing_id == filing_id]

    def decisions(self) -> list[Decision]:
        return list(self._decisions)


def get_repository(backend: str | None = None) -> Repository:
    """Factory. Defaults to the in-memory backend.

    ``supabase`` is intentionally not wired here — it requires credentials and
    the ``supabase`` package (``pip install -e '.[db]'``). Selecting it without
    that setup fails loudly rather than silently degrading.
    """
    backend = backend or os.environ.get("EGX_STORE_BACKEND", "memory")
    if backend == "memory":
        return InMemoryRepository()
    if backend == "supabase":
        raise NotImplementedError(
            "supabase backend is not wired in this build; set EGX_STORE_BACKEND=memory "
            "or implement db.repo.SupabaseRepository against db/schema.sql"
        )
    raise ValueError(f"unknown store backend {backend!r}")
