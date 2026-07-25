"""Event-sourced portfolio ledger (ARCHITECTURE_V2 §6, §12.2).

This module holds the user's money. Every invariant gets a test, and every
refusal gets a test proving it refuses.
"""

from __future__ import annotations

import pytest

from conftest import D
from engine.ledger import (
    EventOrderError,
    EventShapeError,
    EventSource,
    EventType,
    Holding,
    IllegalTransitionError,
    InsufficientCashError,
    InsufficientSharesError,
    LedgerEvent,
    OrderStatus,
    PortfolioState,
    actual_weights,
    apply_event,
    cash_weight,
    fold,
    is_terminal,
    transition,
)

DATE = "2026-07-25"


def ev(seq: int, event_type: EventType, **kw) -> LedgerEvent:
    return LedgerEvent(seq=seq, event_type=event_type, occurred_at=DATE, **kw)


def contribution(seq: int, amount: str) -> LedgerEvent:
    return ev(seq, EventType.CONTRIBUTION, amount=D(amount))


def buy(seq: int, ticker: str, shares: str, price: str, fees: str = "0") -> LedgerEvent:
    return ev(seq, EventType.BUY_FILLED, ticker=ticker, shares=D(shares), price=D(price), fees=D(fees))


def sell(seq: int, ticker: str, shares: str, price: str, fees: str = "0") -> LedgerEvent:
    return ev(seq, EventType.SELL_FILLED, ticker=ticker, shares=D(shares), price=D(price), fees=D(fees))


# ======================================================================
# Cash is a fold, never a stored number
# ======================================================================
def test_empty_ledger_is_zero():
    s = fold([])
    assert s.cash == D("0")
    assert s.holdings == {}
    assert s.last_seq == 0


def test_contribution_increases_cash():
    s = fold([contribution(1, "25000")])
    assert s.cash == D("25000")
    assert s.total_contributed == D("25000")


def test_multiple_contributions_accumulate():
    s = fold([contribution(1, "25000"), contribution(2, "5000")])
    assert s.cash == D("30000")
    assert s.total_contributed == D("30000")


def test_withdrawal_reduces_cash():
    s = fold([contribution(1, "25000"), ev(2, EventType.WITHDRAWAL, amount=D("5000"))])
    assert s.cash == D("20000")
    assert s.total_withdrawn == D("5000")


def test_withdrawal_beyond_cash_refused():
    with pytest.raises(InsufficientCashError):
        fold([contribution(1, "100"), ev(2, EventType.WITHDRAWAL, amount=D("500"))])


def test_dividend_adds_cash_and_is_tracked():
    s = fold([contribution(1, "1000"), ev(2, EventType.DIVIDEND_RECEIVED, ticker="COMI", amount=D("312.50"))])
    assert s.cash == D("1312.50")
    assert s.dividends_received == D("312.50")


def test_standalone_fee_reduces_cash():
    s = fold([contribution(1, "1000"), ev(2, EventType.FEE_CHARGED, amount=D("25"))])
    assert s.cash == D("975")
    assert s.fees_paid == D("25")


def test_fee_beyond_cash_refused():
    with pytest.raises(InsufficientCashError):
        fold([contribution(1, "10"), ev(2, EventType.FEE_CHARGED, amount=D("25"))])


# ======================================================================
# Buying
# ======================================================================
def test_buy_creates_holding_and_debits_cash():
    s = fold([contribution(1, "25000"), buy(2, "COMI", "190", "52.10", fees="20")])
    assert s.shares_of("COMI") == D("190")
    # 190 * 52.10 = 9899 ; + 20 fees = 9919
    assert s.cash == D("25000") - D("9919")
    assert s.holdings["COMI"].cost_basis == D("9919")
    assert s.fees_paid == D("20")


def test_buy_includes_fees_in_cost_basis():
    s = fold([contribution(1, "10000"), buy(2, "COMI", "100", "50", fees="30")])
    assert s.holdings["COMI"].cost_basis == D("5030")
    assert s.holdings["COMI"].avg_cost == D("50.30")


def test_second_buy_averages_cost():
    s = fold([
        contribution(1, "20000"),
        buy(2, "COMI", "100", "50"),
        buy(3, "COMI", "100", "60"),
    ])
    assert s.shares_of("COMI") == D("200")
    assert s.holdings["COMI"].cost_basis == D("11000")
    assert s.holdings["COMI"].avg_cost == D("55")


