"""Portfolio tools: what the user owns, and recording what they did.

Every write here takes a figure the **user** reported and echoes it back for
confirmation in the message. The agent never supplies a quantity or a price of
its own: it can propose an order (that is an engine output), but the fill is a
fact about the world that only the user observed.

Reads return decision-ready composites rather than raw rows — "what do I own"
answers with weights, cost basis and what is missing a price, because that is
the shape the next question needs.
"""

from __future__ import annotations

from typing import Any

from common.decimals import ZERO
from engine.ledger import EventSource, EventType, LedgerError, LedgerEvent, actual_weights, cash_weight
from store.jsonl_orders import OrderAction, OrderStoreError
from tools.context import ToolContext
from tools.errors import ToolRefusal
from tools.registry import Param, Tool, as_ticker, money, money_map, register, require_positive


# ======================================================================
# Reads
# ======================================================================
def _portfolio_payload(ctx: ToolContext) -> dict[str, Any]:
    state = ctx.ledger.state()
    prices = ctx.prices
    unpriced = state.unpriced_holdings(prices)
    weights = actual_weights(state, prices)
    cw = cash_weight(state, prices)
    return {
        "as_of": ctx.now,
        "cash": money(state.cash),
        "holdings": [
            {
                "ticker": t,
                "shares": str(h.shares),
                "cost_basis": money(h.cost_basis),
                "avg_cost": money(h.avg_cost),
                "price": money(prices.get(t)),
                "market_value": money(h.market_value(prices[t])) if t in prices else None,
                "unrealised_pnl": money(h.unrealised_pnl(prices[t])) if t in prices else None,
                "weight": str(weights[t]) if t in weights else None,
            }
            for t, h in sorted(state.open_holdings.items())
        ],
        "total_contributed": money(state.total_contributed),
        "total_withdrawn": money(state.total_withdrawn),
        "dividends_received": money(state.dividends_received),
        "fees_paid": money(state.fees_paid),
        "realised_pnl": money(state.realised_pnl),
        "purification_due": money(state.purification_due),
        "invested_value": money(state.invested_value(prices)) if prices else None,
        "total_value": money(state.total_value(prices)) if prices else None,
        "cash_weight": str(cw) if cw is not None else None,
        "events_recorded": state.last_seq,
        # Surfaced, never smoothed over: a holding with no price makes every
        # weight in this payload a partial picture, and the caller must know.
        "unpriced_holdings": unpriced,
        "valuation_complete": not unpriced and bool(prices or not state.open_holdings),
    }


