# BUILD_SPEC.md — EGX Shariah Engine

## 1. System Overview

```
┌──────────────────────────────────────────────────────────────────┐
│  STAGE 1  UNIVERSE DISCOVERY                       [autonomous]  │
│  Fetch EGX listed companies. Maintain company registry.          │
├──────────────────────────────────────────────────────────────────┤
│  STAGE 2  FILING DISCOVERY                         [autonomous]  │
│  Poll EGX disclosures + company IR pages for new statements.     │
├──────────────────────────────────────────────────────────────────┤
│  STAGE 3  ACQUISITION                              [autonomous]  │
│  Download PDF, hash, archive to storage, detect scanned vs text. │
├──────────────────────────────────────────────────────────────────┤
│  STAGE 4  EXTRACTION (dual, independent)           [autonomous]  │
│  Two independent passes → candidate line items with page refs.   │
├──────────────────────────────────────────────────────────────────┤
│  STAGE 5  VALIDATION (deterministic)               [autonomous]  │
│  9 arithmetic + continuity checks. Fail → DATA_INSUFFICIENT.     │
├──────────────────────────────────────────────────────────────────┤
│  STAGE 6  SHARIAH GATE                             [autonomous]  │
│  Screens A–E. Traffic light. Breach typing. Cure tracking.       │
├──────────────────────────────────────────────────────────────────┤
│  STAGE 7  SCORING                                  [autonomous]  │
│  7 pillars, 100 points. Only for gate-passing companies.         │
├──────────────────────────────────────────────────────────────────┤
│  STAGE 8  DECISION + REPORTING                     [autonomous]  │
│  Decision matrix, watchlists, purification, quarterly report.    │
├──────────────────────────────────────────────────────────────────┤
│  STAGE 9  EXECUTION                                    [manual]  │
│  Order sheet generated. Human places orders in Thndr.            │
└──────────────────────────────────────────────────────────────────┘
```

**Stage 9 is manual because Thndr exposes no retail execution API.** This is a platform constraint, not a design choice. If that changes, `R6` in `CLAUDE.md` still forbids automated execution and would need explicit revision.

## 2. Repository Layout

```
egx-shariah-engine/
├── CLAUDE.md
├── pyproject.toml
├── config/
│   ├── thresholds.yaml          # all numeric thresholds, versioned
│   ├── line_items.yaml          # extraction dictionary (AR/EN synonyms)
│   ├── sources.yaml             # per-company filing source registry
│   └── prohibited_activities.yaml
├── docs/                        # this package
├── src/
│   ├── research/
│   │   ├── universe.py          # EGX company list discovery
│   │   ├── filings.py           # new filing detection
│   │   ├── macro.py             # CBE rate, CPI, T-bill, USD/EGP
│   │   └── market.py            # price, market cap, ADTV
│   ├── ingestion/
│   │   ├── acquire.py           # download, hash, archive
│   │   ├── ocr.py               # scanned document handling
│   │   ├── extract.py           # dual LLM extraction
│   │   └── normalise.py         # Arabic digits, unit scale, signs
│   ├── validation/
│   │   ├── arithmetic.py        # V1–V4
│   │   ├── continuity.py        # V5–V6
│   │   ├── plausibility.py      # V7–V8
│   │   └── agreement.py         # V9 dual-extraction reconciliation
│   ├── engine/
│   │   ├── shariah.py           # Screens A–E, gate, breach typing
│   │   ├── scoring.py           # 7 pillars
│   │   ├── valuation.py         # fair value, ERP
│   │   ├── decisions.py         # decision matrix, vetoes
│   │   ├── watchlist.py         # 4 lists, movement rules
│   │   ├── portfolio.py         # sizing, rebalancing bands
│   │   └── purification.py      # tathir ledger
│   ├── reporting/
│   │   ├── quarterly.py         # Step 17 template renderer
│   │   ├── journal.py           # decision journal entries
│   │   └── narrative.py         # LLM prose — never touches numbers
│   ├── db/
│   │   ├── models.py
│   │   └── repo.py
│   └── cli.py
├── tests/
│   ├── golden/                  # hand-verified filings + expected values
│   ├── test_engine_*.py         # 100% branch coverage required
│   └── test_validation_*.py
└── scripts/
    └── run_review.py            # full quarterly cycle entrypoint
```