def test_buy_without_cash_refused():
    """The ledger refuses an unpayable buy rather than going negative."""
    with pytest.raises(InsufficientCashError, match="buy of COMI"):
        fold([contribution(1, "100"), buy(2, "COMI", "190", "52.10")])


def test_buy_exactly_exhausting_cash_allowed():
    s = fold([contribution(1, "5000"), buy(2, "COMI", "100", "50")])
    assert s.cash == D("0")


# ======================================================================
# Selling
# ======================================================================
def test_sell_reduces_shares_and_credits_cash():
    s = fold([
        contribution(1, "20000"),
        buy(2, "COMI", "100", "50"),
        sell(3, "COMI", "40", "60", fees="10"),
    ])
    assert s.shares_of("COMI") == D("60")
    # proceeds = 40*60 - 10 = 2390
    assert s.cash == D("20000") - D("5000") + D("2390")


def test_sell_relieves_cost_basis_proportionally():
    s = fold([
        contribution(1, "20000"),
        buy(2, "COMI", "100", "50"),      # basis 5000
        sell(3, "COMI", "40", "60"),      # relieve 40% => 2000
    ])
    assert s.holdings["COMI"].cost_basis == D("3000")
    assert s.holdings["COMI"].avg_cost == D("50")   # average unchanged by a partial sale


def test_sell_records_realised_pnl():
    s = fold([
        contribution(1, "20000"),
        buy(2, "COMI", "100", "50"),
        sell(3, "COMI", "40", "60"),
    ])
    # proceeds 2400 - relieved basis 2000 = 400
    assert s.realised_pnl == D("400")


def test_sell_at_a_loss_records_negative_pnl():
    s = fold([
        contribution(1, "20000"),
        buy(2, "COMI", "100", "50"),
        sell(3, "COMI", "100", "40"),
    ])
    assert s.realised_pnl == D("-1000")


def test_full_sale_closes_position():
    s = fold([
        contribution(1, "20000"),
        buy(2, "COMI", "100", "50"),
        sell(3, "COMI", "100", "55"),
    ])
    assert s.shares_of("COMI") == D("0")
    assert s.holdings["COMI"].cost_basis == D("0")
    assert s.holdings["COMI"].avg_cost is None
    assert "COMI" not in s.open_holdings


def test_sell_more_than_held_refused():
    with pytest.raises(InsufficientSharesError, match="only 100 held"):
        fold([contribution(1, "20000"), buy(2, "COMI", "100", "50"), sell(3, "COMI", "150", "60")])


def test_sell_unheld_ticker_refused():
    with pytest.raises(InsufficientSharesError):
        fold([contribution(1, "20000"), sell(2, "SWDY", "10", "60")])


# ======================================================================
# Corporate actions (§12.2) — the five-year silent-corruption guard
# ======================================================================
def test_split_scales_shares_and_preserves_cost_basis():
    s = fold([
        contribution(1, "20000"),
        buy(2, "COMI", "100", "50"),                                  # basis 5000
        ev(3, EventType.SHARE_SPLIT, ticker="COMI", ratio=D("2")),    # 2-for-1
    ])
    assert s.shares_of("COMI") == D("200")
    assert s.holdings["COMI"].cost_basis == D("5000")   # unchanged
    assert s.holdings["COMI"].avg_cost == D("25")       # halved, correctly


def test_bonus_issue_scales_shares():
    s = fold([
        contribution(1, "20000"),
        buy(2, "COMI", "100", "50"),
        ev(3, EventType.BONUS_ISSUE, ticker="COMI", ratio=D("1.1")),  # 10% bonus
    ])
    assert s.shares_of("COMI") == D("110")
    assert s.holdings["COMI"].cost_basis == D("5000")


def test_split_does_not_move_cash():
    s = fold([
        contribution(1, "20000"),
        buy(2, "COMI", "100", "50"),
        ev(3, EventType.SHARE_SPLIT, ticker="COMI", ratio=D("2")),
    ])
    assert s.cash == D("15000")


def test_corporate_action_on_unheld_ticker_creates_nothing():
    """A split on something we do not own must not fabricate a position."""
    s = fold([contribution(1, "1000"), ev(2, EventType.SHARE_SPLIT, ticker="SWDY", ratio=D("2"))])
    assert "SWDY" not in s.holdings
    assert s.cash == D("1000")


