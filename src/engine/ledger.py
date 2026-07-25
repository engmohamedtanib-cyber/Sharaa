"""Event-sourced portfolio ledger (ARCHITECTURE_V2 §6, §12.2).

Portfolio state is a **fold over an append-only event log**, never a mutable row.
Cash is derived, holdings are derived, cost basis is derived. Nothing about the
portfolio is stored as a number that can drift out of agreement with its history.

Why event sourcing here specifically: fills are reported asynchronously by a human
("done, bought 190 at 52.10"), sometimes late, sometimes corrected. A mutable
balance updated by such reports accumulates silent error. A fold cannot: replay
the events and the state is whatever the events say, exactly, every time.

Invariants enforced here, not by convention:
  * cash never goes negative — a buy that cannot be paid for raises
  * shares never go negative — a sell of unheld shares raises
  * event sequence is strictly increasing — out-of-order replay raises
  * splits and bonus issues scale share count and leave total cost basis intact,
    so average cost re-derives correctly (§12.2: missing this silently corrupts
    every weight and liquidity check for the life of the position)

Pure module (``CLAUDE.md`` §5, R2): no I/O, no clock reads, no randomness.
All money is :class:`~decimal.Decimal` in EGP.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal
from enum import StrEnum

from common.decimals import ZERO

# EGX trades whole shares; splits/bonus issues can still produce fractional
# entitlements, which brokers round. We require whole shares on trade events and
# allow the fold to carry fractions only where a ratio legitimately creates them.
_SHARE_QUANTUM = Decimal(1)


class LedgerError(RuntimeError):
    """Base class for ledger rejections."""


class InsufficientCashError(LedgerError):
    """A debit would drive cash negative."""


class InsufficientSharesError(LedgerError):
    """A sale would drive a holding negative."""


class EventOrderError(LedgerError):
    """Events were folded out of sequence."""


class EventShapeError(LedgerError):
    """An event is missing a field its type requires."""


class IllegalTransitionError(LedgerError):
    """An order status change that the lifecycle forbids."""


# ======================================================================
# Events
# ======================================================================
class EventType(StrEnum):
    """The complete ledger vocabulary.

    Deliberately closed: a portfolio movement that is not one of these cannot be
    recorded, which forces new movement kinds to be designed rather than
    smuggled in as a NOTE.
    """

    CONTRIBUTION = "CONTRIBUTION"                    # user adds cash
    WITHDRAWAL = "WITHDRAWAL"                        # user removes cash
    DIVIDEND_RECEIVED = "DIVIDEND_RECEIVED"          # cash in, from a holding
    BUY_FILLED = "BUY_FILLED"                        # confirmed purchase
    SELL_FILLED = "SELL_FILLED"                      # confirmed sale
    FEE_CHARGED = "FEE_CHARGED"                      # custody/platform fee, not per-trade
    SHARE_SPLIT = "SHARE_SPLIT"                      # ratio: 2 = 2-for-1
    BONUS_ISSUE = "BONUS_ISSUE"                      # ratio: 1.1 = 10% bonus shares
    PURIFICATION_ACCRUED = "PURIFICATION_ACCRUED"    # obligation recognised (no cash move)
    PURIFICATION_SETTLED = "PURIFICATION_SETTLED"    # obligation paid (cash out)
    NOTE = "NOTE"                                    # audit-only, no state effect


#: Events that require a positive ``amount``.
_AMOUNT_EVENTS = frozenset({
    EventType.CONTRIBUTION,
    EventType.WITHDRAWAL,
    EventType.DIVIDEND_RECEIVED,
    EventType.FEE_CHARGED,
    EventType.PURIFICATION_ACCRUED,
    EventType.PURIFICATION_SETTLED,
})

#: Events that require ``ticker``, ``shares`` and ``price``.
_TRADE_EVENTS = frozenset({EventType.BUY_FILLED, EventType.SELL_FILLED})

#: Events that require ``ticker`` and ``ratio``.
_RATIO_EVENTS = frozenset({EventType.SHARE_SPLIT, EventType.BONUS_ISSUE})

#: Events that require a ``ticker``.
_TICKER_EVENTS = _TRADE_EVENTS | _RATIO_EVENTS | {
    EventType.DIVIDEND_RECEIVED,
    EventType.PURIFICATION_ACCRUED,
}


class EventSource(StrEnum):
    """Where an event came from. Recorded for audit (R5)."""

    USER_CONFIRMED = "USER_CONFIRMED"   # the user told us, and we read it back
    ENGINE = "ENGINE"                   # deterministic computation (e.g. accrual)
    IMPORT = "IMPORT"                   # historical backfill


@dataclass(frozen=True)
class LedgerEvent:
    """One immutable portfolio movement.

    ``seq`` orders the log. ``occurred_at`` is an ISO date supplied by the caller
    at the boundary — this module never reads a clock (``CLAUDE.md`` §5).
    """

    seq: int
    event_type: EventType
    occurred_at: str
    amount: Decimal | None = None
    ticker: str | None = None
    shares: Decimal | None = None
    price: Decimal | None = None
    fees: Decimal = ZERO
    taxes: Decimal = ZERO
    ratio: Decimal | None = None
    memo: str = ""
    source: EventSource = EventSource.USER_CONFIRMED
    verbatim: str = ""          # what the user actually said, for audit
    decision_id: str | None = None

    def __post_init__(self) -> None:
        if self.seq < 1:
            raise EventShapeError(f"seq must be >= 1 (got {self.seq})")
        if self.fees < ZERO or self.taxes < ZERO:
            raise EventShapeError(f"{self.event_type}: fees and taxes must not be negative")

        if self.event_type in _AMOUNT_EVENTS:
            if self.amount is None:
                raise EventShapeError(f"{self.event_type} requires an amount")
            if self.amount <= ZERO:
                raise EventShapeError(f"{self.event_type}: amount must be positive (got {self.amount})")

        if self.event_type in _TICKER_EVENTS and not (self.ticker or "").strip():
            raise EventShapeError(f"{self.event_type} requires a ticker")

        if self.event_type in _TRADE_EVENTS:
            if self.shares is None or self.price is None:
                raise EventShapeError(f"{self.event_type} requires shares and price")
            if self.shares <= ZERO:
                raise EventShapeError(f"{self.event_type}: shares must be positive")
            if self.shares != self.shares.to_integral_value():
                raise EventShapeError(f"{self.event_type}: shares must be whole (got {self.shares})")
            if self.price <= ZERO:
                raise EventShapeError(f"{self.event_type}: price must be positive")

        if self.event_type in _RATIO_EVENTS:
            if self.ratio is None:
                raise EventShapeError(f"{self.event_type} requires a ratio")
            if self.ratio <= ZERO:
                raise EventShapeError(f"{self.event_type}: ratio must be positive (got {self.ratio})")

    # -- convenience constructors used by the tool layer ---------------
    @property
    def gross_value(self) -> Decimal:
        """shares x price for a trade event; zero otherwise."""
        if self.event_type in _TRADE_EVENTS and self.shares is not None and self.price is not None:
            return self.shares * self.price
        return ZERO

    @property
    def cost_including_charges(self) -> Decimal:
        """Total cash a buy consumes: gross + fees + taxes."""
        return self.gross_value + self.fees + self.taxes

    @property
    def proceeds_after_charges(self) -> Decimal:
        """Net cash a sale produces: gross - fees - taxes."""
        return self.gross_value - self.fees - self.taxes


# ======================================================================
# State
# ======================================================================
@dataclass(frozen=True)
class Holding:
    """A position, with cost basis carried as a total (not an average).

    Storing the total and deriving the average means a split or bonus issue is a
    single change to ``shares`` with no re-computation and no rounding drift.
    """

    ticker: str
    shares: Decimal
    cost_basis: Decimal   # total EGP paid, including fees and taxes

    @property
    def avg_cost(self) -> Decimal | None:
        """Cost per share, or ``None`` for a closed position."""
        if self.shares == ZERO:
            return None
        return self.cost_basis / self.shares

    def market_value(self, price: Decimal) -> Decimal:
        return self.shares * price

    def unrealised_pnl(self, price: Decimal) -> Decimal:
        return self.market_value(price) - self.cost_basis


@dataclass(frozen=True)
class PortfolioState:
    """Everything derivable from the event log. Never persisted as truth."""

    cash: Decimal = ZERO
    holdings: dict[str, Holding] = field(default_factory=dict)
    total_contributed: Decimal = ZERO
    total_withdrawn: Decimal = ZERO
    dividends_received: Decimal = ZERO
    fees_paid: Decimal = ZERO
    realised_pnl: Decimal = ZERO
    purification_accrued: Decimal = ZERO
    purification_settled: Decimal = ZERO
    last_seq: int = 0

    @property
    def purification_due(self) -> Decimal:
        """Outstanding obligation. Accrued but not yet settled (ENGINE_SPEC §8)."""
        return self.purification_accrued - self.purification_settled

    @property
    def open_holdings(self) -> dict[str, Holding]:
        """Holdings with a non-zero share count."""
        return {t: h for t, h in self.holdings.items() if h.shares > ZERO}

    def shares_of(self, ticker: str) -> Decimal:
        h = self.holdings.get(ticker)
        return h.shares if h else ZERO

    def invested_value(self, prices: dict[str, Decimal]) -> Decimal:
        """Market value of open holdings for which a price is supplied.

        Holdings without a price are **excluded** rather than valued at cost —
        a stale or absent price must not masquerade as a current valuation.
        """
        return sum(
            (h.market_value(prices[t]) for t, h in self.open_holdings.items() if t in prices),
            ZERO,
        )

    def total_value(self, prices: dict[str, Decimal]) -> Decimal:
        return self.cash + self.invested_value(prices)

    def unpriced_holdings(self, prices: dict[str, Decimal]) -> list[str]:
        """Open holdings with no supplied price — a caller must handle these."""
        return sorted(t for t in self.open_holdings if t not in prices)


# ======================================================================
# The fold
# ======================================================================
def fold(events: list[LedgerEvent], initial: PortfolioState | None = None) -> PortfolioState:
    """Derive portfolio state from an ordered event log.

    Deterministic: the same event list always yields byte-identical state.
    Raises rather than silently absorbing an impossible movement — a ledger that
    accepts an unpayable buy is worse than one that refuses it.
    """
    state = initial or PortfolioState()
    for event in events:
        state = apply_event(state, event)
    return state


def apply_event(state: PortfolioState, event: LedgerEvent) -> PortfolioState:
    """Apply one event to state, returning a new state."""
    if event.seq <= state.last_seq:
        raise EventOrderError(
            f"event seq {event.seq} is not after last applied seq {state.last_seq}"
        )

    handler = _HANDLERS[event.event_type]
    new_state = handler(state, event)
    return replace(new_state, last_seq=event.seq)


# ---- individual handlers ---------------------------------------------
def _contribution(state: PortfolioState, event: LedgerEvent) -> PortfolioState:
    amount = _amount(event)
    return replace(
        state,
        cash=state.cash + amount,
        total_contributed=state.total_contributed + amount,
    )


def _withdrawal(state: PortfolioState, event: LedgerEvent) -> PortfolioState:
    amount = _amount(event)
    _require_cash(state, amount, "withdrawal")
    return replace(
        state,
        cash=state.cash - amount,
        total_withdrawn=state.total_withdrawn + amount,
    )


def _dividend(state: PortfolioState, event: LedgerEvent) -> PortfolioState:
    amount = _amount(event)
    return replace(
        state,
        cash=state.cash + amount,
        dividends_received=state.dividends_received + amount,
    )


def _fee(state: PortfolioState, event: LedgerEvent) -> PortfolioState:
    amount = _amount(event)
    _require_cash(state, amount, "fee")
    return replace(state, cash=state.cash - amount, fees_paid=state.fees_paid + amount)


def _buy(state: PortfolioState, event: LedgerEvent) -> PortfolioState:
    ticker = _ticker(event)
    cost = event.cost_including_charges
    _require_cash(state, cost, f"buy of {ticker}")

    existing = state.holdings.get(ticker)
    shares = event.shares or ZERO
    if existing is None:
        updated = Holding(ticker=ticker, shares=shares, cost_basis=cost)
    else:
        updated = Holding(
            ticker=ticker,
            shares=existing.shares + shares,
            cost_basis=existing.cost_basis + cost,
        )
    holdings = {**state.holdings, ticker: updated}
    return replace(
        state,
        cash=state.cash - cost,
        holdings=holdings,
        fees_paid=state.fees_paid + event.fees + event.taxes,
    )


def _sell(state: PortfolioState, event: LedgerEvent) -> PortfolioState:
    ticker = _ticker(event)
    shares = event.shares or ZERO
    existing = state.holdings.get(ticker)
    held = existing.shares if existing else ZERO
    if shares > held:
        raise InsufficientSharesError(
            f"cannot sell {shares} {ticker}: only {held} held"
        )
    assert existing is not None  # implied by shares > 0 and shares <= held

    # Weighted-average cost relief, matching positions.avg_cost semantics.
    relieved_basis = existing.cost_basis * (shares / existing.shares)
    proceeds = event.proceeds_after_charges

    updated = Holding(
        ticker=ticker,
        shares=existing.shares - shares,
        cost_basis=existing.cost_basis - relieved_basis,
    )
    holdings = {**state.holdings, ticker: updated}
    return replace(
        state,
        cash=state.cash + proceeds,
        holdings=holdings,
        realised_pnl=state.realised_pnl + (proceeds - relieved_basis),
        fees_paid=state.fees_paid + event.fees + event.taxes,
    )


def _corporate_action(state: PortfolioState, event: LedgerEvent) -> PortfolioState:
    """Split or bonus issue: scale shares, leave total cost basis untouched.

    Leaving ``cost_basis`` alone is the whole point — average cost then re-derives
    correctly (a 2-for-1 split halves it) with no rounding applied to money.
    """
    ticker = _ticker(event)
    existing = state.holdings.get(ticker)
    if existing is None or existing.shares == ZERO:
        # A corporate action on something we do not hold changes nothing. It is
        # recorded for audit but must not fabricate a position.
        return state
    ratio = event.ratio or Decimal(1)
    updated = Holding(
        ticker=ticker,
        shares=existing.shares * ratio,
        cost_basis=existing.cost_basis,
    )
    return replace(state, holdings={**state.holdings, ticker: updated})


def _purification_accrued(state: PortfolioState, event: LedgerEvent) -> PortfolioState:
    # Recognising the obligation does not move cash (ENGINE_SPEC §8).
    return replace(state, purification_accrued=state.purification_accrued + _amount(event))


def _purification_settled(state: PortfolioState, event: LedgerEvent) -> PortfolioState:
    amount = _amount(event)
    _require_cash(state, amount, "purification settlement")
    return replace(
        state,
        cash=state.cash - amount,
        purification_settled=state.purification_settled + amount,
    )


def _note(state: PortfolioState, event: LedgerEvent) -> PortfolioState:
    return state


_HANDLERS = {
    EventType.CONTRIBUTION: _contribution,
    EventType.WITHDRAWAL: _withdrawal,
    EventType.DIVIDEND_RECEIVED: _dividend,
    EventType.FEE_CHARGED: _fee,
    EventType.BUY_FILLED: _buy,
    EventType.SELL_FILLED: _sell,
    EventType.SHARE_SPLIT: _corporate_action,
    EventType.BONUS_ISSUE: _corporate_action,
    EventType.PURIFICATION_ACCRUED: _purification_accrued,
    EventType.PURIFICATION_SETTLED: _purification_settled,
    EventType.NOTE: _note,
}


def _amount(event: LedgerEvent) -> Decimal:
    assert event.amount is not None  # guaranteed by __post_init__
    return event.amount


def _ticker(event: LedgerEvent) -> str:
    assert event.ticker is not None  # guaranteed by __post_init__
    return event.ticker


def _require_cash(state: PortfolioState, amount: Decimal, what: str) -> None:
    if amount > state.cash:
        raise InsufficientCashError(
            f"{what} needs {amount} EGP but only {state.cash} EGP is available"
        )


# ======================================================================
# Order lifecycle (ARCHITECTURE_V2 §6)
# ======================================================================
class OrderStatus(StrEnum):
    PROPOSED = "PROPOSED"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


#: Terminal states cannot be left. A proposal may still fill after a partial fill.
_ALLOWED_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.PROPOSED: frozenset({
        OrderStatus.FILLED,
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.EXPIRED,
        OrderStatus.CANCELLED,
    }),
    OrderStatus.PARTIALLY_FILLED: frozenset({
        OrderStatus.FILLED,
        OrderStatus.EXPIRED,
        OrderStatus.CANCELLED,
    }),
    OrderStatus.FILLED: frozenset(),
    OrderStatus.EXPIRED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
}

#: Statuses that mean "this is not a position and must never be counted as one".
UNFILLED_STATUSES = frozenset({
    OrderStatus.PROPOSED,
    OrderStatus.EXPIRED,
    OrderStatus.CANCELLED,
})


def transition(current: OrderStatus, new: OrderStatus) -> OrderStatus:
    """Validate an order status change, returning the new status.

    A proposal is not a holding: the ledger only learns about shares when a fill
    is confirmed, so an un-actioned proposal expiring is a no-op on state.
    """
    if new not in _ALLOWED_TRANSITIONS[current]:
        raise IllegalTransitionError(f"cannot move order from {current.value} to {new.value}")
    return new


def is_terminal(status: OrderStatus) -> bool:
    return not _ALLOWED_TRANSITIONS[status]


# ======================================================================
# Weights and drift (feeds engine.portfolio rebalancing)
# ======================================================================
def actual_weights(state: PortfolioState, prices: dict[str, Decimal]) -> dict[str, Decimal]:
    """Each holding's share of total portfolio value (including cash).

    Returns an empty mapping when total value is zero. Holdings without a price
    are omitted, and :meth:`PortfolioState.unpriced_holdings` reports them so the
    caller can refuse to act on an incomplete picture rather than act on a
    silently understated denominator.
    """
    total = state.total_value(prices)
    if total == ZERO:
        return {}
    return {
        t: h.market_value(prices[t]) / total
        for t, h in state.open_holdings.items()
        if t in prices
    }


def cash_weight(state: PortfolioState, prices: dict[str, Decimal]) -> Decimal | None:
    """Cash as a share of total value; ``None`` when total value is zero."""
    total = state.total_value(prices)
    if total == ZERO:
        return None
    return state.cash / total
