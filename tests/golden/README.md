# tests/golden/ — the golden set

Hand-verified filings with known-correct extracted values. `CLAUDE.md` §6 makes
this a testing requirement; `decisions/0004` makes it the gate that decides
whether the extraction layer can be trusted at all.

**Status: empty.** Zero real filings have been processed. Extraction accuracy is
therefore *unknown*, not "good" — see `memory/KNOWN_ISSUES.md` §1. This README
is the contract, so that when the first filing arrives there is nothing left to
decide.

It lives here rather than at the repo root because `.gitignore` already carves
out `!tests/golden/**/*.pdf` against the global PDF exclusion — a second
location would mean golden PDFs silently not being committed.

---

## Layout

```
tests/golden/<ticker>/<fiscal_year>-<period_type>/
    filing.pdf          the source document (committed, by the .gitignore carve-out)
    expected.yaml       the hand-verified values
    metadata.yaml       evidence record — same schema as memory/evidence/
```

`expected.yaml` records, per line item: the value, the page number, and the
statement or note it was read from. Those are the same three things `line_items`
requires under R1 — if a golden entry cannot supply them, the extraction it is
meant to validate could not have supplied them either.

## The rule that makes it worth having

> **Values are read by a human from the document. Never by the extractor whose
> output they exist to check.**

A golden file generated from extractor output tests that the extractor is
consistent, not that it is correct — and it would pass at 100% while being
wrong in exactly the way the set exists to catch. If a value is genuinely
ambiguous in the source, that is the finding: record it as ambiguous and let the
company resolve to `DATA_INSUFFICIENT` (R3).

## The bar

- **≥99% on the 12 critical line items**, per `decisions/0004`. Below that, the
  honest outcome is written down in that ADR and is not renegotiable here.
- **Every production bug becomes a golden file** (`CLAUDE.md` §6). The filing
  that broke it is the regression test.
- Extraction changes must not regress the set. It runs in CI like any other
  test.

## Adding one

1. Admit the filing as evidence — `checklists/NEW_EVIDENCE.md`.
2. Read the values off the PDF yourself. Record value, page, and location.
3. Write `expected.yaml`. Note anything ambiguous rather than resolving it.
4. Run the extractor against it and record the delta. **A failure here is a
   finding, not a reason to adjust the expected values.**