def test_corporate_action_on_closed_position_is_noop():
    s = fold([
        contribution(1, "20000"),
        buy(2, "COMI", "100", "50"),
        sell(3, "COMI", "100", "50"),
        ev(4, EventType.SHARE_SPLIT, ticker="COMI", ratio=D("2")),
    ])
    assert s.shares_of("COMI") == D("0")


def test_selling_after_split_uses_new_share_count():
    s = fold([
        contribution(1, "20000"),
        buy(2, "COMI", "100", "50"),
        ev(3, EventType.SHARE_SPLIT, ticker="COMI", ratio=D("2")),
        sell(4, "COMI", "150", "30"),   # only possible because the split doubled shares
    ])
    assert s.shares_of("COMI") == D("50")


# ======================================================================
# Purification (ENGINE_SPEC §8)
# ======================================================================
def test_accrual_creates_obligation_without_moving_cash():
    s = fold([contribution(1, "1000"), ev(2, EventType.PURIFICATION_ACCRUED, ticker="COMI", amount=D("50"))])
    assert s.cash == D("1000")
    assert s.purification_due == D("50")


def test_settlement_pays_obligation_and_moves_cash():
    s = fold([
        contribution(1, "1000"),
        ev(2, EventType.PURIFICATION_ACCRUED, ticker="COMI", amount=D("50")),
        ev(3, EventType.PURIFICATION_SETTLED, amount=D("50")),
    ])
    assert s.cash == D("950")
    assert s.purification_due == D("0")


def test_partial_settlement_leaves_balance():
    s = fold([
        contribution(1, "1000"),
        ev(2, EventType.PURIFICATION_ACCRUED, ticker="COMI", amount=D("50")),
        ev(3, EventType.PURIFICATION_SETTLED, amount=D("20")),
    ])
    assert s.purification_due == D("30")


def test_settlement_beyond_cash_refused():
    with pytest.raises(InsufficientCashError, match="purification"):
        fold([
            contribution(1, "10"),
            ev(2, EventType.PURIFICATION_ACCRUED, ticker="COMI", amount=D("50")),
            ev(3, EventType.PURIFICATION_SETTLED, amount=D("50")),
        ])


# ======================================================================
# Notes and ordering
# ======================================================================
def test_note_changes_nothing_but_advances_seq():
    s = fold([contribution(1, "1000"), ev(2, EventType.NOTE, memo="user is nervous about FX")])
    assert s.cash == D("1000")
    assert s.last_seq == 2


def test_out_of_order_event_refused():
    with pytest.raises(EventOrderError):
        fold([contribution(2, "1000"), contribution(1, "500")])


def test_duplicate_seq_refused():
    with pytest.raises(EventOrderError):
        fold([contribution(1, "1000"), contribution(1, "500")])


def test_apply_event_advances_last_seq():
    s = apply_event(PortfolioState(), contribution(7, "100"))
    assert s.last_seq == 7


def test_fold_accepts_initial_state():
    start = fold([contribution(1, "1000")])
    s = fold([contribution(2, "500")], initial=start)
    assert s.cash == D("1500")


# ======================================================================
# Determinism (R2)
# ======================================================================
def test_fold_is_deterministic():
    events = [
        contribution(1, "25000"),
        buy(2, "COMI", "190", "52.10", fees="20"),
        ev(3, EventType.DIVIDEND_RECEIVED, ticker="COMI", amount=D("312.50")),
        ev(4, EventType.SHARE_SPLIT, ticker="COMI", ratio=D("2")),
        sell(5, "COMI", "100", "28", fees="15"),
    ]
    results = {
        (
            fold(events).cash,
            fold(events).shares_of("COMI"),
            fold(events).holdings["COMI"].cost_basis,
            fold(events).realised_pnl,
        )
        for _ in range(50)
    }
    assert len(results) == 1


def test_replay_from_scratch_matches_incremental():
    events = [contribution(1, "25000"), buy(2, "COMI", "100", "50"), sell(3, "COMI", "40", "60")]
    full = fold(events)
    incremental = PortfolioState()
    for e in events:
        incremental = apply_event(incremental, e)
    assert full == incremental


# ======================================================================
# Event shape validation
# ======================================================================
def test_seq_must_be_positive():
    with pytest.raises(EventShapeError):
        ev(0, EventType.NOTE)


def test_amount_events_require_amount():
    with pytest.raises(EventShapeError, match="requires an amount"):
        ev(1, EventType.CONTRIBUTION)


