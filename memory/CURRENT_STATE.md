# CURRENT_STATE.md

**Updated:** 2026-07-26 · **Suite:** 750 tests green · ruff clean · mypy --strict on 5 layers

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
| `consistency.py` | Cross-artefact gate: dead links, stale counts, bad citations | High — runs in the suite |
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
| `tests/golden/README.md` | the golden-set contract. 1 file so far. |

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

## First real filings processed (2026-07-26)

Telecom Egypt Q1-2026, consolidated + standalone, uploaded by the user. Both registered as
evidence; **PDF bytes are gitignored, metadata is committed** (`decisions/0002`).
First golden file written: `tests/golden/ETEL/2026-Q1/expected.yaml`.

**9 of the 12 critical line items** were read off the document with page-level citations,
and the balance sheet, the loans note and three independent revenue disaggregations all
reconcile. **ETEL resolves to `DATA_INSUFFICIENT`** — the correct outcome, not a failure:

| Blocker | Why |
|---|---|
| Screen B | Interest income is **not separately disclosed**. Note 9 is prose; the P&L shows one undecomposed "Finance income" line. |
| Screens C, D, E | Denominator is `mcap_avg_12m`. No market-data source exists (M5). |

Three findings that change planning, all in `KNOWN_ISSUES` §6–§8: filings are **image scans
with no text layer** (OCR/vision mandatory); a **condensed interim is structurally
insufficient** for the gate, so prefer audited annuals; and **three of five screens are
blocked on price history, not on filings**.

## Execution fees are real now (decisions/0007)

`config/thresholds.yaml` v1.1.0 carries Thndr's published schedule (evidence
`thndr/fee_schedule/2026-07-26`), replacing the deliberate nulls. The cost model became
**fixed-plus-percentage** because the real brokerage fee is 2 EGP **plus** 0.1% — a shape
`max(pct, min)` could not express, and one that made small trades look as economic as large
ones. A test requires the shipped config to reproduce the broker's own worked example
(5 000 EGP → 9.25 EGP per side) exactly.

**Minimum economic trade: 800 EGP** (`engine.portfolio.min_economic_trade_value`). Three
positions therefore need 2 400 EGP; `min_holdings_capital` was already 3 000, set before any
fee data existed, and is left unchanged — vindicated with a 25% margin.

## The consistency gate (`uv run python -m consistency`)

Runs inside the suite. Checks the **seams between** artefacts, which no other test covers:
every documented path resolves, every `decisions/NNNN` citation exists, every `evidence_id`
resolves to a record, ADR and KNOWN_ISSUES numbering is gap-free, and the test count and
thresholds version quoted in prose match reality.

Built because `BRAIN.md` routed to `knowledge/` while that directory did not exist. On its
first run it found a second dead router link in `docs/ARCHITECTURE_V2.md` (it sent readers
to a docs/ copy of FUTURE_PROPOSALS.md that has always lived in `memory/`) plus two stale
test counts. **If it reports a finding, fix the document or the repo — never the check.**

Note the shape of that fix: naming a dead path *inside backticks*, even to describe it,
makes the sentence itself a dead reference. This paragraph was rewritten because the gate
caught exactly that — the check does not care why a link is broken.

`decisions/` is exempt from the path check: an ADR cites where a file was when the decision
was made, and forcing it current would mean editing history (R5).

## What is NOT built

- **No live data.** No EGX filing discovery, no market prices, no CBE macro. Everything
  except the ETEL golden file has been exercised on fixtures only.
- **Golden set: 1 file, no extractor run against it.** It records hand-read values only;
  `ingestion/extract.py` has still never been run on a real document, so extraction
  accuracy remains **unknown**, not "good".
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
