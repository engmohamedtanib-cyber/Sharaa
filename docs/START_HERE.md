# START HERE — Handoff to Claude Code

## What this package is

A complete build specification for an autonomous EGX Shariah-compliant investment analysis engine. Everything Claude Code needs to build the system from an empty directory.

## Files in this package

| File | Purpose | Read order |
|---|---|---|
| `START_HERE.md` | This file. Kickoff instructions. | 1 |
| `CLAUDE.md` | Project constitution. Non-negotiable rules. Copy to repo root. | 2 |
| `BUILD_SPEC.md` | Architecture, build phases, acceptance criteria. | 3 |
| `schema.sql` | Complete Supabase/Postgres schema. | 4 |
| `EXTRACTION_SPEC.md` | Ingestion, extraction, validation pipeline. | 5 |
| `ENGINE_SPEC.md` | Every formula, threshold, and decision rule. | 6 |

`ENGINE_SPEC.md` and `EXTRACTION_SPEC.md` are the two documents that contain the actual intellectual property. The rest is scaffolding.

## Prerequisites before starting

Set up and have credentials ready for:

1. **Supabase project** — free tier is sufficient for Phase 1–3. Need: project URL, `service_role` key, database connection string.
2. **Anthropic API key** — for the extraction layer.
3. **Python 3.11+** and `uv` (or Poetry).
4. **Tesseract OCR with Arabic language pack** — `apt install tesseract-ocr tesseract-ocr-ara tesseract-ocr-eng`
5. **Supabase Storage bucket** named `filings` for PDF archival.

## The kickoff prompt

Paste this into Claude Code in an empty repository directory, with all six files from this package placed in `docs/`:

---

```
Read docs/CLAUDE.md first and treat it as binding for this entire project.
Then read docs/BUILD_SPEC.md, docs/schema.sql, docs/EXTRACTION_SPEC.md,
and docs/ENGINE_SPEC.md in full before writing any code.

Build the EGX Shariah Engine as specified. Work through the phases in
BUILD_SPEC.md in order. Do not start a phase until the previous phase's
acceptance criteria pass.

Critical constraints you must never violate:
- No financial number enters the database without a source reference
  (document, page, note). This is enforced by a NOT NULL constraint.
- The screening and scoring engine is pure Python. Zero LLM calls in
  that layer. Same inputs must always produce identical outputs.
- If data cannot be validated, the company is marked DATA_INSUFFICIENT
  and excluded. Never estimate, interpolate, or infer a missing figure.
- Every threshold lives in config/thresholds.yaml, never hardcoded.

Start with Phase 0. Confirm the plan with me before writing code.
```

---

## What you (the human) will do after the build

Exactly two things, on a quarterly cadence:

1. **Review the exception queue.** Companies flagged `DATA_INSUFFICIENT` land here. You can either resolve the ambiguity manually or leave them excluded — leaving them excluded is always safe.
2. **Place the orders.** The system generates a complete order sheet (ticker, side, quantity, order type, limit price, validity). Thndr has no retail execution API, so you enter these manually.

Everything else — filing discovery, download, extraction, validation, screening, scoring, decision generation, watchlist movement, purification accounting, report writing, journal entries — runs unattended.

## Honest expectations on build effort

| Phase | Realistic effort with Claude Code |
|---|---|
| Phase 0–1 (scaffold + schema) | 1 session |
| Phase 2 (ingestion + extraction) | 3–5 sessions — this is where the difficulty is |
| Phase 3 (engine) | 2 sessions — deterministic, well-specified, fast |
| Phase 4 (reporting + decisions) | 1–2 sessions |
| Phase 5 (dashboard, optional) | 2–3 sessions |
| Phase 6 (scheduling + hardening) | 1–2 sessions |

Phase 2 will consume the majority of the effort. Arabic financial PDFs, inconsistent layouts across issuers, and scanned documents are the real engineering problem. The engine itself is straightforward once clean data exists.

## Scope warning

This system is economically irrational for a 1,000 EGP portfolio — the build cost exceeds the capital by orders of magnitude. It only makes sense if you are building it as a product, or as a durable personal asset you will use for a decade. Decide which before you start, because it changes how much you should invest in the dashboard and multi-user layers.

## Legal note to carry into the build

Publishing buy/sell recommendations to third parties in Egypt may constitute regulated investment advisory activity under FRA rules. Verify the licensing position before making this available to anyone other than yourself. A personal analysis tool and a public advisory service are different regulatory objects.