def test_amount_must_be_positive():
    with pytest.raises(EventShapeError, match="must be positive"):
        ev(1, EventType.CONTRIBUTION, amount=D("0"))


def test_negative_amount_refused():
    with pytest.raises(EventShapeError):
        ev(1, EventType.CONTRIBUTION, amount=D("-100"))


def test_trade_requires_ticker():
    with pytest.raises(EventShapeError, match="requires a ticker"):
        ev(1, EventType.BUY_FILLED, shares=D("10"), price=D("50"))


def test_trade_requires_shares_and_price():
    with pytest.raises(EventShapeError, match="requires shares and price"):
        ev(1, EventType.BUY_FILLED, ticker="COMI", shares=D("10"))


def test_fractional_shares_refused():
    with pytest.raises(EventShapeError, match="whole"):
        ev(1, EventType.BUY_FILLED, ticker="COMI", shares=D("10.5"), price=D("50"))


def test_nonpositive_shares_refused():
    with pytest.raises(EventShapeError):
        ev(1, EventType.BUY_FILLED, ticker="COMI", shares=D("0"), price=D("50"))


def test_nonpositive_price_refused():
    with pytest.raises(EventShapeError):
        ev(1, EventType.BUY_FILLED, ticker="COMI", shares=D("10"), price=D("0"))


def test_negative_fees_refused():
    with pytest.raises(EventShapeError, match="fees"):
        ev(1, EventType.BUY_FILLED, ticker="COMI", shares=D("10"), price=D("50"), fees=D("-1"))


def test_ratio_events_require_ratio():
    with pytest.raises(EventShapeError, match="requires a ratio"):
        ev(1, EventType.SHARE_SPLIT, ticker="COMI")


def test_nonpositive_ratio_refused():
    with pytest.raises(EventShapeError):
        ev(1, EventType.SHARE_SPLIT, ticker="COMI", ratio=D("0"))


def test_dividend_requires_ticker():
    with pytest.raises(EventShapeError, match="requires a ticker"):
        ev(1, EventType.DIVIDEND_RECEIVED, amount=D("100"))


def test_blank_ticker_refused():
    with pytest.raises(EventShapeError, match="requires a ticker"):
        ev(1, EventType.DIVIDEND_RECEIVED, ticker="   ", amount=D("100"))


def test_event_carries_audit_fields():
    e = ev(
        1, EventType.CONTRIBUTION, amount=D("25000"),
        source=EventSource.USER_CONFIRMED, verbatim="I added another 25,000 EGP",
    )
    assert e.source is EventSource.USER_CONFIRMED
    assert "25,000" in e.verbatim


def test_trade_value_helpers():
    e = buy(1, "COMI", "100", "50", fees="20")
    assert e.gross_value == D("5000")
    assert e.cost_including_charges == D("5020")
    s = sell(1, "COMI", "100", "50", fees="20")
    assert s.proceeds_after_charges == D("4980")


def test_gross_value_zero_for_non_trade():
    assert contribution(1, "100").gross_value == D("0")


# ======================================================================
# Valuation and weights
# ======================================================================
def _two_position_state() -> PortfolioState:
    return fold([
        contribution(1, "100000"),
        buy(2, "COMI", "1000", "50"),   # 50000
        buy(3, "SWDY", "1000", "20"),   # 20000
    ])


def test_market_value_and_pnl():
    s = _two_position_state()
    h = s.holdings["COMI"]
    assert h.market_value(D("60")) == D("60000")
    assert h.unrealised_pnl(D("60")) == D("10000")


def test_total_value_includes_cash():
    s = _two_position_state()
    prices = {"COMI": D("50"), "SWDY": D("20")}
    assert s.invested_value(prices) == D("70000")
    assert s.total_value(prices) == D("100000")   # 30000 cash + 70000


def test_weights_sum_with_cash_to_one():
    s = _two_position_state()
    prices = {"COMI": D("50"), "SWDY": D("20")}
    w = actual_weights(s, prices)
    assert w["COMI"] == D("0.5")
    assert w["SWDY"] == D("0.2")
    assert cash_weight(s, prices) == D("0.3")
    assert sum(w.values()) + cash_weight(s, prices) == D("1")


