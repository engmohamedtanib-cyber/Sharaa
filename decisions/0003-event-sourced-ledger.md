# 0003 — Portfolio state is a fold, never a stored balance

**Status:** Accepted · **Date:** 2026-07-25 · **Implements:** `docs/ARCHITECTURE_V2.md` §6

## Context

The system must know what the user owns and how much cash they have. The obvious design is
a `positions` table and a `cash` column, updated as things happen.

That design fails here for a specific reason: **fills are reported by a human, verbally,
asynchronously.** "Done, bought 190 at 52.10" arrives hours after the trade, sometimes
misremembered, occasionally corrected the next day. A mutable balance updated by such
reports accumulates silent error, and there is no way to tell a correct balance from a
drifted one by looking at it.

## Decision

Portfolio state is **derived by folding an append-only event log**. There is deliberately
**no `cash` field anywhere** in the codebase or the schema.

```
cash, holdings, cost basis, purification due  =  fold(events)
```

Event vocabulary is closed: `CONTRIBUTION`, `WITHDRAWAL`, `DIVIDEND_RECEIVED`,
`BUY_FILLED`, `SELL_FILLED`, `FEE_CHARGED`, `SHARE_SPLIT`, `BONUS_ISSUE`,
`PURIFICATION_ACCRUED`, `PURIFICATION_SETTLED`, `NOTE`. A movement that is not one of these
cannot be recorded, which forces new kinds to be designed rather than smuggled in.

## Key consequences

**The fold refuses rather than absorbs.** A buy that cannot be paid for raises
`InsufficientCashError`; a sale of unheld shares raises `InsufficientSharesError`. A
rejected event leaves no trace, so the log is always internally consistent. A ledger that
silently accepts an impossible movement is worse than one that refuses it.

**Corrections are events, not edits.** A misreported fill is fixed by appending a
correcting event, never by editing history. The mistake stays visible, which is the point.

**Corporate actions are first-class.** EGX issuers split and issue bonus shares routinely.
`SHARE_SPLIT` and `BONUS_ISSUE` scale the share count while leaving **total cost basis
untouched**, so average cost re-derives correctly (a 2-for-1 halves it) with no rounding
applied to money. Omitting this would have held a permanently wrong share count after the
first bonus issue, corrupting every weight, every 6B liquidity check and every performance
figure — invisibly and forever.

**A proposal is not a position.** Orders live in their own state machine
(`PROPOSED → FILLED | PARTIALLY_FILLED | EXPIRED | CANCELLED`). The ledger learns about
shares only when a fill is confirmed. An ignored proposal expires and changes nothing,
which is what makes doing nothing always safe for the user.

**Cost basis is weighted-average**, matching the retained `positions.avg_cost` semantics.
Per-lot accounting was considered and deferred (`FUTURE_PROPOSALS` P8) — Egyptian
individual CGT treatment does not currently reward lot selection.

## Why not the alternatives

- **Mutable balances:** drift, unprovable, and no way to answer "what did I own in March".
- **Double-entry bookkeeping:** correct and more general, but the extra abstraction buys
  nothing for a single-account personal portfolio and costs clarity every time it is read.

## Evidence

`engine/ledger.py` is at 100% branch coverage with 75 tests, including a determinism test
(50 folds, one result) and a five-year narrative test covering hire → buy → dividend →
split → contribute → trim → purify.
