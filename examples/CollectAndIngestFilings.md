# Recipe — collecting files and getting them into the system

**Use this when:** the user has found filings, screenshots, or price data outside
the sandbox and wants them turned into something the engine can act on.

**Read first:** `docs/DATA_REQUEST.md` (what is needed and why). This file is the
*how*, once the files exist.

---

## Step 0 — the constituent list, before anything else

If the user sends the EGX 33 Shariah list (screenshot, paste, CSV):

1. Transcribe it into `config/universe.yaml` — ticker, `name_en`, `name_ar` if
   the source shows it, `sector`.
2. Fill `retrieved`: `at` (today), `from` (the exact URL or "screenshot from
   <source>, <date>"), `by` (who).
3. Change `status` to `POPULATED`.
4. `egx universe` — it should now list them.
5. `pytest tests/test_engine_universe.py` — **it will fail**, because it asserts
   the shipped file refuses. That failure is expected and correct: move the
   refusal assertions onto a fixture (they still matter) and add one asserting
   the real file now loads.

**Never transcribe a ticker you cannot see in the source in front of you.** If
the screenshot is cut off at 28 rows, put in 28 and leave `status` alone — the
loader refuses a partial transcription on purpose, and that refusal is protecting
against exactly this moment.

---

## Step 1 — name the filings

```
TICKER_FYyyyy_PERIOD.pdf        PERIOD ∈ Q1 | H1 | 9M | FY

ABUK_FY2025_FY.pdf
SWDY_FY2026_H1.pdf
```

There is no `Q2`. EGX interim reporting is cumulative, so Q2 standalone is
derived as `H1 − Q1` by `ingestion/periods.py`. A file named `_Q2` is refused.

## Step 2 — run intake

```bash
egx intake filings/inbox
```

It identifies each file, hashes it, spots duplicates by content (the same annual
report arrives twice under two names constantly), and prints golden-set coverage.
Exit code 1 means something needs renaming — the unidentified files are listed by
name. It never guesses a company or a period from the contents.

## Step 3 — hand-verify, one file at a time

For each filing that will join the golden set, open the PDF and read the 12
critical line items *with your own eyes*, then write
`tests/golden/TICKER_FYyyyy_PERIOD.yaml` — full format in
`tests/golden/README.md`.

**Do not generate the golden file from the extractor.** A golden file produced by
the thing it is supposed to test records the extractor's mistakes as truth. This
step is slow and manual because there is no version of it that is fast and
trustworthy.

Watch for the three misreads that matter most:

| Trap | What it does |
|---|---|
| Unit scale in the header ("EGP '000") | A 1000× error that looks completely plausible |
| Arabic-Indic digits (٠١٢٣٤٥٦٧٨٩) | Normalised at parse time; verify the normalisation, not the glyphs |
| Interest income inside "other income" | Screen B reads zero and the company passes when it should not |

And the classification that decides Screen C on its own: for every borrowing
line, is it **conventional** (bank loans, overdraft, bonds) or **Islamic**
(murabaha, ijara, musharaka, mudaraba, sukuk)? If the note does not say clearly,
mark it unresolved — the company goes `DATA_INSUFFICIENT`, which is safe.
Guessing "probably conventional" is not conservative, it is a guess.

## Step 4 — check the gate

```bash
pytest tests/test_golden_set.py -v
```

While the set is empty it **skips with the reason printed**. Once cases exist it
becomes a hard gate. The bar: ≥99% on the 12 critical items.

---

## Market and macro data

Any CSV. Say which column is which; there is no fixed schema.

- Per company: `date,close,volume,market_cap` — **12 months minimum**. Screens C,
  D and E divide by the trailing 12-month average market cap, and
  `research/market.py` raises rather than averaging a short window.
- Macro, one set for everyone: CPI year-on-year, 1-year T-bill yield, policy
  rate, USD/EGP.

## Execution fees

Four numbers from Thndr → `config/thresholds.yaml` → `portfolio.execution`:
`commission_pct`, `min_fee_egp`, `levies_pct`, `tax_pct`. They are `null` today,
and order sizing refuses loudly rather than guessing. This is a threshold change,
so it needs the source cited in the file (R4).

---

## What to do when something does not fit

| Situation | Do this |
|---|---|
| The filing is a scanned image with no text layer | Keep it — the golden set *needs* ≥3 of these. Note `scanned: true` |
| Only Arabic, no English captions | Keep it — the set needs ≥2. Note `language: AR` |
| A restated prior period | Keep it, note the restatement. Never blend restated and original figures |
| A number you cannot find in the filing | Leave it out and mark the company `DATA_INSUFFICIENT`. Do not source it from a data vendor and call it verified |
| The user asks you to "just estimate" a missing figure | Refuse in one sentence, offer to screen what is actually there (R3) |