## 3. Build Phases

### Phase 0 — Scaffold
- Repo, `pyproject.toml`, `ruff` + `mypy` config, pre-commit hooks
- Supabase connection, environment handling via `.env` (never committed)
- `config/thresholds.yaml` populated from `ENGINE_SPEC.md` §2 and §4
- CLI skeleton with `--dry-run` on every command

**Acceptance:** `uv run cli --help` works. `ruff` and `mypy --strict` pass on empty modules. Secrets are not in git.

---

### Phase 1 — Schema and Data Layer
- Apply `schema.sql` to Supabase
- Repository layer with typed accessors
- Seed `companies` manually with 10 known EGX names to test round-tripping

**Acceptance:**
- Inserting a `line_item` without `page_no` or `filing_id` raises a database error, not an application error
- Append-only enforcement verified: `UPDATE` on `screening_results` is rejected
- All monetary columns are `NUMERIC`, confirmed by introspection test

---

### Phase 2 — Research and Ingestion *(the hard phase)*

**2a. Universe discovery**
- Fetch and parse EGX listed company list
- Build `config/sources.yaml`: for each company, the URL pattern where filings appear
- Handle the reality that some issuers only publish to EGX and some only to their own IR page

**2b. Acquisition**
- Download, SHA-256 hash, dedupe, archive to Supabase Storage
- Detect scanned vs text-layer: if extractable text < 100 chars/page → scanned
- OCR path: `tesseract -l ara+eng` with deskew preprocessing

**2c. Extraction**
- Implement per `EXTRACTION_SPEC.md`
- Dual independent passes with different prompt framings
- Every extracted value carries page number and note reference

**2d. Normalisation**
- Arabic-Indic digit conversion
- Unit scale detection (EGP / thousands / millions) — see `EXTRACTION_SPEC.md` §5
- Sign convention normalisation (parenthesised negatives, Arabic bracket styles)

**Acceptance:**
- Golden set of **15 filings minimum**, spanning at least 5 issuers, at least 3 scanned, hand-verified
- ≥99% accuracy on the 12 critical line items (`EXTRACTION_SPEC.md` §3.1)
- 100% of extracted values have a non-null page reference
- A deliberately corrupted PDF produces `DATA_INSUFFICIENT`, not a guess

---

### Phase 3 — Validation Layer
- Implement validators V1–V9 per `EXTRACTION_SPEC.md` §6
- Validation result written to `validations` table for every filing, pass or fail
- Critical-check failure sets `filings.data_status = 'INSUFFICIENT'`

**Acceptance:**
- Every validator has passing and failing unit tests
- Injecting a 5% error into any balance sheet figure in the golden set causes V1 to fail
- Injecting a unit-scale error (×1000) causes V7 to fail
- A filing with `data_status = 'INSUFFICIENT'` cannot reach the scoring stage — enforced by type, not by an `if` statement

---

### Phase 4 — Engine
- Screens A–E, gate logic, traffic light, breach typing, cure window tracking (`ENGINE_SPEC.md` §2–3)
- Seven-pillar scoring (`ENGINE_SPEC.md` §4)
- Decision matrix and vetoes (`ENGINE_SPEC.md` §5)
- Watchlist movement (`ENGINE_SPEC.md` §6)
- Position sizing and rebalancing bands (`ENGINE_SPEC.md` §7)
- Purification ledger (`ENGINE_SPEC.md` §8)

**Acceptance:**
- 100% branch coverage on `engine/`
- Boundary tests at every band edge, including exact-threshold values
- Determinism test: 100 consecutive runs on identical input produce identical output hashes
- A company failing Screen A is never scored — verified by test, not inspection
- Score components sum to exactly 100 for a maximal company

---

