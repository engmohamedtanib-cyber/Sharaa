"""Git-native JSONL ledger persistence (decisions/0002)."""

from __future__ import annotations

import json

import pytest

from conftest import D
from engine.ledger import (
    EventSource,
    EventType,
    InsufficientCashError,
    InsufficientSharesError,
    LedgerEvent,
)
from store.jsonl_ledger import (
    JsonlLedgerStore,
    LedgerFileError,
    event_from_dict,
    event_to_dict,
)

DATE = "2026-07-25"


def ev(event_type: EventType, **kw) -> LedgerEvent:
    return LedgerEvent(seq=1, event_type=event_type, occurred_at=DATE, **kw)


@pytest.fixture
def store(tmp_path):
    return JsonlLedgerStore(tmp_path / "portfolio" / "ledger.jsonl")


# ---- empty / creation -------------------------------------------------
def test_missing_file_is_empty_ledger(store):
    assert store.read_all() == []
    assert store.state().cash == D("0")
    assert store.next_seq() == 1


def test_first_append_creates_file_and_parents(store):
    store.append(ev(EventType.CONTRIBUTION, amount=D("25000")))
    assert store.path.exists()
    assert store.state().cash == D("25000")


# ---- round trip -------------------------------------------------------
def test_round_trip_preserves_exact_decimals(store):
    store.append(ev(EventType.CONTRIBUTION, amount=D("25000.55")))
    store.append(
        ev(EventType.BUY_FILLED, ticker="COMI", shares=D("190"), price=D("52.10"), fees=D("20.25"))
    )
    reread = store.read_all()
    assert reread[0].amount == D("25000.55")
    assert reread[1].price == D("52.10")
    assert reread[1].fees == D("20.25")


def test_money_serialised_as_string_never_float(store):
    store.append(ev(EventType.CONTRIBUTION, amount=D("0.1")))
    raw = json.loads(store.path.read_text(encoding="utf-8").strip())
    assert raw["amount"] == "0.1"
    assert isinstance(raw["amount"], str)


def test_decimal_survives_a_value_float_would_corrupt(store):
    store.append(ev(EventType.CONTRIBUTION, amount=D("1234567.89")))
    assert store.read_all()[0].amount == D("1234567.89")


def test_audit_fields_persist(store):
    store.append(
        ev(
            EventType.CONTRIBUTION, amount=D("25000"),
            source=EventSource.USER_CONFIRMED, verbatim="I added another 25,000 EGP",
        )
    )
    e = store.read_all()[0]
    assert e.source is EventSource.USER_CONFIRMED
    assert e.verbatim == "I added another 25,000 EGP"


def test_arabic_memo_preserved(store):
    store.append(ev(EventType.NOTE, memo="المستخدم قلق من سعر الصرف"))
    assert "قلق" in store.read_all()[0].memo


# ---- sequencing -------------------------------------------------------
def test_store_assigns_sequence(store):
    a = store.append(ev(EventType.CONTRIBUTION, amount=D("100")))
    b = store.append(ev(EventType.CONTRIBUTION, amount=D("200")))
    assert (a.seq, b.seq) == (1, 2)
    assert store.next_seq() == 3


def test_sequence_survives_reopen(store, tmp_path):
    store.append(ev(EventType.CONTRIBUTION, amount=D("100")))
    reopened = JsonlLedgerStore(tmp_path / "portfolio" / "ledger.jsonl")
    assert reopened.next_seq() == 2
    assert reopened.state().cash == D("100")


# ---- append-only ------------------------------------------------------
def test_no_mutation_methods_exist(store):
    for forbidden in ("update", "delete", "rewrite", "set_cash", "truncate"):
        assert not hasattr(store, forbidden)


