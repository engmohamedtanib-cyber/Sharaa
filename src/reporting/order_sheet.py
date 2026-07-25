"""Order sheet generation (BUILD_SPEC Phase 5, CLAUDE.md R6).

The system generates order **instructions**. It never places, routes or
transmits an order — there is no brokerage integration and no execution path
(R6). A human enters these in Thndr.

Two invariants are enforced structurally rather than by review:
  * every order is a LIMIT order — :class:`OrderType` has no market member, so
    a market order cannot be represented, let alone printed
  * every order carries a limit price and a validity

Pure module: no I/O, no clock reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from enum import StrEnum

from engine.types import DecisionType

#: EGX trades in whole shares.
_SHARE_QUANTUM = Decimal(1)


class OrderType(StrEnum):
    """Deliberately limit-only. There is no MARKET member (BUILD_SPEC P5)."""

    LIMIT = "LIMIT"


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class Validity(StrEnum):
    DAY = "DAY"
    GTC = "GTC"


#: Decisions that produce an order, and the side they imply.
_SIDE_FOR_DECISION: dict[DecisionType, Side] = {
    DecisionType.BUY: Side.BUY,
    DecisionType.ADD: Side.BUY,
    DecisionType.REDUCE: Side.SELL,
    DecisionType.SELL: Side.SELL,
    DecisionType.REMOVE: Side.SELL,
}

#: Decisions that must never generate an order. HOLD_FROZEN is explicit: a
#: frozen position takes no adds under any circumstance (§5.1).
_NO_ORDER = frozenset({DecisionType.HOLD, DecisionType.HOLD_FROZEN, DecisionType.NO_ACTION})


class OrderGenerationError(ValueError):
    """Raised when an order cannot be stated completely and safely."""


@dataclass(frozen=True)
class Order:
    """One line of the manual order sheet."""

    egx_code: str
    side: Side
    quantity: Decimal
    limit_price: Decimal
    validity: Validity
    order_type: OrderType = OrderType.LIMIT
    decision_id: str | None = None
    note: str = ""

    def __post_init__(self) -> None:
        if self.order_type is not OrderType.LIMIT:  # pragma: no cover - unrepresentable
            raise OrderGenerationError("only limit orders may be generated (BUILD_SPEC Phase 5)")
        if self.quantity <= 0:
            raise OrderGenerationError(f"{self.egx_code}: quantity must be positive")
        if self.quantity != self.quantity.to_integral_value():
            raise OrderGenerationError(f"{self.egx_code}: quantity must be a whole number of shares")
        if self.limit_price <= 0:
            raise OrderGenerationError(f"{self.egx_code}: limit price must be positive")


def side_for(decision: DecisionType) -> Side | None:
    """Map a decision to an order side, or ``None`` when it generates no order."""
    if decision in _NO_ORDER:
        return None
    return _SIDE_FOR_DECISION.get(decision)


def limit_price(
    reference_price: Decimal,
    side: Side,
    slippage_tolerance: Decimal,
) -> Decimal:
    """Compute a limit price that will not chase the market.

    Buys are capped above the reference and sells floored below it by
    ``slippage_tolerance``, rounded to piastres. Priced to control cost, never
    to guarantee a fill — an unfilled order is recoverable, an overpaid one is
    not.
    """
    if reference_price <= 0:
        raise OrderGenerationError("reference price must be positive")
    if slippage_tolerance < 0:
        raise OrderGenerationError("slippage tolerance must not be negative")
    factor = (Decimal(1) + slippage_tolerance) if side is Side.BUY else (Decimal(1) - slippage_tolerance)
    price = (reference_price * factor).quantize(Decimal("0.01"))
    if price <= 0:
        raise OrderGenerationError("computed limit price is not positive")
    return price


def whole_shares(trade_value: Decimal, price: Decimal) -> Decimal:
    """Shares affordable at ``price``, rounded **down** to a whole share.

    Rounding down keeps the order inside the intended value; rounding up would
    quietly exceed the position cap.
    """
    if price <= 0:
        raise OrderGenerationError("price must be positive")
    return (trade_value / price).quantize(_SHARE_QUANTUM, rounding=ROUND_DOWN)


def build_order(
    *,
    egx_code: str,
    decision: DecisionType,
    trade_value: Decimal,
    reference_price: Decimal,
    slippage_tolerance: Decimal = Decimal("0.01"),
    validity: Validity = Validity.DAY,
    decision_id: str | None = None,
    note: str = "",
) -> Order | None:
    """Build one order line, or ``None`` when the decision generates no order.

    Returns ``None`` (rather than raising) when the affordable quantity rounds
    to zero shares — an uneconomic trade is suppressed, not forced.
    """
    side = side_for(decision)
    if side is None:
        return None

    price = limit_price(reference_price, side, slippage_tolerance)
    quantity = whole_shares(trade_value, price)
    if quantity <= 0:
        return None

    return Order(
        egx_code=egx_code,
        side=side,
        quantity=quantity,
        limit_price=price,
        validity=validity,
        decision_id=decision_id,
        note=note,
    )


def render_order_sheet(orders: list[Order]) -> str:
    """Render the manual order sheet a human enters into Thndr (R6)."""
    header = (
        "EGX SHARIAH ENGINE — ORDER SHEET\n"
        "Enter these manually. The system never places orders (CLAUDE.md R6).\n\n"
        f"{'TICKER':<10}{'SIDE':<6}{'QTY':>12}{'TYPE':>8}{'LIMIT':>12}{'VALIDITY':>10}\n"
        f"{'-' * 58}\n"
    )
    if not orders:
        return header + "(no orders this review)\n"

    lines = [
        f"{o.egx_code:<10}{o.side.value:<6}{o.quantity:>12}{o.order_type.value:>8}"
        f"{o.limit_price:>12}{o.validity.value:>10}"
        for o in orders
    ]
    body = "\n".join(lines)
    notes = [f"  {o.egx_code}: {o.note}" for o in orders if o.note]
    footer = "\n\nNotes:\n" + "\n".join(notes) if notes else ""
    return header + body + footer + "\n"
