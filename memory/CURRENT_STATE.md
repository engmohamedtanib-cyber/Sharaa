# CURRENT_STATE.md

**Updated:** 2026-07-26 · **Suite:** 709 tests green · ruff clean · mypy --strict on 5 layers

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
| `engine/universe.py` | EGX33 constituents, issuer grouping, refuses an unpopulated universe | High — 100% branch |
| `validation/` | V1–V10 + status derivation + type-level gate | High |
| `ingestion/normalise.py` | Arabic digits, separators, signs, unit scale | High |
| `ingestion/periods.py` | Cumulative→standalone quarters | High |
| `ingestion/extract.py` | Dual-pass prompt contract + strict parsing | Contract only — never run on a real filing |
| `ingestion/acquire.py` | Hash, dedupe, scanned detection | Logic only — never downloaded anything |
| `ingestion/reconcile.py` | Dual-pass agreement, §3.2/§3.3 gates | High |
| `research/transcribers/transcribe_index.py` | Constituents export → `config/universe.yaml` | High — round-trips against the archived workbook |
| `store/evidence.py` | Evidence registry; recomputes every hash from bytes | High — 100% branch |
| `reporting/journal.py` | Decision journal, falsifiability enforced | High |
| `reporting/order_sheet.py` | Limit-only orders (market unrepresentable) | High |
| `store/jsonl_ledger.py` | Git-native append-only ledger persistence | High |

## The memory system (decisions/0006)

`BRAIN.md` routed to `knowledge/`, which had never been created — the link was dead.
Fixed, and the substrate around it completed before starting a new module:

| Where | What |
|---|---|
| `memory/evidence/<publisher>/<series>/<as_of>/` | one dir per source item + `metadata.yaml` |
| `store/evidence.py` | resolves an id, **recomputes the hash from the bytes**, raises on mismatch |
| `knowledge/` | verified domain facts, every one carrying a citation |
| `checklists/` | `NEW_EVIDENCE`, `INDEX_REBALANCE`, `SESSION_END` |
| `tests/golden/README.md` | the golden-set contract. Set is still **empty**. |

`tests/test_evidence.py` walks every committed record and re-digests it on every run,
so a hash in a YAML file is a claim that is checked rather than merely stated. There is
deliberately **no `sha256.txt` sidecar** — a hash in two places is a hash that can
disagree with itself.

## The universe is populated (M0 done)

`config/universe.yaml` — **34 listings, 33 issuers**, transcribed by script from evidence
record `egx/shariah_index/2026-04-30` (sha256 `1ad43de…4470c`, weights as of 2026-04-30).
Do not hand-edit it; follow `checklists/INDEX_REBALANCE.md` at the next rebalance.
Full rationale: `decisions/0005`.

Three things about it a future session must not undo:

- **34 listings ≠ 34 companies.** Faisal Islamic Bank is listed in EGP (`FAIT`) and USD
  (`FAITA`). Position and concentration caps apply per `issuer_id`, never per row.
- **Every `sector` is `null`,** because the source export has no sector column. Sector
  drives the sector concentration cap, so a guessed one would silently shape position
  sizing. It stays null until a primary source is opened.
- **Provenance stops at the bytes.** The origin URL was not independently verified (egress
  is blocked here) and `retrieved.from` says so. Upgrade it only against a real fetch.

## What is NOT built

- **No live data.** No EGX filing discovery, no market prices, no CBE macro. Everything
  above has been exercised on fixtures only.
- **No golden set.** Zero real filings have been processed. Extraction accuracy is
  therefore **unknown**, not "good". Do not trust extraction until the golden set exists.
- **No sector classification.** See above — blocks the sector cap in `engine/portfolio.py`
  from being meaningful, though it does not block screening or scoring.
- **No investment policy (IPS) module.** Schema is drafted; `engine/policy.py` not written.
- **No tool API / MCP layer.** The conversational surface is not built yet.
- **No routines.** Nothing runs on a schedule. Everything is user-initiated.

## Environment limits (tested 2026-07-25, not assumed)

- **Cannot download filings from this sandbox.** `curl` to egx.com.eg returns 403 from the
  egress proxy (policy denial). `WebFetch` returns 403 for *every* host including
  Wikipedia, so it is blocked environment-wide, not by EGX.
- **`WebSearch` works** — links and metadata only, no document retrieval.
- **File upload into the chat works.** This is the reliable path for filings, and is how
  the constituent list arrived.

## Known truths a future session must not re-litigate

- Architecture is **frozen** for V1 (`docs/ARCHITECTURE_V2.md` §13).
- Runtime is a **Claude subscription**, not the API (`decisions/0001`).
- Storage is **git-native JSONL**, not Supabase, for V1 (`decisions/0002`).
- The portfolio ledger is **event-sourced**; there is no `cash` field anywhere by design
  (`decisions/0003`).
- The universe is the **EGX 33 Shariah Index**, not the whole exchange (`decisions/0004`).
  The index selects the universe; our Screens A–E independently verify each holding and
  measure headroom, which the index never reports.
- Index weights are stored but are **informational only** — never an engine input
  (`decisions/0005`).
- The project **is** worth continuing, in the re-scoped form. The conditions under which
  it would stop are written down in `decisions/0004` so they cannot be rationalised away
  later.

## Portfolio

No live portfolio yet. `memory/portfolio/ledger.jsonl` is created on first event.

## Unresolved, raised with the user 2026-07-25

The user pasted a 34-name EGX price list alongside the workbook. It is **not** the index:
10 of its names are not constituents (El Sewedy, GB Auto, Ezz Steel, Raya Holding, Abu Qir,
Emaar Misr, Cleopatra, Misr Fertilizers, Fawry, Taaleem) and 10 constituents are absent
from it. Nothing from it was stored — it has no verifiable source and there is no
market-data layer yet (M5). Awaiting the user's word on where it came from.
