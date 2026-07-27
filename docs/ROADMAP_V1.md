# ROADMAP_V1.md — Implementation plan under the frozen architecture

**Architecture:** frozen (`ARCHITECTURE_V2.md` §13). This file changes as work completes;
the architecture does not.
**Constitution:** `CLAUDE.md` R1–R8, unchanged and binding.

---

## Already complete (do not rebuild)

| Layer | State | Evidence |
|---|---|---|
| `engine/` — screens, scoring, decisions, watchlist, portfolio, purification, valuation | ✅ | 486 tests, determinism test, 97% branch |
| `validation/` — V1–V10, runner, type-level gate | ✅ | pass/fail cases per validator |
| `ingestion/` — normalise, periods, extract contract, acquire, reconcile | ✅ | LLM/network behind protocols |
| `reporting/` — journal, order sheet | ✅ | limit-only unrepresentable-market-order design |
| `config/` — thresholds, line items, prohibited activities | ✅ | versioned |
| `db/schema.sql` — provenance, append-only, validated-only guards | ✅ | constraint tests |

Quality bar for everything below: `ruff` clean, `mypy --strict`, ≥97% branch coverage on
deterministic modules, 100% on new engine modules.

---

## M1 — Portfolio ledger *(no credentials needed — build first)*

**Build:** `engine/ledger.py` (pure fold + order state machine), schema tables
`ledger_events` / `order_proposals`, repo methods, tests.

**Why first:** every conversational capability depends on knowing what the user owns and
holds in cash. "Should I buy today?" is unanswerable without it. It needs nothing external,
so it is pure progress with zero blocked dependencies.

**Success criteria**
- Cash is a *fold over events*, never a stored mutable number.
- Buying with insufficient cash raises; selling unheld shares raises.
- A bonus issue / split adjusts share count and leaves total cost basis intact.
- Order lifecycle rejects every illegal transition (e.g. FILLED → PROPOSED).
- Replaying the same event list twice yields byte-identical state (determinism).
- 100% branch coverage on `engine/ledger.py`.

**Risks:** cost-basis convention ambiguity (resolved: weighted average, matching
`positions.avg_cost`); partial fills (modelled explicitly, not as two orders).

---

## M2 — Investment Policy Statement

**Build:** `engine/policy.py` (typed IPS, validation, versioning rules),
`config/ips_schema.yaml` (shape + defaults), `policy_versions` table,
`policy_version` column on `decisions`.

**Why second:** M3's tools must accept and enforce a mandate from their first call.
Retrofitting policy into a tool API is far more expensive than building it in.

**Success criteria**
- An IPS that would admit a Shariah-gate-failing company is rejected at load (R7).
- Personal exclusions are additive-only, never subtractive.
- Every decision row carries the `policy_version` in force.
- Changing policy creates a new version; history is never edited.

**Risks:** scope creep into "preferences." Anything that does not change a *decision* is
not policy — it is presentation, and belongs in the persona.

---

## M3 — Tool API facade + order confirm-loop

**Build:** `src/tools/` — the ~20 typed tools from `ARCHITECTURE_V2` §10, each a thin
idempotent wrapper over existing deterministic code, each writing `audit_log`. MCP server
entrypoint. The propose → confirm → ledger loop.

**Why third:** this is the seam between reasoning and truth. Once it exists, the agent is
functional against real state even before live market data.

**Success criteria**
- No tool accepts a financial figure originated by the LLM; amounts come from the user
  (confirmed) or the engine.
- Every tool call is logged with arguments and a result digest.
- Every write tool is idempotent under retry.
- A full conversation flow works end to end on seeded data:
  "how much cash?" → "propose a plan" → "confirm fill" → "what do I own?"

**Risks:** tool sprawl and chatty round-trips. Mitigation: tools return *decision-ready*
composites, not raw rows.

---

## ~~M4 — Supabase wiring~~ — REMOVED from V1

Superseded by `decisions/0002`: storage is git-native JSONL. The SQL schemas are
retained as the normative constraint description and the migration target if
`memory/FUTURE_PROPOSALS.md` P2 is ever revisited. Milestones renumber accordingly;
no credentials are needed for any remaining V1 work.

<details><summary>Original M4 (kept for the record)</summary>

**Build:** apply schema, implement `SupabaseRepository` against the existing `Repository`
Protocol, storage bucket, `.env`, migration discipline.

**Why fourth:** the in-memory repo has carried M1–M3 correctly; swapping the backend is a
contained change once the Protocol has been exercised by real callers.

**Success criteria**
- Inserting a `line_item` without `page_no` fails at the *database*, not in Python.
- `UPDATE` on any append-only table is rejected by trigger.
- Same tool calls produce identical results on memory and Supabase backends.
- Point-in-time recovery verified once, deliberately.

**Risks:** silent divergence between backends. Mitigation: run the repository test suite
against both.

</details>

---

## M5 — Live data adapters (`research/`)

**Build:** EGX filing discovery, market data (price/ADTV/DMA/RSI/mcap_avg_12m), CBE macro
(CPI, T-bill, policy rate, USD/EGP) behind the protocols already defined.

**Success criteria**
- Filing discovery is idempotent by file hash across repeated runs.
- Network failure logs, retries with backoff, and *surfaces* — never a silent skip.
- `mcap_avg_12m` is a true trailing average, not spot.

**Risks:** EGX/IR page fragility is the single most likely long-term maintenance burden.
Mitigation: per-company source registry in config, adapters isolated, failures loud.

---

## M6 — Golden set *(blocked on real filings from the user)*

