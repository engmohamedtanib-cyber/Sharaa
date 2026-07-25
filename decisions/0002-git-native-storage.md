# 0002 — Git-native JSONL storage, not a database (V1)

**Status:** Accepted · **Date:** 2026-07-25 · **Supersedes:** the Supabase requirement in
`docs/ARCHITECTURE_V2.md` §3.1 (truth plane) and roadmap milestone M4

## Context

The original design put the system of record in Supabase Postgres, with `schema.sql` and
`schema_v2_ledger.sql` enforcing provenance, append-only history and validated-only
screening at the database level. That is the right shape for a hosted service.

But this is a personal tool with a subscription runtime (`decisions/0001`). Supabase adds
credentials to manage, a network dependency for every read, and an availability failure
mode — for a dataset of roughly one portfolio, a few hundred ledger events, ~30 companies
and ~120 filings a year.

## Decision

**Storage is plain files in this git repository for V1.**

| Data | Where | Format |
|---|---|---|
| Portfolio ledger | `memory/portfolio/ledger.jsonl` | one JSON object per line, append-only |
| Policy versions | `memory/portfolio/policy.jsonl` | append-only |
| Extracted filings | `memory/filings/<ticker>/<year>-<period>.json` | with full provenance |
| Decisions & journal | `memory/decisions/<review>.json` + rendered `.md` | append-only |
| Archived PDFs | `memory/filings/<ticker>/*.pdf` | gitignored if large; hash recorded |

The SQL schemas are **kept, not deleted** — they remain the normative description of the
constraints, and they are the migration target if P2 is ever revisited.

## Why JSONL specifically

The ledger is already event-sourced, and an append-only event log **is** a JSONL file.
This is not a compromise; it is a better fit:

- **Append-only is native.** Writing a line is the only supported operation.
- **Diffable.** `git diff` shows exactly what changed in the portfolio, in plain text.
- **Auditable forever.** Git history gives us R5's "reconstruct why in two years" for free,
  with signatures and timestamps we did not have to build.
- **No availability failure.** The data is on disk, in the repo, next to the code that
  reads it.
- **Exact decimals.** Numbers are serialised as strings and parsed with `Decimal`, so no
  float ever touches money — the same guarantee `NUMERIC` gave us.

## What we give up, and how it is replaced

| Postgres gave us | Replacement |
|---|---|
| `NOT NULL` provenance | `LedgerEvent.__post_init__` and `ExtractedItem.__post_init__` raise |
| Append-only triggers | The store exposes only `append()`; there is no update or delete method |
| `guard_validated_only` | The `ValidatedFiling` type-level gate in `validation/runner.py` |
| `CHECK` constraints | Dataclass validation, already tested |
| Concurrent writes | Not needed — one user, one session at a time |

The invariants did not weaken; their enforcement point moved from the database to the
type system, which was already doing the same job in parallel.

## Consequences

- M4 (Supabase wiring) leaves the critical path. The roadmap shortens.
- No credentials are needed for M2, M3 or M4 — implementation is unblocked.
- If the ledger ever exceeds ~100k events or needs multi-device concurrent writes, migrate
  to Postgres using the retained schemas (`memory/FUTURE_PROPOSALS.md` P2).
- **The repository now contains personal financial data.** It must stay private, and large
  binaries (filing PDFs) are gitignored with their hashes recorded in the JSON.
