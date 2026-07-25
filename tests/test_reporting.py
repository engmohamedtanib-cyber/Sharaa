"""Order sheet and decision journal (BUILD_SPEC Phase 5, R6, §5.5)."""

from __future__ import annotations

import pytest

from conftest import D
from engine.types import BreachType, DecisionType, ShariahStatus
from reporting.journal import (
    JournalEntry,
    JournalEntryError,
    entry_from_decision,
    render_journal,
)
from reporting.order_sheet import (
    Order,
    OrderGenerationError,
    OrderType,
    Side,
    Validity,
    build_order,
    limit_price,
    render_order_sheet,
    side_for,
    whole_shares,
)


# ======================================================================
# ORDER SHEET
# ======================================================================
def test_order_type_enum_has_no_market_member():
    """A market order must be unrepresentable, not merely discouraged."""
    assert [m.value for m in OrderType] == ["LIMIT"]
    assert not hasattr(OrderType, "MARKET")


@pytest.mark.parametrize(
    "decision,expected",
    [
        (DecisionType.BUY, Side.BUY),
        (DecisionType.ADD, Side.BUY),
        (DecisionType.REDUCE, Side.SELL),
        (DecisionType.SELL, Side.SELL),
        (DecisionType.REMOVE, Side.SELL),
    ],
)
def test_side_for_ordering_decisions(decision, expected):
    assert side_for(decision) is expected


@pytest.mark.parametrize(
    "decision", [DecisionType.HOLD, DecisionType.HOLD_FROZEN, DecisionType.NO_ACTION]
)
def test_non_ordering_decisions_generate_nothing(decision):
    assert side_for(decision) is None


def test_hold_frozen_never_generates_an_order():
    """A frozen position takes no adds under any circumstance (§5.1)."""
    order = build_order(
        egx_code="COMI", decision=DecisionType.HOLD_FROZEN,
        trade_value=D("10000"), reference_price=D("50"),
    )
    assert order is None


def test_limit_price_buy_caps_above_reference():
    assert limit_price(D("100"), Side.BUY, D("0.01")) == D("101.00")


def test_limit_price_sell_floors_below_reference():
    assert limit_price(D("100"), Side.SELL, D("0.01")) == D("99.00")


def test_limit_price_zero_slippage():
    assert limit_price(D("100"), Side.BUY, D("0")) == D("100.00")


def test_limit_price_rejects_nonpositive_reference():
    with pytest.raises(OrderGenerationError):
        limit_price(D("0"), Side.BUY, D("0.01"))


def test_limit_price_rejects_negative_slippage():
    with pytest.raises(OrderGenerationError):
        limit_price(D("100"), Side.BUY, D("-0.01"))


def test_whole_shares_rounds_down():
    # 1000 / 30.00 = 33.33 -> 33 shares, never 34
    assert whole_shares(D("1000"), D("30")) == D("33")


def test_whole_shares_rejects_nonpositive_price():
    with pytest.raises(OrderGenerationError):
        whole_shares(D("1000"), D("0"))


def test_build_order_produces_limit_order():
    o = build_order(
        egx_code="COMI", decision=DecisionType.BUY,
        trade_value=D("10000"), reference_price=D("50"),
    )
    assert o is not None
    assert o.order_type is OrderType.LIMIT
    assert o.side is Side.BUY
    assert o.limit_price == D("50.50")
    assert o.quantity == D("198")   # 10000 / 50.50 rounded down
    assert o.validity is Validity.DAY


def test_build_order_suppresses_zero_quantity():
    o = build_order(
        egx_code="COMI", decision=DecisionType.BUY,
        trade_value=D("10"), reference_price=D("500"),
    )
    assert o is None


def test_order_rejects_fractional_quantity():
    with pytest.raises(OrderGenerationError):
        Order(
            egx_code="COMI", side=Side.BUY, quantity=D("10.5"),
            limit_price=D("50"), validity=Validity.DAY,
        )


