# 0007 — The trade-cost model is fixed-plus-percentage

**Status:** Accepted · **Date:** 2026-07-26
**Question asked:** the user wants to start with 1 000 EGP. Is that viable? Answering it
required the real fee schedule, and the real schedule did not fit the engine's model.

## What arrived

The user transcribed Thndr's published fee page (evidence
`thndr/fee_schedule/2026-07-26`). `ENGINE_SPEC` §7.3 had left the fee parameters `null`
precisely so they could not be guessed, and this is the source it named.

| Component | Rate | Basis |
|---|---|---|
| Thndr fixed order fee | **2 EGP** | per order |
| Thndr variable order fee | 0.1% | per order |
| EGX | 0.01% | per order |
| MCDR | 0.01% | per order |
| Risk insurance | 0.005% | per order |
| FRA | 0.005%, **min 1 EGP** | per *transaction* |

## The problem: the model could not represent it

The engine computed `commission = max(commission_pct × V, min_fee)` — a purely
proportional model with a floor. Thndr's brokerage fee is **additive**: a flat 2 EGP *plus*
0.1%. There is no assignment of the old four parameters that reproduces it.

Why that mattered rather than being cosmetic: a proportional model makes cost the **same
percentage at every trade size**. A 300 EGP trade looks exactly as economic as a 300 000
EGP one. The error is not symmetric — it **understates the cost of small trades**, which is
the direction that lets the system recommend a trade destroyed by its own execution cost.
For a user starting at 1 000 EGP, that is the only region that matters.

## Decision

Per side::

    fixed_fee_egp
    + commission_pct * V
    + levies_pct     * V
    + max(tax_pct * V, min_fee_egp)

Doubled for the round trip, because the source states plainly that fees apply to buy *and*
sell orders. `fixed_fee_egp` is new; `min_fee_egp` now gates only the regulator component,
which is the only one carrying a minimum.

This is not a change to a rule — §7.3 sketches the shape with an ellipsis and instructs
that fees be verified against Thndr's schedule before any live run. This is that
verification, and the model follows the evidence rather than the reverse.

**The config is self-checking.** The source publishes its own worked example — a 5 000 EGP
order costs 9.25 EGP — and `test_trade_cost_reproduces_the_brokers_own_worked_example`
requires the shipped config to reproduce it to the piastre. Four numbers someone typed in
would not survive that; these do.

## Added: `min_economic_trade_value`

Solving the gate for V answers "how much do I actually need?" with a number instead of a
refusal. Under the real schedule it is **800 EGP**.

It raises rather than returns a number when the proportional components alone breach the
ceiling — that state means no trade of any size is economic, and the honest response is to
say the broker cannot serve the strategy, not to hand back a threshold that cannot be met.

## What this answered

| Trade | Round trip | Cost | Gate |
|---|---|---|---|
| 333 EGP (1 000 split three ways) | 6.83 | **2.05%** | SUPPRESSED |
| 500 EGP | 7.25 | 1.45% | SUPPRESSED |
| **800 EGP** | 8.00 | 1.00% | breakeven |
| 1 000 EGP | 8.50 | 0.85% | ok |
| 10 000 EGP | 31.00 | 0.31% | ok |

So 1 000 EGP as a **single** position is economic. Split across the three positions
`min_holdings` requires, every leg is suppressed — each ~333 EGP order pays the same flat
3 EGP. Three economic positions need 3 × 800 = **2 400 EGP**.

**`min_holdings_capital` was already set to 3 000**, before any fee data existed. The real
schedule puts the true floor at 2 400. The threshold was right, with a 25% margin, and is
left unchanged — it was a conservative judgement that the evidence has now vindicated
rather than a guess that needs correcting.

## Corrected in the process

An earlier message in this session warned the user that a fixed minimum commission "could
exceed 1% on a 1 000 EGP trade". The real flat fee is 2 EGP, and the round trip is 0.85%.
The caution was overstated and the correction was given, because it changes what the user
should do.

Separately, a WebSearch snippet consulted before the real page arrived stated the EGX fee
as "0.12 per thousand" (0.012%). The published figure is 0.01%. The snippet was wrong and
was never written to config — the rule against storing unsourced figures paid for itself
inside one session.

## Known gaps, recorded so they are not mistaken for oversights

- **The FRA fee is per transaction, not per order.** One order filling across several
  counterparties incurs the 1 EGP minimum repeatedly. The engine assumes one transaction
  per order and therefore **understates** cost for fragmented fills — the unsafe direction,
  accepted only because the fill count is unknowable in advance.
- **Caps are not modelled** (5 000 EGP on EGX/MCDR/risk insurance, 250 EGP on FRA). They
  bind only above roughly 50 million EGP of order value.
- **MCDR annual custody** (0.01% of portfolio value each 31 December) is a holding cost,
  not a trade cost, and has no home in the engine yet.
- **Thndr Trader waives 50 commission orders a month.** Not modelled; the unsubscribed
  case is the conservative one.
