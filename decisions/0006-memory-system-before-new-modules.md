# 0006 — Finish the memory system before building new modules

**Status:** Accepted · **Date:** 2026-07-26
**Question asked:** M0 finished and `NEXT_TASK.md` pointed at M2 (the IPS). A
second opinion the user sought said: stop, build the memory system completely
first. Who is right?

## The check that settled it

Before arguing, we checked what actually existed:

| Claimed component | Reality |
|---|---|
| `BRAIN.md`, `memory/CURRENT_STATE.md` | existed |
| `ARCHITECTURE.md` | existed as `docs/ARCHITECTURE_V2.md` |
| `DECISION_LOG/` | existed as `decisions/` (0001–0005) |
| `PLAYBOOKS/` | existed as `examples/` (2 playbooks) |
| `KNOWLEDGE/` | **did not exist — and `BRAIN.md:125` routed to it** |
| `EVIDENCE/` | existed, flat and ad hoc, as `memory/sources/` |
| `CHECKLISTS/`, `GOLDEN_DATASET/` | did not exist |

So the framing "build the memory system" was wrong — most of it was already
built and maintained. But the *instinct* was right, and it caught a real defect:
**the router pointed at a directory that had never been created.** A session
following that link finds nothing and cannot distinguish "empty, nothing learned
yet" from "missing, the link is broken". That is the memory system failing in
precisely the way it exists to prevent, and it had been sitting there unnoticed
since `BRAIN.md` was written.

One verified defect beats an argument about sequencing. Accepted.

## Decision — finish the memory substrate now, IPS next

Not because "memory before code" is a law, but because of what these specific
gaps cost:

- Evidence handling was about to be **repeated**. M5 brings filings, macro data
  and prices — three more sources, each needing provenance. `memory/sources/`
  was a flat directory with one file and a hash copied into a config comment.
  Generalising it once, before three consumers exist, is cheap; retrofitting it
  across three is not.
- `knowledge/` being empty means every session re-derives the same EGX
  conventions, and — worse — re-derives them *from recall*, which is the one
  source this project does not accept. A cited page turns a recurring guess into
  a lookup.
- Checklists are where a procedure survives the session that invented it. The
  rebalance procedure existed only in one ADR paragraph and one comment.

## What was built

**`memory/evidence/<publisher>/<series>/<as_of>/`** — one directory per evidence
*item*, each with a `metadata.yaml`. Keyed by as-of date so a new vintage is a
new directory rather than an overwrite: the index rebalances, the CBE publishes
monthly, companies file quarterly, and overwriting evidence is how an audit
trail dies (R5).

**`store/evidence.py`** — reads a record, recomputes the digest from the actual
bytes, raises on mismatch. `find()` verifies before returning, so a caller
resolving an id from `config/` cannot receive unchecked bytes.
`tests/test_evidence.py` walks every committed record on every run.

**`knowledge/EGX_DATA_CONVENTIONS.md`** — the conventions this project has
actually verified, each with the file or evidence record it can be checked
against, and an explicit list of what is *not* written down because it has not
been verified.

**`checklists/`** — `NEW_EVIDENCE.md`, `INDEX_REBALANCE.md`, `SESSION_END.md`.

**`tests/golden/README.md`** — the contract, so the first real filing has
nothing left to decide. The set is still empty and says so.

**`research/transcribers/`** — `transcribe_index.py`, with room for the
`transcribe_pdf` / `transcribe_financials` / `transcribe_prices` siblings that
M5 needs.

## Rejected: a `sha256.txt` sidecar

The proposal included a hash file beside each evidence item. Declined, and the
reasoning generalises: **a fact recorded in two places is a fact that can
disagree with itself**, and at the moment it does, neither copy is trustworthy.

What replaces it is strictly stronger. The hash lives in `metadata.yaml` only,
and `store/evidence.py` recomputes it from the bytes on every test run. The
sidecar would have been a second transcription checked by nobody; the verifier
is a continuous check against reality. `config/universe.yaml` does still name
the hash — that duplication is deliberate, so the config is self-describing, and
it is covered by a test asserting the two agree.

## Rejected: `2026_Q2` as the evidence key

The proposal keyed the record by quarter. The workbook's own header says
`Weight as of 30/04/2026`, so `2026-04-30` is a fact parsed from the document
while `2026_Q2` is an interpretation layered on top of it — and an ambiguous one,
since 30 April is the last day of a fiscal Q1 under some conventions and inside
Q2 under others. Evidence directories are keyed by what the document says.

## Consequence

`NEXT_TASK.md` moves to M2 (IPS) with this substrate in place. The memory system
is not "done" — `knowledge/` has one page and the golden set is empty — but both
now have a home, a contract and a route from `BRAIN.md`, which is what was
missing.
