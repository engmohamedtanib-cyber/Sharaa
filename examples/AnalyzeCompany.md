# Recipe — analyse one company

Trigger: "should I buy X?", "is X still halal?", "what do you think of X?"

## Order is not negotiable (R7: the gate comes first)

1. **Gate first.** `run_gate(financials, mcap_avg_12m, cfg)`.
   If `overall_status` is not GREEN/AMBER → **stop**. Do not score, do not rank,
   do not mention valuation. Report the failing screen in plain language.
2. **Only then score.** `score_company(inputs, gate, cfg)` returns `None` for a
   gate-failing or unvalidated company — that `None` is not an error, it is the rule.
3. **Decide.** `decide(ctx, cfg)` — priority order handles vetoes and overrides.
4. **Explain.** Plain language, no pillar codes, no thresholds unless asked.

## The data question you must ask yourself first

Has this company's filing actually been extracted and validated? If `data_status`
is not `VALIDATED`, the honest answer is *"I don't have verified numbers for X"* —
not a score computed from gaps. See `memory/KNOWN_ISSUES.md` §1.

## What a good answer looks like

> COMI passes the Shariah screen comfortably — its debt is about a third of the
> limit and interest income is under 1% of revenue. It scores 87/100, and at
> 52 EGP it's roughly 28% below what the numbers justify.
>
> **Buy 190 at limit 52.10, valid today** — 9,899 EGP of your 15,081 cash.
>
> I'd be wrong if interest income crosses 3% of revenue next quarter, or the
> score drops below 85.

## What a bad answer looks like

Anything containing "approximately", "I estimate", "it's probably around", or a
number you cannot point to a source for.