15 filings minimum, ≥5 issuers, ≥3 scanned, ≥2 Arabic-only, ≥1 with Islamic financing,
≥1 with interest buried in other income, ≥1 thousands and ≥1 millions, ≥1 restatement.

**Success criteria:** ≥99% accuracy on the 12 critical line items; a deliberately corrupted
PDF yields `DATA_INSUFFICIENT`, not a guess; regression gate blocks any prompt change that
drops below the bar.

**Risk:** this is the gate on trusting live extraction at all. Until it passes, the agent
may screen and explain but should not be trusted to open positions on freshly extracted
data.

---

## M7 — Routines (proactive autonomy)

Daily filing poll · post-filing pipeline · daily market refresh · weekly digest ·
quarterly review · alert watch. Each idempotent, each captures `now` once, each either
messages the user or deliberately stays quiet.

**Success criteria**
- A simulated new filing drives the full pipeline unattended to a journalled decision.
- Running any routine twice does not double-process (hash/event dedupe).
- The weekly digest sends a "quiet week" message when nothing happened.

---

## M8 — CIO persona

`config/cio_persona.md`, versioned like a threshold file: tone, bilingual behaviour
(AR/EN), the "nothing to do" discipline, confirm-before-record, the plan format, and the
explicit refusal patterns (never invent a number, never override the gate).

**Success criteria:** a naive user completes hire → plan → fill → question → weekly digest
without ever seeing a ratio, a pillar name, or a threshold unless they ask.

---

## Why this order is optimal

Dependency-first, credential-last, and *trust-building last*:

1. **M1–M3 need nothing external.** They convert a correct engine into a stateful agent.
   Any other ordering blocks on inputs we do not control.
2. **M2 before M3** because policy is an *input to* the tool contract; retrofitting a
   mandate into a live API is expensive.
3. **M4 before M5** so live data lands in a durable store rather than a process that dies.
4. **M6 gates M7's trustworthiness**, not its construction — routines can be built and
   tested on fixtures while the golden set is assembled.
5. **M8 last on purpose.** A charming agent with amnesia is a toy; a stateful, honest agent
   with a plain voice is already a CIO. Voice is the cheapest thing to add and the most
   tempting thing to add early.

## Cross-cutting risks

| Risk | Consequence | Control |
|---|---|---|
| Agent states a number no tool produced | Prime Directive violation | Tool-call audit log + test that every figure traces to a tool result |
| Ledger drift vs. reality (user misreports a fill) | Every downstream weight wrong | Confirm-before-record; periodic "does this match your broker?" reconciliation prompt |
| Unconfirmed proposal treated as a holding | Phantom position | Order state machine; proposals expire and are re-decided |
| Extraction accuracy decay | Silent compliance error | Golden-set regression on every prompt change (M6) |
| EGX source pages change | Data stops flowing | Loud failures, per-company source registry |
| Architecture churn | Never shipping | Frozen V1; new ideas → `FUTURE_PROPOSALS.md` |

---

## Current status (updated 2026-07-27)

- **M1: ✅ complete.** `engine/ledger.py` (100% branch) + `store/jsonl_ledger.py` +
  `db/schema_v2_ledger.sql`. Cash is a fold · unpayable buys and unheld sales refused ·
  splits/bonus preserve cost basis · illegal order transitions rejected · replay
  byte-identical.
- **M1.5: ✅ complete.** Knowledge architecture: `BRAIN.md`, `memory/`, `decisions/`,
  `examples/`. A future session resumes from `BRAIN.md` + `memory/` alone.
- **M0 infrastructure: ✅ complete** (the *data* is still missing, deliberately).
  `engine/universe.py` parses the universe and **refuses** an unpopulated one rather
  than returning an empty list. A partial transcription is refused at parse time.
- **M2: ✅ complete.** `engine/policy.py` + `config/ips_schema.yaml` + `config/ips.yaml`.
  Tighten-only against `thresholds.yaml` · `require_shariah_gate` cannot be false (R7) ·
  no whitelist field exists · exclusions additive unless explicitly rescinded · every
  decision records the policy version.
- **M3: ✅ complete.** `src/tools/` — 18 typed tools over the existing engine, an
  append-only audit log, idempotent writes keyed by `request_id`, and a dependency-free
  MCP stdio server. The propose → confirm → ledger loop works end to end on real files.
  No tool accepts a figure the model originated: user-reported writes require `verbatim`.
- **M5: 🟡 structure complete, no live adapter possible here.** `src/research/` — source
  protocols, per-company registry, bounded retry with injected sleep, trailing-average
  market cap (raises rather than averaging a short window), idempotent filing discovery.
  The only adapters that exist are the honest ones: `Blocked*` (raises with the 403
  reason) and `UploadedFilingDiscovery` (a directory of uploaded PDFs).
- **M7: ✅ complete.** `src/routines/` — daily filing poll, daily market refresh, weekly
  digest, quarterly review. One run per routine per day, enforced through the audit log
  so a restart does not re-announce yesterday's news. The quiet-week message is sent, not
  skipped; a source that could not be reached is always reported.
- **M8: ✅ complete.** `config/cio_persona.md`, versioned like a threshold file.
- **M6: blocked on real filings.** See `docs/DATA_REQUEST.md` for the exact request.

Suite: **753 tests**, ruff clean, `mypy --strict` across engine/validation/ingestion/
reporting/store/tools/research/routines.

**Blocked on the user, in priority order:** the EGX33 Shariah constituent list · Thndr's
fee schedule (four numbers) · 2–3 real filing PDFs · their mandate. All four are written
up with links in `docs/DATA_REQUEST.md`. No credentials are needed for anything.
