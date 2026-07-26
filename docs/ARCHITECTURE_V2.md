# ARCHITECTURE_V2.md — The AI Personal CIO

**Status:** Adopted design. Supersedes the implicit "pipeline + CLI" product framing.
**Does not supersede:** `CLAUDE.md`. Every rule there (R1–R8) survives this redesign intact.
**Read order:** this file after `CLAUDE.md`, before `BUILD_SPEC.md` (whose phases it re-sequences).

---

## 1. What product are we actually building?

**Not** a screener, a pipeline, a dashboard, or a Python package. Those are organs, not the animal.

The product is a **fiduciary agent**: an AI the user *hires* to manage their halal EGX portfolio.
The user's entire job is three actions:

1. Say things in plain language ("I added 25,000 EGP", "should I buy today?").
2. Place the orders the agent hands them (Thndr has no API; this step is irreducibly human).
3. Confirm fills ("done, bought 190 at 52.10").

Everything else — discovery, extraction, validation, screening, scoring, deciding, sizing,
purification, monitoring, explaining — is the agent's job, performed proactively, without
being asked.

**The critical reframe:** the deterministic engine we built is not the product.
It is the *instrument panel* the product uses. A pilot is not an altimeter.
The product is the relationship: continuous, conversational, accountable delegation.

### 1.1 One boundary the vision must respect

The vision says "delegate investment management." Full delegation ends at order entry, for
three stacked reasons, any one of which is sufficient:

1. **R6 is constitutional.** No execution path, ever, without explicit revision of `CLAUDE.md`.
2. **No API exists.** Thndr exposes no retail execution API. The constraint is physical.
3. **Regulatory safety.** A tool that *advises its owner* and a service that *executes for
   clients* are different objects under FRA rules. Order entry as a human act keeps this
   firmly a personal tool.

So the honest product promise is: **"You will never think about investing. You will only
ever tap 'confirm' and type what happened."** That is 95% of delegation, and the missing
5% is a feature — it is the human veto, the audit point, and the legal firewall.

---

## 2. The user experience, day one to year five

### Day 1 — Hiring the CIO
The user states, in conversation: capital, monthly contribution (if any), horizon,
and any personal exclusions beyond the Shariah gate. The agent:
- creates the portfolio ledger (cash = initial capital),
- runs the universe screen,
- returns a **first investment plan**: 3–5 names, exact quantities, exact limit prices,
  a one-paragraph plain-language reason per name, and one falsification sentence per name
  ("we sell this if X").
- The user places the orders and replies with fills. The agent records them.

Nothing in this flow shows a ratio, a pillar, or a threshold unless asked.

### Week 2 — First proactive contact
The agent messages first: "COMI filed H1 results. Still compliant, still on thesis.
Nothing to do." **The 'nothing to do' message is a core product feature** — it is how
trust is built. An agent that only speaks when action is needed feels like an alarm;
an agent that also confirms quiet is a steward.

### Month 3 — First intervention
"EAST's interest income crossed 3% of revenue — the early-warning line, not a breach.
No action required, but if next quarter continues the trend it will fail Screen B and
we will exit. Flagging now so nothing surprises you."

### Year 1 — Rhythm
- Weekly one-paragraph digest (or "quiet week").
- Quarterly full review after earnings season: portfolio state, decisions taken,
  decisions proposed, purification amount due, one-tap-confirm order sheet.
- Event-driven interrupts only for: compliance change, veto fired, filing from a held
  company, drawdown beyond a set line, order proposals.

### Year 5 — Institutional memory
The agent can answer: "Why did we sell SWDY in 2027?" with the original decision,
its stated falsification condition, and whether the exit proved right. Every answer
traces to the append-only ledger (R5). The user has a track record, a purification
history for their zakat/charity accounting, and has never read a balance sheet.

**The interface for all of this is a chat thread.** Not a dashboard the user must visit —
a conversation that comes to them. A dashboard may exist later as a *read-only artifact
the agent renders*, never as the primary surface.

