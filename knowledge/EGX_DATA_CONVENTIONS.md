# EGX data conventions

Things about Egyptian market data that a general-purpose model gets wrong by
default, each with the place it can be checked. Nothing here is from recall.

**Reading rule:** if you are about to rely on an EGX convention that is *not* on
this page, you do not know it — you remember it. Check it, then add it here with
a citation, or exclude what depends on it (R3).

---

## Reporting periods

**Interim statements are cumulative, not standalone.** The "Q2" figure a filing
shows is six months of activity. Standalone Q2 must be derived as `H1 − Q1` and
is never assumed reported.
> `CLAUDE.md` §5 · implemented in `ingestion/periods.py` · tested in
> `tests/test_ingestion_periods.py`

A period is identified by `(fiscal_year, period_type)` with
`period_type ∈ {Q1, H1, 9M, FY}`. There is no standalone quarter type, by design
— deriving one is an operation, not a label.
> `CLAUDE.md` §5 · `engine/types.py::PeriodType`

## Numerals and text

**Arabic-Indic digits (٠١٢٣٤٥٦٧٨٩) appear in source documents** and must be
normalised to ASCII at parse time. This is called out as a known and frequent
source of extraction error, not a theoretical one.
> `CLAUDE.md` §7 · `ingestion/normalise.py` · `tests/test_ingestion_normalise.py`

**Arabic text is stored verbatim in UTF-8, never transliterated.** Company names
and statement captions keep their original form.
> `CLAUDE.md` §7 · asserted for the universe in
> `tests/test_universe.py::test_shipped_arabic_names_are_preserved_not_transliterated`

**Thousands separators are ambiguous with decimal points** in EGX money
formatting: `12.345` is twelve thousand, not twelve point three. Callers wanting
a genuine decimal must say so explicitly.
> `memory/KNOWN_ISSUES.md` §2 · `ingestion/normalise.py`

## Identifiers

**Reuters codes carry a `.CA` venue suffix**: `TMGH.CA`, `ETEL.CA`. The EGX
ticker is the part before the dot.
> All 34 rows of `egx/shariah_index/2026-04-30` · asserted in
> `tests/test_universe.py::test_every_shipped_row_carries_an_egyptian_isin_and_a_ca_ric`

**Egyptian ISINs are 12 characters beginning `EG`** (the constituents export
labels this column `SYMBOL_CODE`).
> Same record, same test.

**One company can hold more than one listing.** Faisal Islamic Bank of Egypt is
listed in EGP (`FAIT`, ISIN EGS60321C014) and in USD (`FAITA`, EGS60322C012) —
two rows, two ISINs, one issuer. Any per-company limit must group by issuer, or
a single bank occupies two position slots.
> `decisions/0005` · `engine/universe.py::Universe.issuers`

## The EGX 33 Shariah Index

Drawn from EGX100; constituents must have a Shariah supervisory board and meet
the board's activity, financial-ratio and liquidity criteria. Launched June 2024,
base date 2022-01-01. Single-stock weight capped at **15%**.
> `decisions/0004` · `config/universe.yaml` header

The 15% cap is independently corroborated by the data rather than taken on
trust: in the 2026-04-30 export, `TMGH` and `ETEL` both sit at exactly 0.15000000
after float noise is removed, and nothing sits above it.
> `egx/shariah_index/2026-04-30` · `research/transcribers/transcribe_index.py`

**Published weights are a snapshot, not a live state.** They are stamped with an
as-of date and drift with price between rebalances, so a weight above the cap
mid-cycle is a real published state, not a corrupt file.
> `decisions/0005` · `tests/test_universe.py::test_weight_above_the_cap_is_allowed`

**Membership is a periodic, lagging signal.** A company can breach between
rebalances, which is why Screens A–E run independently on every constituent
rather than deferring to the index.
> `decisions/0004` · `CLAUDE.md` R7

## EGX site data — two traps

**"Market Cap. Data" is the whole exchange, not a company.** The export has two
columns, `Trade Date` and `Market Cap. Total`, and no company column. Per-company
figures live under **Historical Statistics → Stocks Data** with a company
selected. Reaching for the wrong one puts a denominator ~22x too large into
Screens C/D/E, which turns a breach into a comfortable pass.
> `egx/market_cap_total/2026-07-26` — the record carries the worked numbers

**EGX "`.xls`" downloads are HTML.** `file(1)` reports "HTML document"; the
payload is a single `<table>`. openpyxl and xlrd both fail on it. Parse it as
HTML, not as a workbook.
> Same record.

## Price history — two ways to get a wrong denominator

**Use `Close`, never `Adj. Close`.** Market capitalisation is the price actually
traded multiplied by shares outstanding. Adjusted close back-adjusts for
dividends so that return series are comparable, and it drifts *below* the traded
price as adjustments accumulate — on ETEL's 2026 history the two diverge by over
1.50 EGP within six months. Feeding adjusted prices into `mcap_avg_12m` shrinks
the denominator of Screens C, D and E and makes every company look more
compliant than it is.
> Observed 2026-07-26 in a stockanalysis.com export: `Close 95.65` against
> `Adj. Close 94.15` on the same row.

**A partial window biases toward compliance, so never average what you have.**
The denominator is a *trailing 12-month* average by design (`ENGINE_SPEC` §2.6).
A shorter window is not a smaller sample of the same thing — it is a different
number. ETEL traded near 37-40 EGP in August 2025 and near 103 in July 2026, so
averaging only the recent months would have raised the denominator sharply,
shrunk the debt ratio, and moved the company toward a pass. Missing months are
`DATA_INSUFFICIENT`, not an invitation to average what is available (R3).

**Copy-paste from price sites can destroy the date column.** A 117-row paste
from stockanalysis.com arrived with every `Date` cell reading "2026" — no day,
no month — which makes the rows unalignable to any window. Prefer a screenshot,
or a real CSV export; and reconstructing dates by counting trading days
backwards is inference, not data.

## Money and time

All money is `Decimal` in EGP, stored `NUMERIC(20,2)`. Ratios are `Decimal`,
rounded only at the point of comparison. Timestamps are stored UTC and displayed
Africa/Cairo.
> `CLAUDE.md` §5 · `common/decimals.py`

Trading costs, thresholds and caps are **configuration**, never facts to be
remembered — they live in `config/thresholds.yaml` with a version and a source.
> `CLAUDE.md` R4

---

## Deliberately absent

Not written down here because this project has not verified them: settlement
cycle, trading hours, tick sizes, price limits, taxes, and actual broker
commission schedules. Several are needed eventually — the trade-cost gate uses
fee figures from `config/thresholds.yaml`, whose own citations should be checked
before anyone relies on them for a live order.
