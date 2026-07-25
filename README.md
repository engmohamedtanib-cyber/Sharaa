# EGX Shariah Engine

An autonomous system that screens Egyptian Exchange (EGX) listed companies for
Shariah compliance (AAOIFI), scores them on a 100-point institutional model, and
generates quarterly investment decisions — **from primary financial statements
only**.

> **Prime Directive** (`CLAUDE.md`): The system may be wrong. The system may
> never be *confidently* wrong about something it did not verify.

## Architecture

```
research/   → discovers companies, filings, macro data      [LLM + web allowed]
ingestion/  → downloads, OCRs, extracts candidate values    [LLM allowed]
validation/ → deterministic checks on extracted values      [NO LLM]
engine/     → screening, scoring, decisions                 [NO LLM]
reporting/  → renders reports and journal entries           [LLM for prose only]
db/         → schema + repository                            [NO LLM]
```

Data flows one direction. `engine/` never imports from `ingestion/`.
`validation/` never makes a network call.

## Non-negotiables (`CLAUDE.md`)

- **R1 — Provenance or nothing.** Every stored figure carries filing id, page,
  and note location (`NOT NULL` in the schema).
- **R2 — The engine is deterministic.** Zero LLM calls in `engine/`. Same inputs
  → byte-identical output.
- **R3 — Uncertainty → exclusion, never estimation.** Missing/ambiguous data →
  `DATA_INSUFFICIENT`. Never interpolate.
- **R4 — Thresholds are config.** Every number lives in
  `config/thresholds.yaml`, versioned.
- **R7 — Shariah is a gate, evaluated first.** A company failing the gate is
  never scored.

## Status of this build

Everything below runs and is tested with **no external credentials**. Network
and model calls sit behind injectable protocols, so the logic around them is
exercised with fakes.

| Layer | State |
|---|---|
| `config/` | ✅ thresholds, line-items, prohibited activities |
| `db/` (schema + models + in-memory repo) | ✅ |
| `validation/` (V1–V9 + status derivation + type-level gate) | ✅ pure, deterministic |
| `engine/` (screens A–E, 7-pillar scoring, decisions, watchlist, portfolio, purification) | ✅ pure, deterministic |
| `ingestion/` (normalisation, period derivation, extraction contract, acquisition, reconciliation) | ✅ deterministic parts tested; LLM/network behind protocols |
| `reporting/` (decision journal, order sheet) | ✅ pure |
| `research/` (universe, filings, macro, market discovery) | ⏳ needs live EGX/CBE sources |
| Live Supabase + Anthropic wiring | ⏳ needs credentials |

### What the ingestion layer guarantees

- **Arabic-Indic and Eastern digits** normalised before any parsing (§5.1)
- **Unit scale** read from the statement header, and **refused** when the header
  declares none — defaulting to units would silently make every ratio 1000x
  wrong, and V1 cannot catch it (§5.2)
- **Sign conventions**: `(1,234)`, `1,234-`, `−1,234`, full-width brackets (§5.3)
- **Separator disambiguation** for `1,234.56` vs `1.234,56` vs `1,234`, with an
  ambiguity **refusal** rather than a guess (§5.4)
- **Cumulative interims** derived to standalone quarters (`Q2 = H1 − Q1`), with
  balance-sheet items never subtracted (§4)
- **Dual independent passes** with different framing, reconciled at 0.1%;
  critical disagreement withholds the value and raises `CONFLICT` (§2, §6 V9)
- **Interest income traced into the notes** — a material `other_income` whose
  note cannot be parsed makes the filing `DATA_INSUFFICIENT` (§3.3)
- **Islamic vs conventional financing** classified explicitly; a caption
  matching both or neither is `AMBIGUOUS` and blocks the filing (§3.2)

## Quickstart

```bash
uv sync --extra dev          # install core + dev tooling
uv run egx --help            # CLI
uv run egx demo --dry-run    # gate -> score -> decision -> journal -> order sheet
uv run egx normalise         # value normalisation and unit-scale detection
uv run pytest                # full suite
uv run ruff check .
uv run mypy src/engine src/validation src/ingestion src/reporting
```

The repository layer defaults to an in-memory store (`EGX_STORE_BACKEND=memory`),
so the pipeline runs end-to-end on fixtures without a database. Point it at
Supabase by filling `.env` (see `.env.example`) and setting
`EGX_STORE_BACKEND=supabase`.

## Human-in-the-loop

The only mandatory manual step is **order entry** (Thndr exposes no retail
execution API — `CLAUDE.md` R6). The engine produces an order sheet; a human
places the orders. It also surfaces an exception queue of `DATA_INSUFFICIENT`
companies, which are safe to leave excluded.

See `docs/` for the full specification package.
