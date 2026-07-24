# CLAUDE.md — EGX Shariah Engine Project Constitution

This file is binding. If any instruction elsewhere conflicts with this file, this file wins. If a user request conflicts with this file, stop and raise the conflict rather than silently complying.

---

## 1. Purpose

An autonomous system that screens Egyptian Exchange (EGX) listed companies for Shariah compliance, scores them on a 100-point institutional model, and generates quarterly investment decisions — from primary financial statements only.

## 2. The Prime Directive

> **The system may be wrong. The system may never be confidently wrong about something it did not verify.**

A wrong output here is not a financial error alone — declaring a non-compliant company compliant is an error with religious consequence. The entire architecture exists to make that specific failure mode structurally difficult.

## 3. Non-Negotiable Rules

### R1 — Provenance or nothing
Every financial figure stored in `line_items` carries a mandatory source reference: filing ID, page number, and statement/note location. This is enforced by `NOT NULL` database constraints, not by convention. A figure without provenance cannot physically be inserted.

### R2 — The engine is deterministic
`engine/` contains zero LLM calls. Screening ratios, scores, and decisions are computed by pure Python from stored values. Running the engine twice on the same data must produce byte-identical output. Any non-determinism is a bug of the highest severity.

LLMs are permitted in exactly two layers:
- `ingestion/` — reading documents into structured candidate values (always followed by deterministic validation)
- `reporting/` — writing narrative prose around numbers the engine already produced (never generating or altering a number)

### R3 — Uncertainty resolves to exclusion, never to estimation
If a required figure is missing, ambiguous, or fails validation, mark the company `DATA_INSUFFICIENT` and exclude it from the investable universe. Never interpolate, never estimate, never carry forward a prior value, never infer from a peer, never let a model "reason about what it probably is."

Exclusion is always safe. Estimation is never safe.

### R4 — Thresholds are configuration, not code
Every numeric threshold (Shariah limits, scoring bands, materiality triggers, position caps) lives in `config/thresholds.yaml` with a version number and a source citation. Changing a threshold must be a config change with a changelog entry, never a code edit.

### R5 — Immutable audit trail
Every screening result, score, and decision is written append-only with its full input snapshot. Never update in place. You must be able to reconstruct in two years exactly why a given decision was made and from which numbers.

### R6 — No execution
The system generates order instructions. It never places, routes, or transmits an order. There is no brokerage integration, no credential storage for any trading platform, and no automated execution path. This is a hard architectural boundary.

### R7 — Shariah is a gate, evaluated first
Compliance screening runs before scoring. A company failing the gate is never scored, never ranked, never appears in a portfolio recommendation. It cannot be rescued by strength on any other dimension.

### R8 — Never silently change a rule
If you believe a rule in `ENGINE_SPEC.md` is wrong, say so and propose the change. Do not implement your own version. The specification is the contract.

## 4. Architecture Boundaries

```
research/     → discovers companies, filings, macro data      [LLM + web allowed]
ingestion/    → downloads, OCRs, extracts candidate values    [LLM allowed]
validation/   → deterministic checks on extracted values      [NO LLM]
engine/       → screening, scoring, decisions                 [NO LLM]
reporting/    → renders reports and journal entries           [LLM for prose only]
api/          → read-only serving layer                       [NO LLM]
```

Data flows in one direction only. `engine/` must never import from `ingestion/`. `validation/` must never call an API of any kind.

## 5. Code Standards

- **Python 3.11+**, `uv` for dependency management, `ruff` for lint, `mypy --strict` on `engine/` and `validation/`
- **All money as `Decimal`**, never `float`. Currency amounts are stored as `NUMERIC(20,2)` in EGP.
- **All ratios as `Decimal`** with explicit rounding at the point of comparison only, never during calculation.
- **Every engine function is pure** — inputs in, value out, no I/O, no clock reads, no randomness.
- **Timezone**: store UTC, display Africa/Cairo.
- **Fiscal periods** are identified as `(fiscal_year, period_type)` where `period_type ∈ {Q1, H1, 9M, FY}`. EGX interim reporting is cumulative — Q2 standalone must be derived as `H1 − Q1`, never assumed reported.

## 6. Testing Requirements

- `engine/` requires **100% branch coverage**. Non-negotiable.
- Every scoring sub-criterion has a test at each band boundary, including the exact boundary value.
- Every validator has a passing case and a failing case.
- **Golden-file tests**: a set of hand-verified filings with known-correct extracted values. Extraction changes must not regress these. Build this set as you go — every bug found in production becomes a golden file.
- Property test: for any valid input, screening status is one of exactly four values and never null.

## 7. Language and Localisation

- Code, comments, commit messages, and specs: **English**
- Extracted company names, statement captions, and generated reports: **bilingual (AR/EN)** where the source provides both
- Arabic text storage: UTF-8, preserve original, do not transliterate
- Arabic numerals in source documents (٠١٢٣٤٥٦٧٨٩) must be normalised to ASCII digits at parse time — this is a known and frequent source of extraction error

## 8. What "Done" Means

A phase is complete when its acceptance criteria in `BUILD_SPEC.md` pass, tests are green, and the work is committed. Not when the code appears to work.

## 9. When to Stop and Ask

Stop and raise the question rather than proceeding if:
- A specification is ambiguous in a way that affects a stored financial number
- Implementing something as specified would require violating a rule in Section 3
- You find that a threshold in `config/thresholds.yaml` appears to contradict a published standard
- Extraction accuracy on the golden set drops below 99% on any critical line item
- You are about to write code that estimates, infers, or fills in a financial figure

Raising a question costs a message. A wrong compliance verdict costs more than that.