def test_unpriced_holdings_are_reported_not_guessed():
    """A missing price must be surfaced, never substituted with cost."""
    s = _two_position_state()
    prices = {"COMI": D("50")}
    assert s.unpriced_holdings(prices) == ["SWDY"]
    assert s.invested_value(prices) == D("50000")   # SWDY excluded, not valued at cost
    assert "SWDY" not in actual_weights(s, prices)


def test_weights_empty_when_no_value():
    s = PortfolioState()
    assert actual_weights(s, {}) == {}
    assert cash_weight(s, {}) is None


def test_closed_positions_excluded_from_weights():
    s = fold([
        contribution(1, "20000"),
        buy(2, "COMI", "100", "50"),
        sell(3, "COMI", "100", "50"),
    ])
    assert actual_weights(s, {"COMI": D("50")}) == {}


def test_shares_of_unknown_ticker_is_zero():
    assert PortfolioState().shares_of("NOPE") == D("0")


def test_holding_avg_cost_none_when_closed():
    assert Holding("X", D("0"), D("0")).avg_cost is None


# ======================================================================
# Order lifecycle
# ======================================================================
@pytest.mark.parametrize(
    "target",
    [OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED, OrderStatus.EXPIRED, OrderStatus.CANCELLED],
)
def test_proposed_can_move_anywhere(target):
    assert transition(OrderStatus.PROPOSED, target) is target


def test_partial_can_complete_or_lapse():
    assert transition(OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED) is OrderStatus.FILLED
    assert transition(OrderStatus.PARTIALLY_FILLED, OrderStatus.EXPIRED) is OrderStatus.EXPIRED


def test_partial_cannot_go_back_to_proposed():
    with pytest.raises(IllegalTransitionError):
        transition(OrderStatus.PARTIALLY_FILLED, OrderStatus.PROPOSED)


@pytest.mark.parametrize("terminal", [OrderStatus.FILLED, OrderStatus.EXPIRED, OrderStatus.CANCELLED])
def test_terminal_states_are_final(terminal):
    assert is_terminal(terminal) is True
    with pytest.raises(IllegalTransitionError):
        transition(terminal, OrderStatus.PROPOSED)


def test_filled_cannot_be_cancelled():
    with pytest.raises(IllegalTransitionError, match="FILLED to CANCELLED"):
        transition(OrderStatus.FILLED, OrderStatus.CANCELLED)


def test_non_terminal_states():
    assert is_terminal(OrderStatus.PROPOSED) is False
    assert is_terminal(OrderStatus.PARTIALLY_FILLED) is False


def test_unfilled_statuses_are_not_positions():
    from engine.ledger import UNFILLED_STATUSES

    assert OrderStatus.PROPOSED in UNFILLED_STATUSES
    assert OrderStatus.EXPIRED in UNFILLED_STATUSES
    assert OrderStatus.CANCELLED in UNFILLED_STATUSES
    assert OrderStatus.FILLED not in UNFILLED_STATUSES


# ======================================================================
# A realistic multi-year narrative
# ======================================================================
def test_five_year_narrative():
    """Hire -> buy -> dividend -> split -> contribute -> trim -> purify."""
    events = [
        contribution(1, "50000"),
        buy(2, "COMI", "500", "50", fees="50"),                              # -25050
        buy(3, "SWDY", "800", "20", fees="30"),                              # -16030
        ev(4, EventType.DIVIDEND_RECEIVED, ticker="COMI", amount=D("1200")),
        ev(5, EventType.PURIFICATION_ACCRUED, ticker="COMI", amount=D("60")),
        ev(6, EventType.SHARE_SPLIT, ticker="COMI", ratio=D("2")),
        contribution(7, "25000"),
        sell(8, "COMI", "200", "30", fees="25"),
        ev(9, EventType.PURIFICATION_SETTLED, amount=D("60")),
    ]
    s = fold(events)

    assert s.shares_of("COMI") == D("800")     # 500 -> 1000 after split, -200 sold
    assert s.shares_of("SWDY") == D("800")
    assert s.total_contributed == D("75000")
    assert s.dividends_received == D("1200")
    assert s.purification_due == D("0")

    expected_cash = (
        D("50000") - D("25050") - D("16030") + D("1200") + D("25000")
        + (D("200") * D("30") - D("25")) - D("60")
    )
    assert s.cash == expected_cash

    # Cost basis relieved proportionally: 20% of 25050
    assert s.holdings["COMI"].cost_basis == D("25050") * D("0.8")
    assert s.last_seq == 9
