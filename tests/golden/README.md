# tests/golden/ — the golden set

The regression gate on extraction accuracy (`ROADMAP_V1` M6). **It is empty**, and
until it is not, extraction accuracy is *unknown* — not "good".

`tests/test_golden_set.py` discovers every case in this directory. With zero cases
it reports the gap loudly and passes only the harness's own self-test. It does
not pretend the bar is met by an empty set.

---

## Adding a case (the whole workflow)

### 1. Put the PDF somewhere durable

Do **not** commit filing PDFs — they are large, and some are redistributable only
under the issuer's terms. Keep them in `filings/` (git-ignored) or any local path.
Commit only the manifest and the hand-verified values.

### 2. Create `tests/golden/TICKER_FYyyyy_PERIOD.yaml`

```yaml
# tests/golden/ABUK_FY2025_FY.yaml
ticker: ABUK
fiscal_year: 2025
period: FY                    # Q1 | H1 | 9M | FY

source:
  file: filings/ABUK/2025/FY/ab12cd34ef567890.pdf
  sha256: ab12cd34ef567890...          # full hash, from `egx intake`
  pages: 84
  language: AR                          # AR | EN | MIXED
  scanned: false
  unit_scale: thousands                 # units | thousands | millions
  verified_by: mohamed
  verified_at: 2026-07-28

# The 12 critical line items, in EGP AFTER unit scaling, read by a human
# off the statement. Every one carries where it was found (R1).
expected:
  total_assets:            {value: 41234567890, page: 12, note: "Balance sheet"}
  cash_and_equivalents:    {value:  3210000000, page: 12, note: "Balance sheet"}
  time_deposits:           {value:  1500000000, page: 45, note: "Note 14"}
  treasury_bills_and_bonds:{value:   900000000, page: 45, note: "Note 14"}
  accounts_receivable:     {value:  2100000000, page: 12, note: "Balance sheet"}
  short_term_borrowings:   {value:   400000000, page: 13, note: "Balance sheet"}
  long_term_borrowings:    {value:  1200000000, page: 52, note: "Note 21"}
  bank_overdraft:          {value:           0, page: 13, note: "nil"}
  bonds_payable:           {value:           0, page: 52, note: "nil"}
  total_revenue:           {value: 28000000000, page: 15, note: "Income statement"}
  interest_income:         {value:   350000000, page: 61, note: "Note 27, inside other income"}
  net_profit_attributable: {value:  6100000000, page: 15, note: "Income statement"}

# The classification that decides Screen C on its own.
financing:
  - {caption: "قروض بنكية طويلة الأجل", classification: CONVENTIONAL, page: 52}
  - {caption: "تمويل مرابحة",           classification: ISLAMIC,      page: 52}

# What the engine must conclude from the above. Filled in on the first run,
# then frozen — this is what catches a silent behaviour change.
expected_gate:
  status: GREEN
  worst_screen: C
```

### 3. Read the values off the PDF *by hand*

This is the whole point. A golden file produced by the extractor tests nothing —
it just records what the extractor already does, including its mistakes. Open the
statement, read the number, type it in, note the page.

### 4. Run the gate

```
pytest tests/test_golden_set.py -v
```

---

## What the set must eventually contain

| Requirement | Why this specific case |
|---|---|
| ≥ 15 filings | Below that, one bad case swings the accuracy rate past the 99% bar |
| ≥ 5 issuers | One issuer's house style is not a test of the extractor |
| ≥ 3 scanned PDFs | OCR is a different failure mode from text extraction |
| ≥ 2 Arabic-only | Arabic-Indic digits, RTL tables, no English caption to fall back on |
| ≥ 1 with Islamic financing | Murabaha/ijara must be excluded from Screen C's numerator |
| ≥ 1 with interest income inside "other income" | The most consequential hiding place in the whole filing |
| ≥ 1 in thousands, ≥ 1 in millions | Unit-scale misreads are 1000× errors that look plausible |
| ≥ 1 with a restatement | A restated prior period must not be silently mixed with the original |

## The bar

**≥ 99% accuracy on the 12 critical line items**, and a deliberately corrupted PDF
must yield `DATA_INSUFFICIENT` rather than a guess. Any prompt or parser change
that drops below the bar is blocked — that is what makes this a gate rather than
a report.

Until it passes, the agent may screen and explain, but must not be trusted to
open a position on freshly extracted data.
