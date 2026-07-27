"""Append-only order-proposal store (``ARCHITECTURE_V2`` §6, roadmap M3).

A proposal is **not** a holding. The user places orders in their broker by hand;
we learn what actually happened only when they tell us. So a proposal's life is
recorded as a small event log — proposed, then filled / partially filled /
cancelled / expired — and the current status is a fold, exactly like the
portfolio itself.

Why not a status column: a mutable status can be set to FILLED without a fill
ever being reported, and nothing in the file would show that it happened. With a
log, the fill event carries the quantity, the price and the user's own words, or
it does not exist.

The legal transitions live in :mod:`engine.ledger` and are enforced here on
every append, so an impossible history cannot be written in the first place.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

from common.decimals import ZERO
from engine.ledger import IllegalTransitionError, OrderStatus, transition
from reporting.order_sheet import Side, Validity


class OrderStoreError(RuntimeError):
    """The order log is malformed or describes an impossible history."""


class OrderAction(StrEnum):
    """What happened to a proposal."""

    PROPOSED = "PROPOSED"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


#: Which status each action moves the proposal to.
_ACTION_STATUS: dict[OrderAction, OrderStatus] = {
    OrderAction.PROPOSED: OrderStatus.PROPOSED,
    OrderAction.FILLED: OrderStatus.FILLED,
    OrderAction.PARTIALLY_FILLED: OrderStatus.PARTIALLY_FILLED,
    OrderAction.CANCELLED: OrderStatus.CANCELLED,
    OrderAction.EXPIRED: OrderStatus.EXPIRED,
}

_DECIMAL_FIELDS = ("quantity", "limit_price", "filled_quantity", "fill_price", "fees", "taxes")


@dataclass(frozen=True)
class OrderRecord:
    """A proposal's current state, folded from its events."""

    order_id: str
    ticker: str
    side: Side
    quantity: Decimal
    limit_price: Decimal
    validity: Validity
    status: OrderStatus
    created_at: str
    updated_at: str
    filled_quantity: Decimal = ZERO
    decision_id: str | None = None
    note: str = ""

    @property
    def outstanding(self) -> Decimal:
        """Shares still unfilled. Zero for a terminal order."""
        remaining = self.quantity - self.filled_quantity
        return remaining if remaining > ZERO else ZERO

    @property
    def open(self) -> bool:
        return self.status in {OrderStatus.PROPOSED, OrderStatus.PARTIALLY_FILLED}


def _dec(raw: Any) -> Decimal | None:
    return None if raw is None else Decimal(str(raw))


class JsonlOrderStore:
    """Append-only proposal log backed by a JSONL file in the repository."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    # -- reads ---------------------------------------------------------
    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    rows.append(json.loads(stripped))
                except json.JSONDecodeError as exc:
                    raise OrderStoreError(f"{self.path}:{line_no} is not valid JSON: {exc}") from exc
        return rows

    def state(self) -> dict[str, OrderRecord]:
        """Fold the log into current proposals, keyed by ``order_id``."""
        orders: dict[str, OrderRecord] = {}
        for row in self.read_all():
            orders = _apply(orders, row)
        return orders

    def get(self, order_id: str) -> OrderRecord | None:
        return self.state().get(order_id)

    def open_orders(self) -> list[OrderRecord]:
        return sorted((o for o in self.state().values() if o.open), key=lambda o: o.order_id)

    # -- the only write ------------------------------------------------
    def append(self, row: dict[str, Any]) -> OrderRecord:
        """Validate the event against current state, then append one line."""
        orders = self.state()
        updated = _apply(orders, row)          # raises on an illegal history
        record = updated[str(row["order_id"])]

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            fh.flush()
        return record

    def propose(
        self,
        *,
        order_id: str,
        at: str,
        ticker: str,
        side: Side,
        quantity: Decimal,
        limit_price: Decimal,
        validity: Validity,
        decision_id: str | None = None,
        note: str = "",
    ) -> OrderRecord:
        return self.append(
            {
                "order_id": order_id,
                "at": at,
                "action": OrderAction.PROPOSED.value,
                "ticker": ticker,
                "side": side.value,
                "quantity": str(quantity),
                "limit_price": str(limit_price),
                "validity": validity.value,
                **({"decision_id": decision_id} if decision_id else {}),
                **({"note": note} if note else {}),
            }
        )

    def record_action(
        self,
        *,
        order_id: str,
        at: str,
        action: OrderAction,
        filled_quantity: Decimal | None = None,
        fill_price: Decimal | None = None,
        verbatim: str = "",
    ) -> OrderRecord:
        row: dict[str, Any] = {"order_id": order_id, "at": at, "action": action.value}
        if filled_quantity is not None:
            row["filled_quantity"] = str(filled_quantity)
        if fill_price is not None:
            row["fill_price"] = str(fill_price)
        if verbatim:
            row["verbatim"] = verbatim
        return self.append(row)


def _apply(orders: dict[str, OrderRecord], row: dict[str, Any]) -> dict[str, OrderRecord]:
    """Apply one log row to the folded state."""
    try:
        order_id = str(row["order_id"])
        action = OrderAction(str(row["action"]))
        at = str(row["at"])
    except (KeyError, ValueError) as exc:
        raise OrderStoreError(f"malformed order event {row!r}: {exc}") from exc

    out = dict(orders)

    if action is OrderAction.PROPOSED:
        if order_id in out:
            raise OrderStoreError(f"order {order_id} was proposed twice")
        quantity = _dec(row.get("quantity"))
        limit_price = _dec(row.get("limit_price"))
        if quantity is None or limit_price is None:
            raise OrderStoreError(f"order {order_id}: a proposal needs quantity and limit_price")
        if quantity <= ZERO or limit_price <= ZERO:
            raise OrderStoreError(f"order {order_id}: quantity and limit_price must be positive")
        try:
            side = Side(str(row["side"]))
            validity = Validity(str(row.get("validity", Validity.DAY.value)))
        except (KeyError, ValueError) as exc:
            raise OrderStoreError(f"order {order_id}: {exc}") from exc
        out[order_id] = OrderRecord(
            order_id=order_id,
            ticker=str(row["ticker"]).upper(),
            side=side,
            quantity=quantity,
            limit_price=limit_price,
            validity=validity,
            status=OrderStatus.PROPOSED,
            created_at=at,
            updated_at=at,
            decision_id=row.get("decision_id"),
            note=str(row.get("note", "")),
        )
        return out

    current = out.get(order_id)
    if current is None:
        raise OrderStoreError(f"order {order_id} was {action.value} before it was proposed")

    try:
        new_status = transition(current.status, _ACTION_STATUS[action])
    except IllegalTransitionError as exc:
        # Re-raised as a store error so callers handle one exception type for
        # "this history is impossible", wherever the impossibility came from.
        raise OrderStoreError(f"order {order_id}: {exc}") from exc

    filled = current.filled_quantity
    if action in {OrderAction.FILLED, OrderAction.PARTIALLY_FILLED}:
        delta = _dec(row.get("filled_quantity"))
        if delta is None or delta <= ZERO:
            raise OrderStoreError(f"order {order_id}: a fill needs a positive filled_quantity")
        filled = current.filled_quantity + delta
        if filled > current.quantity:
            raise OrderStoreError(
                f"order {order_id}: fills total {filled} against a proposal of {current.quantity}. "
                "A fill larger than the order is a mis-reported trade, not a bigger position."
            )

    out[order_id] = replace(current, status=new_status, filled_quantity=filled, updated_at=at)
    return out
