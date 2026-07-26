# KNOWN_ISSUES.md

Open defects and honest limitations. An empty section is a claim — only write "none" when
you have checked.

---

## Open

### 1. Extraction accuracy is unknown, not good
`ingestion/extract.py` has never seen a real filing. Its prompt contract and parser are
tested; its *accuracy* is untested by definition. **Do not open a position on freshly
extracted data until the golden set (M5) passes at ≥99% on the 12 critical line items.**

### 2. Separator heuristic can misread sub-unit money
`parse_number("12.345")` returns `12345` (thousands grouping, the EGX money convention).
For a genuine ratio or EPS printed as `12.345`, the caller must pass `decimal_places`.
Mitigated for the known dangerous case (a leading `0` blocks grouping, so `0.005` is
correct). Revisit if a golden filing shows a counter-example.

### 3. No liveness anywhere
Nothing polls, nothing schedules, nothing alerts. Proactive monitoring is a design
intention, not a running capability. See `decisions/0001` for what is and is not possible
on a subscription runtime.

### 4. `research/` holds only the universe transcriber
EGX filing discovery, market data and CBE macro are unimplemented. Every market-dependent
sub-criterion (6A–6D, 4A, 4D) will score `MISSING_DATA` until they exist — which is
correct behaviour, but means scores are currently structurally understated.

### 5. Every constituent's `sector` is `null`
The EGX constituents export carries no sector column, and `decisions/0005` refuses to fill
it from recall. Consequence: the **sector concentration cap in `engine/portfolio.py` has
nothing to bite on** — it will not reject a portfolio that is in fact concentrated in one
sector. Screening and scoring are unaffected. Blocked on a primary source for sector.

### 6. EGX filings arrive as image scans with NO text layer
Both Telecom Egypt Q1-2026 PDFs (30 and 28 pages) extract **zero characters** —
`pdfplumber` and `pypdf` agree, one full-page image per page. Text parsing is not merely
unreliable here, it is impossible. Every filing needs OCR or vision before any extraction
step runs. `ingestion/extract.py` currently assumes a text layer.
Found 2026-07-26 on the first two real filings ever received — a sample of two, but two out
of two.

### 7. A condensed interim does not contain what the gate needs
Telecom Egypt's Q1-2026 note 9 (Net finance cost) is **prose with no breakdown table**, and
the profit-or-loss face shows a single undecomposed "Finance income" line. Interest income
is therefore unobtainable, and Screen B cannot be computed → `DATA_INSUFFICIENT`
(`tests/golden/ETEL/2026-Q1/expected.yaml`).
Consequence for data collection: **prefer the audited annual report.** Interim filings may
be structurally insufficient for the Shariah gate regardless of extraction quality, which
is a document-selection problem, not an engine problem.

### 8. Screens C, D and E cannot run at all yet
Their denominator is `mcap_avg_12m` (`config/thresholds.yaml`). A trailing 12-month average
market capitalisation exists in no filing and there is no market-data source (M5). Shares
outstanding *are* obtainable from the filings (ETEL: 1 707 071 600, note 26). **Three of
the five screens are blocked on price history, not on filings** — which was not obvious
before the first real document.

### 9. FRA fee is modelled per order, but charged per transaction
`engine/portfolio.trade_cost` assumes one order fills as one transaction. The FRA levy
(0.005%, floor 1 EGP) is charged **per transaction**, and one order can fill across several
counterparties. Consequence: trade cost is **understated** for fragmented fills — the unsafe
direction. Accepted because fill count is unknowable in advance (`decisions/0007`).

### 10. Universe provenance stops one link short
`config/universe.yaml` attests to the exact bytes it was transcribed from (sha256, archived
in-repo) but **not** to the URL those bytes came from — egress is blocked here, so the
upload's chain of custody before it reached the session is unverified. Recorded honestly in
`retrieved.from`. Upgrade only against a real fetch whose hash matches, never by assertion.

---

## Accepted limitations (not defects — do not "fix")

- **Order entry is manual.** R6, no broker API, and regulatory posture. Permanent.
- **Weights may not deploy all investable cash.** When candidates are too concentrated for
  the sector/position caps, the remainder stays as cash and is surfaced. Intentional.
- **A company excluded for missing data stays excluded.** Safe by construction.

---

## Resolved

- **`0.005` parsed as `5`** — a leading-zero first group was being read as thousands
  grouping. Fixed; every sub-unit ratio would have been 1000× too large.
  (Found by its own test before any real data.)
- **`build_weights` re-inflated positions past the cap** after sector capping. Rewritten as
  bounded water-filling; the undeployable remainder now stays as cash.
- **Watchlist fallback promoted straight to HIGH_CONVICTION**, bypassing the two-review
  gate. Fixed — re-entry is deliberately harder than exit.