def test_one_line_per_event(store):
    store.append(ev(EventType.CONTRIBUTION, amount=D("100")))
    store.append(ev(EventType.CONTRIBUTION, amount=D("200")))
    lines = [ln for ln in store.path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 2


# ---- refusal leaves the file untouched --------------------------------
def test_impossible_buy_refused_and_nothing_written(store):
    store.append(ev(EventType.CONTRIBUTION, amount=D("100")))
    with pytest.raises(InsufficientCashError):
        store.append(ev(EventType.BUY_FILLED, ticker="COMI", shares=D("190"), price=D("52.10")))
    assert len(store.read_all()) == 1
    assert store.state().cash == D("100")


def test_impossible_sale_refused(store):
    store.append(ev(EventType.CONTRIBUTION, amount=D("10000")))
    with pytest.raises(InsufficientSharesError):
        store.append(ev(EventType.SELL_FILLED, ticker="COMI", shares=D("10"), price=D("50")))
    assert len(store.read_all()) == 1


# ---- corrupt file detection -------------------------------------------
def test_malformed_line_raises(store):
    store.append(ev(EventType.CONTRIBUTION, amount=D("100")))
    with store.path.open("a", encoding="utf-8") as fh:
        fh.write("{not json}\n")
    with pytest.raises(LedgerFileError):
        store.read_all()


def test_out_of_order_file_raises(store, tmp_path):
    p = tmp_path / "portfolio" / "ledger.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"seq": 2, "event_type": "CONTRIBUTION", "occurred_at": DATE, "amount": "100"})
        + "\n"
        + json.dumps({"seq": 1, "event_type": "CONTRIBUTION", "occurred_at": DATE, "amount": "50"})
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(LedgerFileError, match="strictly increasing"):
        JsonlLedgerStore(p).read_all()


def test_blank_lines_ignored(store):
    store.append(ev(EventType.CONTRIBUTION, amount=D("100")))
    with store.path.open("a", encoding="utf-8") as fh:
        fh.write("\n\n")
    assert len(store.read_all()) == 1


# ---- batch ------------------------------------------------------------
def test_append_many(store):
    store.append_many([
        ev(EventType.CONTRIBUTION, amount=D("25000")),
        ev(EventType.BUY_FILLED, ticker="COMI", shares=D("100"), price=D("50")),
    ])
    assert store.state().shares_of("COMI") == D("100")


def test_append_many_keeps_valid_prefix_on_failure(store):
    with pytest.raises(InsufficientCashError):
        store.append_many([
            ev(EventType.CONTRIBUTION, amount=D("100")),
            ev(EventType.BUY_FILLED, ticker="COMI", shares=D("100"), price=D("50")),
        ])
    assert len(store.read_all()) == 1   # the legitimate contribution stayed


# ---- serialisation helpers --------------------------------------------
def test_event_dict_round_trip():
    e = LedgerEvent(
        seq=7, event_type=EventType.SHARE_SPLIT, occurred_at=DATE,
        ticker="COMI", ratio=D("2"), memo="2-for-1",
    )
    assert event_from_dict(event_to_dict(e)) == e


def test_serialisation_omits_empty_fields():
    d = event_to_dict(LedgerEvent(seq=1, event_type=EventType.NOTE, occurred_at=DATE))
    assert "ticker" not in d
    assert "memo" not in d
    assert "amount" not in d


# ---- narrative --------------------------------------------------------
def test_full_narrative_persists_and_replays(store, tmp_path):
    store.append_many([
        ev(EventType.CONTRIBUTION, amount=D("50000")),
        ev(EventType.BUY_FILLED, ticker="COMI", shares=D("500"), price=D("50"), fees=D("50")),
        ev(EventType.DIVIDEND_RECEIVED, ticker="COMI", amount=D("1200")),
        ev(EventType.SHARE_SPLIT, ticker="COMI", ratio=D("2")),
        ev(EventType.SELL_FILLED, ticker="COMI", shares=D("200"), price=D("30"), fees=D("25")),
    ])
    live = store.state()
    replayed = JsonlLedgerStore(tmp_path / "portfolio" / "ledger.jsonl").state()
    assert live == replayed
    assert live.shares_of("COMI") == D("800")
    assert live.dividends_received == D("1200")
