# CURRENT_STATE.md

**Updated:** 2026-07-27 · **Suite:** 753 tests green · ruff clean · `mypy --strict` on 8 layers

Rewrite this file at the end of every working session. It is the first thing a future
session reads. Keep it short and true.

---

## What is built and trustworthy

| Module | What it does | Confidence |
|---|---|---|
| `engine/shariah.py` | Screens A–E, gate, status bands, breach typing, cure windows, admission test | High — boundary-tested |
| `engine/scoring.py` | 7 pillars / 100 points, gate-first, missing→0 | High — every band tested |
| `engine/decisions.py` | Priority matrix, vetoes, mandatory falsification | High |
| `engine/watchlist.py` | 4 lists, movement rules, re-entry harder than exit | High |
| `engine/portfolio.py` | Target weights, caps, water-fill, trade-cost gate | High |
| `engine/purification.py` | Tathir ratio, carry-forward on loss periods | High |
| `engine/valuation.py` | ERP, justified P/E, peer-relative, lower-of | High |
| `engine/ledger.py` | Event-sourced portfolio state; cash is a fold | High — 100% branch |
| `engine/universe.py` | **NEW** — parses the universe; refuses an unpopulated one | High |
| `engine/policy.py` | **NEW** — IPS: tighten-only, gate not disableable, additive exclusions | High |
| `validation/` | V1–V10 + status derivation + type-level gate | High |
| `ingestion/normalise.py` | Arabic digits, separators, signs, unit scale | High |
| `ingestion/periods.py` | Cumulative→standalone quarters | High |
| `ingestion/extract.py` | Dual-pass prompt contract + strict parsing | Contract only — never run on a real filing |
| `ingestion/acquire.py` | Hash, dedupe, scanned detection | Logic only — never downloaded anything |
| `ingestion/reconcile.py` | Dual-pass agreement, §3.2/§3.3 gates | High |
| `reporting/` | Decision journal (falsifiability enforced), limit-only order sheet | High |
| `store/jsonl_ledger.py` | Git-native append-only ledger persistence | High |
| `store/jsonl_orders.py` | **NEW** — proposal log; status is a fold, illegal transitions refused | High |
| `store/jsonl_decisions.py` | **NEW** — append-only decision journal, content-addressed ids | High |
| `tools/` | **NEW** — 18 typed tools, audited, idempotent writes, MCP stdio server | High — refusals tested |
| `research/` | **NEW** — source protocols, registry, retry, market derivations, discovery | Logic only — no live source exists |
| `routines/` | **NEW** — daily poll, market refresh, weekly digest, quarterly review | High — idempotency tested |
| `config/cio_persona.md` | **NEW** — the conversation contract, versioned like a threshold file | n/a |

## What is NOT built

- **No live data.** No EGX filing discovery, no market prices, no CBE macro. The
  protocols and the orchestration exist; no adapter reaches a real source, because
  nothing in this environment can.
- **No golden set.** Zero real filings processed. Extraction accuracy is **unknown**,
  not "good".
- **No universe.** `config/universe.yaml` still has `constituents: []`. Now, unlike
  before, every consumer *refuses* rather than screening an empty list.
- **Execution fees unset.** `config/thresholds.yaml` has commission/fees as `null`, so
  order sizing refuses loudly instead of guessing. Four numbers from Thndr unblock it.
- **No scheduler.** Routines are written and idempotent; nothing calls them on a clock.

## Environment limits (re-verified 2026-07-27)

- **No outbound HTTP.** `WebFetch` returns 403 for `egx.com.eg`, `investing.com`,
  `mubasher.info` — and for every other host. Environment policy, not those sites.
- **`WebSearch` works** — titles and URLs only, no document retrieval.
- **File upload works.** This is the reliable path for filings.
- Consequence: `docs/DATA_REQUEST.md` is the deliverable that closes this gap — every
  file needed, with links, for the user to collect outside the sandbox.

## Known truths a future session must not re-litigate

- Architecture is **frozen** for V1 (`docs/ARCHITECTURE_V2.md` §13).
- Runtime is a **Claude subscription**, not the API (`decisions/0001`).
- Storage is **git-native JSONL**, not Supabase, for V1 (`decisions/0002`).
- The portfolio ledger is **event-sourced**; there is no `cash` field by design
  (`decisions/0003`).
- The universe is the **EGX 33 Shariah Index** (`decisions/0004`). The index selects the
  universe; Screens A–E independently verify each holding and measure headroom.
- **An empty universe is not "nothing is compliant".** This is now enforced in code, not
  convention — `Universe.require_available()` raises, and every tool relays the reason.
- **Policy may only tighten.** A mandate that would raise a cap, lower the cash floor or
  disable the gate is rejected at load, not warned about.

## Portfolio

No live portfolio yet. `memory/portfolio/ledger.jsonl` is created on the first event.
