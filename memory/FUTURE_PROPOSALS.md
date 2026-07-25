# FUTURE_PROPOSALS.md

Ideas deferred past V1. **Do not implement anything in this file.** The V1 architecture is
frozen (`docs/ARCHITECTURE_V2.md` §13); this is where good ideas wait so they stop causing
churn.

Each entry: what, why it was deferred, what would justify revisiting.

---

## P1 — Scheduled proactive monitoring
**What:** daily filing polls, weekly digests, alerting without the user asking.
**Deferred because:** a subscription runtime only executes while a session is running
(`decisions/0001`). Real autonomy needs either scheduled Claude Code sessions or an API
worker, and the latter costs money the user explicitly wants to avoid.
**Revisit when:** the user wants proactivity badly enough to accept scheduled sessions, or
when a session-scheduling primitive proves reliable over a month of use.

## P2 — Supabase / hosted Postgres
**What:** move storage from git-native JSONL to a managed database.
**Deferred because:** at this scale (one portfolio, hundreds of events, ~30 companies)
JSONL in git is simpler, diffable, free, and needs no credentials (`decisions/0002`).
**Revisit when:** multi-device concurrent writes are needed, or the event log passes
~100k rows and reading becomes slow.

## P3 — Dashboard / web UI
**What:** a visual portfolio view.
**Deferred because:** the product is conversation-first. A dashboard is a place the user
must remember to visit; a conversation comes to them.
**Revisit when:** a read pattern proves genuinely awkward in text (a five-year performance
chart is the likely first case).

## P4 — Telegram / WhatsApp surface
**What:** reach the user on their phone outside Claude.
**Deferred because:** requires a hosted worker and an API key. The agent plane is
surface-agnostic by design, so this costs nothing to defer.

## P5 — DCF valuation
**What:** discounted cash flow as a third fair-value method.
**Deferred because:** `ENGINE_SPEC` §5.4 argues it is usually false precision in a
12%-inflation, volatile-FX environment. Two methods with lower-of is more honest.
**Revisit when:** a holding has genuine multi-year cash-flow visibility (a utility or a
long-contract infrastructure name).

## P6 — Multi-currency / non-EGX holdings
**Deferred because:** the Shariah gate, the denominators, and the fee model are all
EGX/EGP-specific. Adding a market is a V2-sized change, not a feature.

## P7 — Automatic rights-issue handling
**What:** treat a rights issue like a split.
**Deferred because:** a rights issue requires a *cash decision* — it is a proposal, not an
automatic adjustment. Currently routed to the user as a normal plan, which is correct.

## P8 — Tax-lot accounting (FIFO/specific-lot)
**What:** replace weighted-average cost basis with per-lot tracking.
**Deferred because:** Egyptian individual CGT treatment does not currently make lot
selection advantageous, and weighted average matches the existing `positions.avg_cost`.
**Revisit when:** tax law changes, or the user asks about tax-loss harvesting.
