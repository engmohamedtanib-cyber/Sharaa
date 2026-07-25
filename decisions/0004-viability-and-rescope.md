# 0004 — Viability assessment and re-scope

**Status:** Accepted · **Date:** 2026-07-25
**Question asked:** is this project worth continuing, and if so in what form?

## What was tested, not assumed

| Path | Result |
|---|---|
| `curl https://www.egx.com.eg/...` | **403** — organisation policy denial at the egress proxy |
| `WebFetch` on egx.com.eg | **403** |
| `WebFetch` on an aggregator | **403** |
| `WebFetch` on Wikipedia (control) | **403** — so WebFetch is blocked *environment-wide*, not by EGX |
| `WebSearch` | **works** — metadata and links only, no document retrieval |
| User uploading files into the chat | **works** — this is how the specs arrived |

**Conclusion:** in this sandbox I cannot autonomously download filings. This is an
environment restriction, not a property of the project. The same code would work from a
local runtime with normal network access.

## The finding that changes the plan

**The EGX 33 Shariah Index exists.** Launched June 2024 (base date 2022-01-01), 33
companies drawn from EGX100, each with a Shariah supervisory board, vetted by an
independent Shariah Board against activity, financial-ratio and liquidity criteria, with
single-stock weights capped at 15%.

`ENGINE_SPEC.md` §11 listed "existence and methodology of any EGX Shariah index" as an
open verification item. It is now answered, and it resolves the hardest unsolved problem
in the build: **the universe**.

## Verdict: continue, re-scoped

### Why continue

1. **The expensive part is already built and tested.** 590 tests, 100% branch coverage on
   the money-handling modules. The marginal cost to finish is far below the cost already
   spent. Abandoning now discards a working asset to avoid a smaller remaining effort.
2. **The core value is not replicable by asking Claude ad hoc.** A fresh chat gives a
   different answer every time, with no audit trail and real risk of a hallucinated ratio.
   For a verdict with religious consequence, *reproducibility is the product*. That is
   exactly what a deterministic engine plus an append-only ledger provides and a
   conversation cannot.
3. **Memory is real value.** Why a position was opened, what would falsify it, what the
   purification balance is — none of this survives in a chat.

### Why re-scope, honestly

1. **Screening 715 companies was never achievable** without bulk filing access, and now
   it is unnecessary.
2. **Proactive monitoring is impossible** on a subscription runtime (`decisions/0001`).
3. **The user is not a domain expert** and cannot validate outputs. That raises the cost
   of a wrong answer and argues for a smaller, better-verified universe.

## The re-scope

**Universe:** the EGX 33 Shariah Index constituents, not the whole exchange.

This changes the division of labour in a way that is *better*, not merely smaller:

| Layer | Before | Now |
|---|---|---|
| Universe filter | Our Screens A–E across 715 companies | EGX33 Shariah membership (a qualified Shariah board's verdict) |
| Our Screens A–E | The primary verdict | **Independent verification** of index membership, and headroom measurement |
| Pillars 2–7 | Ranking | Unchanged — the index ranks nothing |
| Decisions, sizing, purification | — | Unchanged — the index provides none of this |

**Why our engine is still necessary, precisely:**
- Index membership is a **periodic, lagging** signal. A company can breach between
  rebalances; our Screen B early-warning (interest income crossing 3% of revenue) catches
  drift the index will not report until its next review.
- The index reports *pass/fail*, never **headroom**. Pillar 1 measures how close to the
  limit a company sits — the difference between "compliant" and "compliant with room".
- The index computes **no purification amount**. That is per-holding and unavoidable.
- The index gives no score, no fair value, no position size, no decision.

**Data acquisition:** filings arrive by (a) the user uploading a PDF, or (b) a local
runtime with network access. Autonomous discovery is deferred, not designed out — the
`Downloader` protocol in `ingestion/acquire.py` already isolates it.

## Consequences for the roadmap

- **Add M0:** record the EGX33 Shariah constituent list in `config/universe.yaml`.
  It must be **obtained from a primary source, not recalled** — inventing 33 tickers
  would be exactly the failure this project exists to prevent.
- **M5 (golden set) shrinks** from "15 filings across the exchange" to "3–5 filings from
  index constituents we actually intend to hold".
- **M7 (routines) is deferred** to `FUTURE_PROPOSALS` P1.
- Everything else stands.

## What would make this project not worth continuing

Stated in advance so the answer is not rationalised later:

- If extraction accuracy on the golden set cannot reach 99% on the 12 critical line items
  after a genuine attempt, the engine cannot be trusted with religious-consequence
  verdicts, and the honest outcome is to use the index membership alone and delete the
  screening layer.
- If the portfolio stays small enough that trade costs dominate (the trade-cost gate will
  say so out loud), the correct advice is a compliant fund, not a self-managed portfolio —
  and the system should say that rather than justify its own existence.