### Phase 5 — Reporting and Decisions
- Quarterly report renderer following the Step 17 template
- Decision journal entries generated **before** the order sheet, with mandatory falsification condition
- Order sheet: ticker, side, quantity, order type (limit only), limit price, validity
- Exception queue view for `DATA_INSUFFICIENT` companies

**Acceptance:**
- Full review runs end-to-end from CLI and produces a complete report
- Every decision in the report has a non-empty reason and falsification condition — enforced by validation, empty string rejected
- Order sheet never contains a market order
- Report regenerates identically from stored data (no recomputation drift)

---

### Phase 6 — Scheduling and Hardening
- Scheduler: daily filing-discovery poll during reporting windows (May, Aug, Nov, Mar–Apr); weekly otherwise
- Full review triggered when a watched company files
- Macro data refresh: monthly for CPI, on CBE MPC dates for policy rate
- Alerting on: new filing detected, status colour change, veto fired, extraction failure
- Retry with backoff on all network calls; never retry an LLM extraction into a different answer without flagging disagreement

**Acceptance:**
- A simulated new filing triggers the full pipeline unattended and produces a decision
- Network failure produces a logged, retried, and eventually surfaced error — never a silent skip
- Running the scheduler twice does not double-process a filing (idempotency via file hash)

---

### Phase 7 — Dashboard *(optional)*
Next.js read-only interface: portfolio view, compliance heatmap, score breakdown per company, decision history, exception queue with resolve action, purification ledger.

**Acceptance:** dashboard cannot write any financial figure. Only permitted write is exception-queue resolution, which is itself audit-logged.

## 4. Autonomy Boundary — Explicit

| Function | Autonomous? | Note |
|---|---|---|
| Find new companies | ✅ | |
| Detect new filings | ✅ | |
| Download and archive | ✅ | |
| Extract financials | ✅ | Dual pass |
| Validate | ✅ | Deterministic |
| Exclude on doubt | ✅ | Automatic, no escalation needed |
| Shariah screening verdict | ✅ | Deterministic from validated data |
| Scoring and ranking | ✅ | |
| Buy/sell/hold decision | ✅ | Rule-based from the matrix |
| Watchlist movement | ✅ | |
| Purification calculation | ✅ | |
| Report writing | ✅ | |
| Resolve extraction conflicts | ⚠️ optional | Leaving unresolved = permanent exclusion, which is safe |
| **Place orders** | ❌ | No API exists |

The only mandatory human action in the entire loop is order entry.

## 5. Failure Modes to Design Against

| Failure | Consequence | Control |
|---|---|---|
| Extraction reads wrong figure | False compliance verdict | V1–V9, dual extraction |
| Unit scale misread (×1000) | Every ratio wrong | V7 magnitude check |
| Interest income buried in "other income" | Missed Shariah breach | Mandatory note-level extraction, `EXTRACTION_SPEC.md` §3.3 |
| Islamic financing counted as conventional debt | False non-compliance | Separate line items, §3.2 |
| Cumulative interim treated as standalone | Growth metrics doubled | Period derivation rule, `CLAUDE.md` §5 |
| Market cap staleness | Wrong screen denominator | 12-month trailing average, refreshed daily |
| Restatement of comparatives | Silent history corruption | V5 flags mismatch, never auto-overwrites |
| Nominal growth mistaken for real | Systematically wrong ranking | CPI deflation mandatory in Pillar 5 |
| Company stops filing | Stale data drives live decisions | Filing age check; >180 days → `DATA_INSUFFICIENT` |
| Model drift in extraction | Gradual accuracy decay | Golden-set regression on every run |

## 6. Cost Estimate

Extraction is the only material recurring cost. Roughly: ~30 investable companies × 4 filings/year × 2 extraction passes × ~40k tokens per filing. Budget for a few hundred thousand input tokens per company-year. Supabase free tier suffices until the filing archive exceeds its storage limit.

Reduce cost by filtering the universe on liquidity (`ENGINE_SPEC.md` §4.6, sub-criterion 6B) **before** extraction. There is no reason to extract financials for a company you could never trade.