---

## 3. Architecture options, evaluated honestly

| Option | Verdict | Why |
|---|---|---|
| **Claude Projects / plain chat + docs** | ❌ Reject as substrate | No structured persistent state, no scheduled execution, no determinism. The CIO's memory cannot be a context window; portfolio state in prose corrupts. Fine as a *reading room*, useless as a *system of record*. |
| **Claude Desktop + local MCP + local DB** | ❌ Reject as primary | Breaks the two most important requirements: proactivity (a laptop that is off cannot monitor filings) and reachability (the user's real device is a phone). Local-first optimizes for the wrong constraint — this data is not privacy-critical (public filings + one small portfolio); availability matters more than locality. |
| **Fully local automation (cron on user machine)** | ❌ Reject | Same availability problem, plus silent-failure risk: a dead cron job on a personal machine is the "company stops filing" failure mode applied to ourselves. |
| **Pure SaaS web app with embedded chat** | ❌ Reject for now | Maximum build cost, and it front-loads the regulatory question (a hosted multi-user service is an advisory product). Correct only if this later becomes a company. |
| **Fine-tuned / agentic "LLM decides trades"** | ❌ Reject permanently | Violates R2 and the Prime Directive. An LLM that outputs "buy" is confidently wrong by construction — unverifiable, non-reproducible, un-auditable. |
| **Agent-on-Tools, cloud state, chat-native (hybrid)** | ✅ **Adopt** | Detailed below. |

### 3.1 The adopted architecture: **Agent-on-Tools**

Three planes, strictly separated:

```
┌─────────────────────────────────────────────────────────────────┐
│  CONVERSATION PLANE                              [LLM, stateless]│
│  The CIO persona. Understands intent, calls tools, explains      │
│  results, drafts prose. Holds NO state, computes NO numbers.     │
│  Surface: Claude (custom connector/MCP) today; any chat surface  │
│  (Telegram/WhatsApp/app) later — the plane is surface-agnostic.  │
├─────────────────────────────────────────────────────────────────┤
│  AUTONOMY PLANE                        [scheduled headless runs] │
│  Cloud-scheduled routines (daily poll, post-filing pipeline,     │
│  weekly digest, quarterly review). Each run = the same agent     │
│  with the same tools, started by a clock or an event instead of  │
│  the user. Output = ledger writes + a message to the user.       │
├─────────────────────────────────────────────────────────────────┤
│  TRUTH PLANE                     [deterministic code + Postgres] │
│  Everything already built: engine/, validation/, ingestion/      │
│  contracts, reporting/. Exposed as a typed TOOL API (MCP server).│
│  Plus the system of record: Supabase Postgres with the existing  │
│  schema, extended with a portfolio ledger and an event log.      │
│  Append-only. The ONLY place state lives.                        │
└─────────────────────────────────────────────────────────────────┘
```

**Why this is superior to every alternative:**

1. **It makes the LLM safe by making it stateless and numberless.** The conversation plane
   can be wrong in *phrasing* but never in *fact*: every number it utters came out of a
   deterministic tool, and every action it takes is a tool call that hits the schema's
   constraints (provenance NOT NULL, append-only triggers, validated-only guards). The
   Prime Directive is enforced by topology, not by prompt.
2. **Proactivity without a running app.** The autonomy plane is just the same agent woken
   by a scheduler in the cloud. No laptop, no daemon on the user's machine, no app to open.
3. **The interface is replaceable; the system is not.** Chat surfaces will change many times
   in five years. The tool API and the ledger will not. Investing engineering effort in the
   truth plane and treating the surface as a thin client is the only five-year-safe bet.
4. **It is exactly what we already built, plus two missing organs.** The redesign costs
   almost nothing in rework — see §10. This is evidence *for* the design, not convenience:
   the constitution forced the right shape before we knew the product.

---

## 4. Division of labor — permanent law

### Deterministic forever (the truth plane; no LLM, no exceptions)
- Screens A–E, gate, breach typing, cure windows, admission test
- All scoring, all bands, all thresholds (config-versioned)
- Decision matrix, vetoes, watchlist movement
- Position sizing, constraint caps, rebalancing math, trade-cost gate
- Purification arithmetic
- Validators V1–V10, dual-extraction reconciliation
- Number normalisation (digits, scale, signs)
- **The portfolio ledger state machine** (new): cash arithmetic, position updates,
  order lifecycle transitions. Money math is never LLM output.

### LLM territory (conversation + autonomy planes)
- Intent understanding ("I got dividends" → `record_dividend` tool call with amount confirmed)
- Document reading in `ingestion/` (already fenced by dual-pass + validation)
- **Explanation**: turning engine output into plain language, in the user's language
  (Arabic/English), at the user's level
- **Prioritization of attention**: which of this week's events deserve the top of the digest
- **Clarifying dialogue**: exception-queue items become plain questions
  ("EAST's report shows a borrowing I can't classify as Islamic or conventional —
  it's excluded until resolved; here's the page, does your broker note say murabaha?")
- Drafting falsification prose *around* engine-supplied metrics (the metric and threshold
  come from the rule that fired; the LLM only words it)

### The line, stated once
**The LLM chooses words and sequences tool calls. It never chooses numbers, verdicts,
or trades.** If a future feature requires the LLM to originate a number that gets stored
or acted on, the feature is wrong.

---

## 5. Automation map

| Routine | Trigger | What it does | Speaks to user? |
|---|---|---|---|
| Filing poll | Daily in reporting windows (May/Aug/Nov/Mar–Apr), weekly otherwise | Discover new filings for universe companies | Only if a held/watched company filed |
| Post-filing pipeline | Event: new filing for held/watched company | Acquire → extract → validate → screen → score → decide → journal | Always: result or "still on thesis" |
| Market refresh | Daily close | Prices, ADTV, mcap_avg_12m, DMAs, RSI | No (feeds other routines) |
| Macro refresh | Monthly + CBE MPC dates | CPI, T-bill, policy rate, USD/EGP | Only if it changes a live decision |
| Weekly digest | Weekly, fixed time | Summarize event log; explicitly say "quiet week" if quiet | Always |
| Quarterly review | After earnings season completes | Full cycle: re-screen, re-score, re-decide, purification accrual, order proposals | Always, as the flagship report |
| Drawdown/alert watch | Daily close | Compare against falsification conditions and alert lines | Only on trip |
| Golden-set regression | On any extraction-prompt change | Block deployment if <99% on critical items | No (developer-facing) |

Every routine is **idempotent** (file-hash keys, event-log dedupe) and every routine that
fails **says so** — a silent skip is the failure mode BUILD_SPEC §5 already names.

---

## 6. State: the portfolio ledger (the biggest gap in the current build)

Current schema has `positions` and `transactions` — necessary but not sufficient. The CIO
needs a **full event-sourced ledger** where portfolio state is a *fold over events*, never
a mutable row:

```
ledger_events (append-only):
  CONTRIBUTION(amount)            ← "I added 25,000 EGP"
  DIVIDEND_RECEIVED(company, amount)
  ORDER_PROPOSED(order details, decision_id)     ← agent output
  ORDER_CONFIRMED_FILLED(order_id, actual qty, actual price, fees)  ← user report
  ORDER_EXPIRED / ORDER_CANCELLED(order_id)
  PURIFICATION_ACCRUED(company, amount) / PURIFICATION_SETTLED(amount, ref)
  NOTE(user statement worth remembering, verbatim)
```

Rules:
- **Cash is derived, never stored as a mutable number.** `cash = fold(events)`. This makes
  "I added 25,000" trivially safe: it's one event, and every balance everywhere updates.
- **Order lifecycle is a state machine**: PROPOSED → (FILLED | PARTIAL | EXPIRED | CANCELLED).
  The agent may not treat a proposed order as held. Unconfirmed proposals expire and are
  re-decided — the market moved.
- **User utterances that carry money are confirmed before recording.** "You received
  dividends of 312.50 EGP from ABUK — recording that. Correct?" The LLM parses; the tool
  records only after confirmation; the event stores the verbatim utterance for audit.
- Same store as everything else: Supabase Postgres, same append-only trigger pattern,
  same reconstruction guarantee (R5).

Conversation history is **not** state. Any fact worth keeping is either a ledger event or
a decision row. A new conversation with zero context must be able to serve the user fully
from the truth plane alone. (Test: "wipe the chat, ask 'what do I own and why' — the answer
must be complete.")

---

## 7. Recommendations → executable plans (Q11 answered)

The agent produces **complete executable plans, never vague recommendations.**

A recommendation is "consider adding to ABUK." A plan is:

> **Buy 190 ABUK, limit 52.10, valid today.** Uses 9,899 EGP of your 12,400 cash.
> Why: score 87, trading 28% under fair value, position currently 4% under target.
> Wrong if: it fills and the gap was gone (re-check at fill), or score drops below 85
> next review. Reply "done" with your fill, or "skip".

This is non-negotiable for the product because the user *cannot evaluate a vague
recommendation* — that's the whole premise. Vagueness delegates the hard part
(sizing, pricing, timing) back to the person who hired us to do it. The engine already
computes everything a complete plan needs; the order sheet already renders it; the only
missing piece is the confirm-loop into the ledger.

---

## 8. The autonomy contract (Q12 answered)

Three tiers, fixed:

**Tier 1 — Autonomous, silent or notifying:** discovery, acquisition, extraction,
validation, screening, scoring, watchlist movement, purification accrual, journal
writing, digest writing, alerting. No permission needed, ever. (This is BUILD_SPEC §4
unchanged.)

**Tier 2 — Autonomous decision, human execution:** every trade. The agent decides
*fully* (name, side, quantity, price, validity) and hands over a finished plan. The
human contributes exactly two things: placing it and reporting the fill. The human may
veto by ignoring it — an expired proposal is safe by construction.

**Tier 3 — Never autonomous, never delegated to the LLM:**
- executing or transmitting orders (R6),
- estimating/filling any financial figure (R3),
- overriding the Shariah gate or any veto (R7 — not even the *user* can make the agent
  score a gate-failing company; they can of course trade against advice, and the agent
  records it as a user-initiated event, flagged non-compliant),
- changing thresholds (config change with changelog, a deliberate human act, R4).

This is more conservative than "how much autonomy *can* we give it" — deliberately.
Trust compounds from a small set of never-broken promises.

---

## 9. Assumptions in the current implementation that must change

1. **"The CLI is the interface."** → The CLI is an ops/debug tool. The interface is
   conversation. *(Demote, keep.)*
2. **"The quarterly review is the heartbeat."** → The heartbeat is the daily/weekly
   autonomy loop; the quarterly review is one (flagship) routine among several.
   *(Re-sequence Phase 6 earlier — the scheduler is core product, not "hardening.")*
3. **"Reports are files."** → Reports are messages. The renderer's output type is
   "content for a chat turn + optional artifact," not a path on disk. *(Adapt.)*
4. **"The exception queue is a table a technician reads."** → It is a source of
   plain-language questions the agent asks, each carrying its safe default ("it stays
   excluded unless you can resolve this"). *(Wrap, keep semantics.)*
5. **"Portfolio state = positions + transactions."** → Full event-sourced ledger with
   cash, contributions, dividends, order lifecycle, purification settlement. *(Extend
   schema — the single biggest new build.)*
6. **"`EGX_STORE_BACKEND=memory` is a reasonable default."** → For tests only. The
   product default is Supabase; an agent whose memory resets is not a CIO. *(Flip default
   at deployment; implement `SupabaseRepository`.)*
7. **"The user reads the order sheet."** → The user *confirms* orders one at a time in
   chat, and fills flow back into the ledger. *(Build the confirm-loop.)*
8. **"One shot per question."** (implicit in CLI design) → Conversations continue; tool
   calls must be cheap, composable, and safe to repeat. The tool API must be designed for
   an agent caller: small, typed, idempotent operations. *(New facade layer.)*

**What explicitly does NOT change:** every rule in `CLAUDE.md`; the schema's constraint
philosophy; the engine/validation/ingestion/reporting code and its tests; the exclusion-
over-estimation posture; the golden-set protocol; English code / bilingual output.

---

## 10. Revised master architecture (component level)

```
                    ┌──────────────────────────────┐
   user (phone) ───▶│ CHAT SURFACE (Claude today;   │
                    │ replaceable thin client)      │
                    └──────────────┬───────────────┘
                                   │ natural language
                    ┌──────────────▼───────────────┐
                    │ CIO AGENT (LLM, stateless)    │
                    │ persona + tool orchestration  │
                    └──────┬────────────────┬──────┘
        scheduled wake     │ tool calls      │ messages out
   ┌───────────────────────▼──┐   ┌──────────▼─────────────────┐
   │ ROUTINES (cloud cron)     │   │ TOOL API (MCP server)      │
   │ poll / pipeline / digest  │──▶│ typed, idempotent, audited │
   │ review / alerts           │   └──────────┬─────────────────┘
   └───────────────────────────┘              │
              ┌────────────────────────────────▼─────────────────┐
              │ DETERMINISTIC CORE (already built)                │
              │ engine/  validation/  ingestion/  reporting/      │
              ├───────────────────────────────────────────────────┤
              │ SUPABASE POSTGRES (system of record)              │
              │ existing schema + ledger_events + event_log       │
              │ append-only, provenance NOT NULL, guards          │
              ├───────────────────────────────────────────────────┤
              │ EXTERNAL: EGX/IR filings · market data · CBE macro│
              │ Anthropic API (extraction only) · Storage (PDFs)  │
              └───────────────────────────────────────────────────┘
```

Tool API surface (first cut, ~20 tools, all thin wrappers over existing code):

```
# read                                # write (all append-only)
get_portfolio()                       record_contribution(amount)
get_position(ticker)                  record_dividend(ticker, amount)
get_compliance_status(ticker?)        confirm_order_fill(order_id, qty, price, fees)
get_score_breakdown(ticker)           cancel_order_proposal(order_id)
get_watchlists()                      resolve_exception(filing_id, resolution, reasoning)
get_exception_queue()                 settle_purification(amount, ref)
get_purification_balance()
get_decision_history(ticker?)         # orchestration (routines + on-demand)
get_event_digest(since)               run_screening(ticker | universe)
explain_decision(decision_id)         run_full_review()
                                      propose_investment_plan(cash?)
                                      check_filings_now()
```

Every write tool: validates via the existing constraint layer, logs to `audit_log`,
and is idempotent under retry. No tool accepts a financial figure the LLM computed —
amounts come from the user (confirmed) or from the engine.

---

## 11. Roadmap for the next implementation sessions

### KEEP EXACTLY AS IMPLEMENTED (do not touch)
- `engine/` — all seven modules, all tests. It is the truth plane's core.
- `validation/` — V1–V10, runner, type-level gate.
- `ingestion/` — normalise, periods, extract contract, acquire, reconcile.
- `reporting/journal.py`, `reporting/order_sheet.py` — semantics unchanged; output
  routing changes later, code doesn't.
- `db/schema.sql` constraint philosophy and all existing tables.
- `config/` — all four files.
- The entire test suite (486 green).

### REDESIGN / EXTEND
1. **Schema**: add `ledger_events` (event-sourced, append-only trigger) and
   `order_proposals` (state machine: PROPOSED/FILLED/PARTIAL/EXPIRED/CANCELLED).
   Cash and holdings become folds over events; add `v_portfolio_state` view.
2. **`db/repo.py`**: implement `SupabaseRepository` against the same Protocol; the
   in-memory repo remains the test double. Product default = supabase.
3. **Reporting output types**: return structured content objects (message + optional
   artifact), not printed strings/paths.

### DELETE
- Nothing. (`cli.py` is demoted to ops tool, not deleted — it is the fastest way to
  debug the truth plane and it exercises the same code paths.)

### POSTPONE (deliberately, with reasons)
- **Dashboard (Phase 7)**: chat-first product; build only when conversation proves
  insufficient for some read pattern.
- **Multi-user / SaaS anything**: changes the regulatory object; personal tool first.
- **WhatsApp/Telegram surface**: nice, later; the agent plane is surface-agnostic so
  this costs nothing to defer.
- **DCF valuation method**: ENGINE_SPEC already calls it usually-false-precision here.

### BUILD NEXT (priority order)
1. **Portfolio ledger** (schema + pure fold functions in `engine/ledger.py` +
   repo methods + tests). The single biggest product gap; everything conversational
   depends on it. *Deterministic module — 100% branch coverage bar applies.*
2. **Tool API facade** (`src/tools/`): the ~20 typed tools above, wrapping existing
   code; MCP server entrypoint. Includes the order confirm-loop.
3. **Supabase wiring**: apply schema, implement repository, `.env` flip, storage bucket.
4. **Live data adapters** (`research/`): EGX filing discovery, market data, CBE macro —
   behind the protocols already defined in `ingestion/acquire.py`.
5. **Golden set**: 15 real filings, hand-verified (needs real PDFs — the one input the
   user must supply). Gates any live extraction.
6. **Routines**: daily poll, post-filing pipeline, weekly digest, quarterly review —
   as scheduled headless agent runs writing to the ledger and messaging the user.
7. **CIO persona + conversation contract**: system prompt for the agent plane —
   tone, bilingual behavior, the "nothing to do" discipline, confirm-before-record,
   plan format. This is a *prompt artifact in the repo* (`config/cio_persona.md`),
   versioned like a threshold file.

Sequencing rationale: 1–3 make the agent *stateful and honest*; 4–5 make it *live*;
6 makes it *proactive*; 7 makes it *pleasant*. In that order — a charming agent with
amnesia is a toy; a stateful agent with a dry voice is already a CIO.

---

## 12. Final review amendments (added at architecture freeze)

The pre-implementation gate review found the design complete against every stated
criterion except two. Both are added here; nothing else changed.

### 12.1 MISSING LAYER — the Investment Policy Statement (IPS)

A real CIO does not operate on taste. It operates under a **mandate**, and every
decision is judged against the mandate *in force at the time it was made*. The
architecture had the user's preferences existing only as Day-1 conversation, which means
they would live in a context window — exactly the failure `CLAUDE.md` R5 forbids for
anything that shapes a decision.

**The IPS is a first-class, versioned, append-only object**, governed like
`config/thresholds.yaml` (R4) and referenced like `threshold_version` (R5):

```yaml
policy_version: "1.0.0"          # bumped on any change; never edited in place
effective_from: "2026-07-25"
objective: LONG_TERM_GROWTH       # LONG_TERM_GROWTH | INCOME | CAPITAL_PRESERVATION
horizon_years: 10
base_currency: EGP
contributions:
  amount: 5000
  cadence: MONTHLY                # MONTHLY | IRREGULAR | NONE
liquidity_needs:
  reserve_egp: 0                  # money that must never be invested
  expected_withdrawal: null
risk:
  max_drawdown_tolerance: 0.30    # informational; triggers a conversation, not a trade
  concentration_comfort: STANDARD # maps to which portfolio caps apply
exclusions:                       # PERSONAL exclusions, layered ON TOP of the Shariah gate
  sectors: []
  tickers: []
purification:
  basis: DIVIDENDS
  settlement_cadence: ANNUAL
review:
  cadence: QUARTERLY
  digest: WEEKLY
```

Rules, binding:
- **The IPS never loosens the Shariah gate.** It may only add exclusions. An IPS that
  attempted to admit a gate-failing company is rejected at load (R7).
- **Every `decisions` row stores `policy_version` alongside `threshold_version`.**
  Two years later you must be able to say "we bought this under the policy you had then."
- **The agent may propose an IPS change; only the user adopts it**, and adoption writes
  a new version with the user's stated reason. Policy drift is thereby visible.
- The IPS lives in the database (not a YAML file) because the user edits it by talking,
  not by committing — but it obeys the same versioning discipline. `config/ips_schema.yaml`
  holds the *shape* and defaults; the *instance* is a ledger-adjacent table.

### 12.2 MISSING BEHAVIOUR — corporate actions in the ledger

EGX issuers do bonus issues and splits routinely. A ledger that models only buys, sells
and cash will silently hold a wrong share count after the first bonus issue, and every
weight, every 6B liquidity check and every performance figure downstream inherits the
error. Because the ledger is a fold, the corruption is permanent and invisible.

The ledger event vocabulary therefore includes `SHARE_SPLIT` and `BONUS_ISSUE`
(share count scales by ratio, total cost basis unchanged, so average cost re-derives
correctly), and `RIGHTS_ISSUE` is deliberately **not** auto-handled — it requires a cash
decision and is routed to the user as a normal proposal.

### 12.3 CLARIFICATION — auditing the reasoning plane

R5 makes the *data* auditable. For an LLM-fronted system that is not sufficient: you must
also be able to prove, after the fact, that no number the agent said was invented. Every
tool call made by the conversation or autonomy plane is logged to `audit_log` with its
arguments, its result digest, and the run that made it. Consequence: any figure in any
message traces to a tool result, and a figure that traces to nothing is a defect with a
test that can catch it.

### 12.4 CLARIFICATION — clock authority

`engine/` stays pure and clock-free (`CLAUDE.md` §5). Every routine captures `now` **once**
at its boundary, records it on the run, and passes it inward as data. Replaying a run with
its recorded timestamp must reproduce its output exactly.

---

## 13. ARCHITECTURE FROZEN — Version 1

**As of this amendment, the V1 architecture is frozen.** No further structural change will
be made during V1 implementation.

Frozen means:
- The three planes (conversation / autonomy / truth) and their separation are fixed.
- The tool-API-as-only-write-path is fixed.
- The deterministic/LLM division in §4 is fixed.
- The autonomy tiers in §8 are fixed.
- The ledger, IPS, and audit obligations above are in scope for V1.

New ideas discovered during implementation are written to `memory/FUTURE_PROPOSALS.md`
and considered for V2. They do not modify this document. The only admissible reason to
reopen V1 architecture is a discovery that makes a frozen element *impossible or unsafe*,
not merely improvable.

---

## 14. Verdict in one paragraph

The current architecture survives the review — not because it was the product, but
because it was accidentally the correct *foundation* for one: a deterministic, auditable,
constraint-enforced truth plane is precisely what makes an LLM-fronted fiduciary agent
safe to build. What changes is everything around it: the product is a conversational,
proactive, cloud-resident CIO agent; the CLI becomes a maintenance hatch; the quarterly
report becomes one routine among a weekly rhythm of contact; and the one genuinely new
organ is the event-sourced portfolio ledger, without which the agent cannot be trusted
to know what the user owns. The LLM chooses words and sequences tool calls; it never
chooses numbers, verdicts, or trades. Full delegation ends at order entry — by
constitution, by physics, and by law — and that residual human act is the product's
safety architecture, not its limitation.