def test_order_rejects_nonpositive_quantity():
    with pytest.raises(OrderGenerationError):
        Order(
            egx_code="COMI", side=Side.BUY, quantity=D("0"),
            limit_price=D("50"), validity=Validity.DAY,
        )


def test_order_rejects_nonpositive_price():
    with pytest.raises(OrderGenerationError):
        Order(
            egx_code="COMI", side=Side.BUY, quantity=D("10"),
            limit_price=D("0"), validity=Validity.DAY,
        )


def test_order_sheet_never_contains_a_market_order():
    orders = [
        build_order(egx_code="COMI", decision=DecisionType.BUY,
                    trade_value=D("10000"), reference_price=D("50")),
        build_order(egx_code="SWDY", decision=DecisionType.SELL,
                    trade_value=D("5000"), reference_price=D("20")),
    ]
    sheet = render_order_sheet([o for o in orders if o])
    assert "MARKET" not in sheet.upper()
    # every ticker row states LIMIT as its order type
    rows = [ln for ln in sheet.splitlines() if ln.startswith(("COMI", "SWDY"))]
    assert len(rows) == 2
    assert all("LIMIT" in row for row in rows)


def test_empty_order_sheet_renders():
    assert "(no orders this review)" in render_order_sheet([])


def test_order_sheet_includes_notes():
    o = build_order(
        egx_code="COMI", decision=DecisionType.BUY, trade_value=D("10000"),
        reference_price=D("50"), note="starter position",
    )
    assert "starter position" in render_order_sheet([o])


# ======================================================================
# JOURNAL
# ======================================================================
def _entry(**overrides) -> JournalEntry:
    base = dict(
        review_code="R1-2026",
        egx_code="COMI",
        decision=DecisionType.BUY,
        shariah_status=ShariahStatus.GREEN,
        breach=BreachType.NONE,
        trigger="score>=85 and discount>=0.25",
        reason="High-conviction score 88 with a 0.28 discount to fair value while unheld: initiate.",
        falsification_condition="Score falls below 85 or the valuation gap drops under 0.15 next review.",
    )
    base.update(overrides)
    return entry_from_decision(**base)  # type: ignore[arg-type]


def test_valid_entry_builds():
    assert _entry().egx_code == "COMI"


def test_short_reason_rejected():
    with pytest.raises(JournalEntryError, match="reason"):
        _entry(reason="too short")


def test_short_falsification_rejected():
    with pytest.raises(JournalEntryError):
        _entry(falsification_condition="nope")


def test_empty_trigger_rejected():
    with pytest.raises(JournalEntryError, match="trigger"):
        _entry(trigger="   ")


@pytest.mark.parametrize("placeholder", ["n/a", "N/A", "TBD", "unknown", "to be determined"])
def test_placeholder_falsification_rejected(placeholder):
    with pytest.raises(JournalEntryError):
        _entry(falsification_condition=placeholder.ljust(25, "."))


def test_unfalsifiable_prose_rejected():
    """A condition with no metric and no threshold is not falsifiable."""
    with pytest.raises(JournalEntryError, match="metric"):
        _entry(falsification_condition="the thesis stops making sense to me")


def test_falsification_with_number_accepted():
    assert _entry(falsification_condition="Revenue growth turns negative for 2 quarters.")


def test_falsification_with_comparative_accepted():
    assert _entry(falsification_condition="Shariah status falls to ORANGE or worse.")


def test_render_journal_includes_falsification():
    out = render_journal("R1-2026", [_entry()])
    assert "This decision is wrong if:" in out
    assert "COMI" in out


def test_render_journal_includes_veto():
    out = render_journal("R1-2026", [_entry(decision=DecisionType.REMOVE, veto_fired="LIQUIDITY_6B")])
    assert "LIQUIDITY_6B" in out


def test_render_empty_journal():
    assert "No decisions this review." in render_journal("R1-2026", [])