def _get_portfolio(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    payload = _portfolio_payload(ctx)
    if payload["unpriced_holdings"]:
        payload["_message"] = (
            "No market price for " + ", ".join(payload["unpriced_holdings"]) +
            ". Weights and total value exclude them — they are not valued at cost."
        )
    return payload


register(Tool(
    name="get_portfolio",
    description="Current cash, holdings, cost basis, weights and purification balance, folded from the ledger.",
    params=(),
    handler=_get_portfolio,
))


def _get_position(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    ticker = as_ticker(args["ticker"])
    state = ctx.ledger.state()
    holding = state.holdings.get(ticker)
    if holding is None or holding.shares == ZERO:
        return {
            "ticker": ticker,
            "held": False,
            "shares": "0",
            "_message": f"No position in {ticker}.",
        }
    price = ctx.prices.get(ticker)
    return {
        "ticker": ticker,
        "held": True,
        "shares": str(holding.shares),
        "cost_basis": money(holding.cost_basis),
        "avg_cost": money(holding.avg_cost),
        "price": money(price),
        "market_value": money(holding.market_value(price)) if price is not None else None,
        "unrealised_pnl": money(holding.unrealised_pnl(price)) if price is not None else None,
    }


register(Tool(
    name="get_position",
    description="One holding: share count, cost basis, average cost, and market value when a price is known.",
    params=(Param("ticker", "string", "EGX code, e.g. COMI."),),
    handler=_get_position,
))


def _get_ledger_history(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    limit = int(args.get("limit") or 20)
    ticker = (args.get("ticker") or "").strip().upper()
    events = ctx.ledger.read_all()
    if ticker:
        events = [e for e in events if (e.ticker or "").upper() == ticker]
    tail = events[-limit:] if limit > 0 else events
    return {
        "count": len(tail),
        "total_events": len(events),
        "events": [
            {
                "seq": e.seq,
                "at": e.occurred_at,
                "type": e.event_type.value,
                "ticker": e.ticker,
                "amount": money(e.amount),
                "shares": str(e.shares) if e.shares is not None else None,
                "price": money(e.price),
                "fees": money(e.fees),
                "memo": e.memo,
                "verbatim": e.verbatim,
                "source": e.source.value,
            }
            for e in tail
        ],
    }


register(Tool(
    name="get_ledger_history",
    description="Recent ledger events, optionally for one ticker. The audit trail behind every balance.",
    params=(
        Param("ticker", "string", "Filter to one EGX code.", required=False),
        Param("limit", "integer", "How many of the most recent events to return (default 20).", required=False),
    ),
    handler=_get_ledger_history,
))


def _get_purification_balance(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    state = ctx.ledger.state()
    due = state.purification_due
    return {
        "accrued": money(state.purification_accrued),
        "settled": money(state.purification_settled),
        "due": money(due),
        "_message": (
            f"{due} EGP of purification is outstanding — give it away and tell me the reference."
            if due > ZERO else "Nothing outstanding to purify."
        ),
    }


register(Tool(
    name="get_purification_balance",
    description="Impure income accrued, settled, and still owed (tathir). Folded from the ledger.",
    params=(),
    handler=_get_purification_balance,
))


def _get_order_proposals(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    include_closed = bool(args.get("include_closed") or False)
    records = ctx.orders.state().values()
    chosen = sorted(
        (o for o in records if include_closed or o.open),
        key=lambda o: (o.created_at, o.order_id),
    )
    return {
        "count": len(chosen),
        "orders": [
            {
                "order_id": o.order_id,
                "ticker": o.ticker,
                "side": o.side.value,
                "quantity": str(o.quantity),
                "filled_quantity": str(o.filled_quantity),
                "outstanding": str(o.outstanding),
                "limit_price": money(o.limit_price),
                "validity": o.validity.value,
                "status": o.status.value,
                "created_at": o.created_at,
                "updated_at": o.updated_at,
                "decision_id": o.decision_id,
                "note": o.note,
            }
            for o in chosen
        ],
        "_message": (
            "No open proposals." if not chosen and not include_closed else ""
        ),
    }


register(Tool(
    name="get_order_proposals",
    description="Outstanding order proposals awaiting the user's fill confirmation. A proposal is never a holding.",
    params=(Param("include_closed", "boolean", "Include filled/cancelled/expired orders.", required=False),),
    handler=_get_order_proposals,
))


# ======================================================================
# Writes — cash movements
# ======================================================================
def _append(ctx: ToolContext, event: LedgerEvent) -> LedgerEvent:
    """Append to the ledger, converting engine rejections into refusals.

    A :class:`LedgerError` is a rule saying no (unpayable buy, unheld sale), not
    a malformed call, so it surfaces to the agent as a refusal to relay rather
    than an error to retry.
    """
    try:
        return ctx.ledger.append(event)
    except LedgerError as exc:
        raise ToolRefusal(str(exc)) from exc


def _cash_event(event_type: EventType, verb: str) -> Any:
    def handler(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        amount = require_positive(args["amount"], "amount")
        ticker = as_ticker(args["ticker"]) if args.get("ticker") else None
        written = _append(
            ctx,
            LedgerEvent(
                seq=1,  # the store owns sequencing
                event_type=event_type,
                occurred_at=args.get("occurred_at") or ctx.today,
                amount=amount,
                ticker=ticker,
                memo=str(args.get("memo") or ""),
                source=EventSource.USER_CONFIRMED,
                verbatim=args["verbatim"],
            ),
        )
        state = ctx.ledger.state()
        return {
            "recorded": True,
            "seq": written.seq,
            "event_type": event_type.value,
            "amount": money(amount),
            "ticker": ticker,
            "cash_after": money(state.cash),
            "_message": f"{verb} {amount} EGP recorded. Cash is now {state.cash} EGP.",
        }

    return handler


register(Tool(
    name="record_contribution",
    description="Record cash the user added to the portfolio. The amount must be the user's own figure.",
    params=(
        Param("amount", "money", "EGP added, as a string, e.g. \"25000.00\"."),
        Param("occurred_at", "string", "ISO date it happened; defaults to today.", required=False),
        Param("memo", "string", "Optional note.", required=False),
    ),
    handler=_cash_event(EventType.CONTRIBUTION, "Contribution of"),
    write=True,
    requires_verbatim=True,
))

register(Tool(
    name="record_withdrawal",
    description="Record cash the user took out of the portfolio.",
    params=(
        Param("amount", "money", "EGP withdrawn."),
        Param("occurred_at", "string", "ISO date; defaults to today.", required=False),
        Param("memo", "string", "Optional note.", required=False),
    ),
    handler=_cash_event(EventType.WITHDRAWAL, "Withdrawal of"),
    write=True,
    requires_verbatim=True,
))

register(Tool(
    name="record_dividend",
    description="Record a cash dividend received from a holding.",
    params=(
        Param("ticker", "string", "EGX code that paid it."),
        Param("amount", "money", "EGP received, net of any tax withheld."),
        Param("occurred_at", "string", "ISO date; defaults to today.", required=False),
        Param("memo", "string", "Optional note.", required=False),
    ),
    handler=_cash_event(EventType.DIVIDEND_RECEIVED, "Dividend of"),
    write=True,
    requires_verbatim=True,
))

register(Tool(
    name="record_fee",
    description="Record a custody or platform fee charged to the account (not a per-trade commission).",
    params=(
        Param("amount", "money", "EGP charged."),
        Param("occurred_at", "string", "ISO date; defaults to today.", required=False),
        Param("memo", "string", "What the fee was for.", required=False),
    ),
    handler=_cash_event(EventType.FEE_CHARGED, "Fee of"),
    write=True,
    requires_verbatim=True,
))


# ======================================================================
# Writes — trades and corporate actions
# ======================================================================
def _confirm_order_fill(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    order_id = str(args["order_id"]).strip()
    order = ctx.orders.get(order_id)
    if order is None:
        raise ToolRefusal(
            f"no proposal {order_id!r}. Fills are confirmed against a proposal so that the plan and "
            "the ledger stay connected; propose the order first, or record it as an unplanned trade."
        )
    if not order.open:
        raise ToolRefusal(f"proposal {order_id} is already {order.status.value}; it cannot take another fill.")

    shares = require_positive(args["shares"], "shares")
    price = require_positive(args["price"], "price")
    fees = args.get("fees") or ZERO
    taxes = args.get("taxes") or ZERO
    if fees < ZERO or taxes < ZERO:
        raise ToolRefusal("fees and taxes cannot be negative")

    if shares > order.outstanding:
        raise ToolRefusal(
            f"{shares} shares exceeds the {order.outstanding} still outstanding on proposal {order_id}. "
            "If the broker filled more than we proposed, that is a different order — record it as one."
        )

    event_type = EventType.BUY_FILLED if order.side.value == "BUY" else EventType.SELL_FILLED
    written = _append(
        ctx,
        LedgerEvent(
            seq=1,
            event_type=event_type,
            occurred_at=args.get("occurred_at") or ctx.today,
            ticker=order.ticker,
            shares=shares,
            price=price,
            fees=fees,
            taxes=taxes,
            source=EventSource.USER_CONFIRMED,
            verbatim=args["verbatim"],
            decision_id=order.decision_id,
        ),
    )

    partial = shares < order.outstanding
    action = OrderAction.PARTIALLY_FILLED if partial else OrderAction.FILLED
    try:
        updated = ctx.orders.record_action(
            order_id=order_id,
            at=ctx.now,
            action=action,
            filled_quantity=shares,
            fill_price=price,
            verbatim=args["verbatim"],
        )
    except OrderStoreError as exc:  # pragma: no cover - guarded above, kept as a backstop
        raise ToolRefusal(str(exc)) from exc

    state = ctx.ledger.state()
    return {
        "recorded": True,
        "seq": written.seq,
        "order_id": order_id,
        "ticker": order.ticker,
        "side": order.side.value,
        "shares": str(shares),
        "price": money(price),
        "fees": money(fees),
        "status": updated.status.value,
        "outstanding": str(updated.outstanding),
        "cash_after": money(state.cash),
        "shares_held_after": str(state.shares_of(order.ticker)),
        "_message": (
            f"{order.side.value} {shares} {order.ticker} at {price} recorded"
            + (f"; {updated.outstanding} shares still outstanding." if partial else " in full.")
        ),
    }


register(Tool(
    name="confirm_order_fill",
    description=(
        "Record that a proposed order was filled, using the quantities and prices the user reports. "
        "Partial fills are supported: send what actually filled."
    ),
    params=(
        Param("order_id", "string", "The proposal being filled."),
        Param("shares", "money", "Shares that filled, as reported by the user."),
        Param("price", "money", "Price per share actually paid or received."),
        Param("fees", "money", "Commission and platform charges on this fill.", required=False),
        Param("taxes", "money", "Stamp duty or other taxes on this fill.", required=False),
        Param("occurred_at", "string", "ISO trade date; defaults to today.", required=False),
    ),
    handler=_confirm_order_fill,
    write=True,
    requires_verbatim=True,
))


def _cancel_order_proposal(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    order_id = str(args["order_id"]).strip()
    order = ctx.orders.get(order_id)
    if order is None:
        raise ToolRefusal(f"no proposal {order_id!r}.")
    if not order.open:
        raise ToolRefusal(f"proposal {order_id} is already {order.status.value}.")
    action = OrderAction.EXPIRED if bool(args.get("expired")) else OrderAction.CANCELLED
    updated = ctx.orders.record_action(order_id=order_id, at=ctx.now, action=action)
    return {
        "order_id": order_id,
        "status": updated.status.value,
        "_message": f"Proposal {order_id} is {updated.status.value.lower()}. Nothing entered the ledger.",
    }


register(Tool(
    name="cancel_order_proposal",
    description="Close an unfilled proposal. A cancelled or expired proposal never becomes a position.",
    params=(
        Param("order_id", "string", "The proposal to close."),
        Param("expired", "boolean", "True if it lapsed rather than being cancelled.", required=False),
    ),
    handler=_cancel_order_proposal,
    write=True,
))


def _record_corporate_action(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    ticker = as_ticker(args["ticker"])
    ratio = require_positive(args["ratio"], "ratio")
    kind = str(args["kind"]).strip().upper()
    if kind not in {"SPLIT", "BONUS"}:
        raise ToolRefusal("kind must be SPLIT or BONUS")
    event_type = EventType.SHARE_SPLIT if kind == "SPLIT" else EventType.BONUS_ISSUE

    before = ctx.ledger.state().shares_of(ticker)
    if before == ZERO:
        raise ToolRefusal(f"no position in {ticker}; there is nothing for a {kind.lower()} to act on.")

    _append(
        ctx,
        LedgerEvent(
            seq=1,
            event_type=event_type,
            occurred_at=args.get("occurred_at") or ctx.today,
            ticker=ticker,
            ratio=ratio,
            source=EventSource.USER_CONFIRMED,
            verbatim=args["verbatim"],
        ),
    )
    after = ctx.ledger.state().shares_of(ticker)
    return {
        "ticker": ticker,
        "kind": kind,
        "ratio": str(ratio),
        "shares_before": str(before),
        "shares_after": str(after),
        "_message": (
            f"{kind.title()} recorded for {ticker}: {before} -> {after} shares. "
            "Total cost basis is unchanged, so average cost re-derives on its own."
        ),
    }


register(Tool(
    name="record_corporate_action",
    description="Record a share split or bonus issue. Share count scales; total cost basis does not change.",
    params=(
        Param("ticker", "string", "EGX code."),
        Param("kind", "string", "SPLIT or BONUS."),
        Param("ratio", "money", "2 for a 2-for-1 split; 1.1 for a 10% bonus issue."),
        Param("occurred_at", "string", "ISO date; defaults to today.", required=False),
    ),
    handler=_record_corporate_action,
    write=True,
    requires_verbatim=True,
))


def _settle_purification(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    amount = require_positive(args["amount"], "amount")
    state = ctx.ledger.state()
    if amount > state.purification_due:
        raise ToolRefusal(
            f"{amount} EGP exceeds the {state.purification_due} EGP outstanding. Settling more than is "
            "owed would understate future obligations; record the excess as a separate donation."
        )
    _append(
        ctx,
        LedgerEvent(
            seq=1,
            event_type=EventType.PURIFICATION_SETTLED,
            occurred_at=args.get("occurred_at") or ctx.today,
            amount=amount,
            memo=str(args.get("reference") or ""),
            source=EventSource.USER_CONFIRMED,
            verbatim=args["verbatim"],
        ),
    )
    after = ctx.ledger.state()
    return {
        "settled": money(amount),
        "due_after": money(after.purification_due),
        "reference": str(args.get("reference") or ""),
        "_message": f"{amount} EGP purification settled. Outstanding: {after.purification_due} EGP.",
    }


register(Tool(
    name="settle_purification",
    description="Record that impure income was given away. Cannot settle more than is outstanding.",
    params=(
        Param("amount", "money", "EGP given away."),
        Param("reference", "string", "Where it went — receipt, charity name.", required=False),
        Param("occurred_at", "string", "ISO date; defaults to today.", required=False),
    ),
    handler=_settle_purification,
    write=True,
    requires_verbatim=True,
))


def weights_snapshot(ctx: ToolContext) -> dict[str, str]:
    """Current portfolio weights as strings. Used by the plan and review tools."""
    return money_map(actual_weights(ctx.ledger.state(), ctx.prices))
