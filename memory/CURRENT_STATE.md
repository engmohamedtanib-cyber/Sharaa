# CURRENT_STATE.md

**Updated:** 2026-07-25 · **Suite:** 590 tests green · ruff clean · mypy --strict on 4 layers

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
| `validation/` | V1–V10 + status derivation + type-level gate | High |
| `ingestion/normalise.py` | Arabic digits, separators, signs, unit scale | High |
| `ingestion/periods.py` | Cumulative→standalone quarters | High |
| `ingestion/extract.py` | Dual-pass prompt contract + strict parsing | Contract only — never run on a real filing |
| `ingestion/acquire.py` | Hash, dedupe, scanned detection | Logic only — never downloaded anything |
| `ingestion/reconcile.py` | Dual-pass agreement, §3.2/§3.3 gates | High |
| `reporting/journal.py` | Decision journal, falsifiability enforced | High |
| `reporting/order_sheet.py` | Limit-only orders (market unrepresentable) | High |
| `store/jsonl_ledger.py` | Git-native append-only ledger persistence | High |

## What is NOT built

- **No live data.** No EGX filing discovery, no market prices, no CBE macro. Everything
  above has been exercised on fixtures only.
- **No golden set.** Zero real filings have been processed. Extraction accuracy is
  therefore **unknown**, not "good". Do not trust extraction until the golden set exists.
- **No investment policy (IPS) module.** Schema is drafted; `engine/policy.py` not written.
- **No tool API / MCP layer.** The conversational surface is not built yet.
- **No routines.** Nothing runs on a schedule. Everything is user-initiated.

## Known truths a future session must not re-litigate

- Architecture is **frozen** for V1 (`docs/ARCHITECTURE_V2.md` §13).
- Runtime is a **Claude subscription**, not the API (`decisions/0001`).
- Storage is **git-native JSONL**, not Supabase, for V1 (`decisions/0002`).
- The portfolio ledger is **event-sourced**; there is no `cash` field anywhere by design
  (`decisions/0003`).

## Portfolio

No live portfolio yet. `memory/portfolio/ledger.jsonl` is created on first event.
