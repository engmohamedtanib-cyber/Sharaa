"""Repository-level ledger behaviour: sequencing, refusal, derived state."""

from __future__ import annotations

import pytest

from conftest import D
from db.repo import InMemoryRepository
from engine.ledger import (
    EventType,
    InsufficientCashError,
    InsufficientSharesError,
    LedgerEvent,
)

DATE = "2026-07-25"


def ev(event_type: EventType, **kw) -> LedgerEvent:
    # seq is a placeholder; the store assigns the real one.
    return LedgerEvent(seq=1, event_type=event_type, occurred_at=DATE, **kw)


def test_store_assigns_sequence_numbers():
    repo = InMemoryRepository()
    a = repo.append_ledger_event(ev(EventType.CONTRIBUTION, amount=D("1000")))
    b = repo.append_ledger_event(ev(EventType.CONTRIBUTION, amount=D("500")))
    assert (a.seq, b.seq) == (1, 2)


def test_state_is_derived_from_events():
    repo = InMemoryRepository()
    repo.append_ledger_event(ev(EventType.CONTRIBUTION, amount=D("25000")))
    repo.append_ledger_event(
        ev(EventType.BUY_FILLED, ticker="COMI", shares=D("190"), price=D("52.10"), fees=D("20"))
    )
    state = repo.portfolio_state()
    assert state.cash == D("25000") - D("9919")
    assert state.shares_of("COMI") == D("190")


def test_impossible_buy_refused_and_not_recorded():
    """A rejected event must leave no trace — the log stays consistent."""
    repo = InMemoryRepository()
    repo.append_ledger_event(ev(EventType.CONTRIBUTION, amount=D("100")))
    with pytest.raises(InsufficientCashError):
        repo.append_ledger_event(
            ev(EventType.BUY_FILLED, ticker="COMI", shares=D("190"), price=D("52.10"))
        )
    assert len(repo.ledger_events()) == 1
    assert repo.portfolio_state().cash == D("100")


def test_impossible_sale_refused():
    repo = InMemoryRepository()
    repo.append_ledger_event(ev(EventType.CONTRIBUTION, amount=D("10000")))
    with pytest.raises(InsufficientSharesError):
        repo.append_ledger_event(
            ev(EventType.SELL_FILLED, ticker="COMI", shares=D("10"), price=D("50"))
        )
    assert len(repo.ledger_events()) == 1


def test_state_recomputes_after_each_append():
    repo = InMemoryRepository()
    repo.append_ledger_event(ev(EventType.CONTRIBUTION, amount=D("1000")))
    assert repo.portfolio_state().cash == D("1000")
    repo.append_ledger_event(ev(EventType.WITHDRAWAL, amount=D("400")))
    assert repo.portfolio_state().cash == D("600")


def test_ledger_events_returns_a_copy():
    repo = InMemoryRepository()
    repo.append_ledger_event(ev(EventType.CONTRIBUTION, amount=D("1000")))
    repo.ledger_events().clear()
    assert len(repo.ledger_events()) == 1


def test_empty_ledger_state():
    assert InMemoryRepository().portfolio_state().cash == D("0")


def test_no_mutation_surface_for_ledger():
    """Append-only: there is no update or delete method to call."""
    repo = InMemoryRepository()
    for forbidden in ("update_ledger_event", "delete_ledger_event", "set_cash"):
        assert not hasattr(repo, forbidden)
