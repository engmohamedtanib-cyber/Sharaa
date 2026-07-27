# NEXT_TASK.md

One task. Not a backlog. When it is done, replace this file with the next one.

---

## Now: close out ETEL, then take a second company through the same path

ETEL was screened end to end on 2026-07-26 and **fails Screen C at 61.87% against a 30%
limit** (`tests/golden/ETEL/2026-Q1/expected.yaml`). Two things still hold it at
`DATA_INSUFFICIENT` rather than a clean, citable breach, and both are cheap to close:

1. **Share count before 31/12/2025 is unverified.** The FY2025 annual report states capital
   at both year ends and settles it. Neither outcome can overturn Screen C — a share count
   large enough to pass would have to be 44% higher than reported.
2. **Screen B has no numerator.** The condensed interim does not disclose interest income;
   the full annual notes are expected to. If they do not, Screen B is structurally
   uncomputable for this company and that is itself a finding worth recording.

Then run a second constituent through the identical path. ETEL is one company and one
industry; a telecom carrying heavy conventional debt is not evidence about the other 32.
Pick one where the answer is not obviously predetermined.

## Then: M2 — Investment Policy Statement

Build `engine/policy.py` + `config/ips_schema.yaml`. Versioned, append-only, and it may
only **add** exclusions — a policy that would admit a gate-failing company is rejected at
load (R7). Full brief in `docs/ROADMAP_V1.md` M2.

**Success criteria** (from the roadmap, restated so this file stands alone)
- An IPS that would admit a Shariah-gate-failing company is rejected at load.
- Personal exclusions are additive-only, never subtractive.
- Every decision row carries the `policy_version` in force.
- Changing policy creates a new version; history is never edited.

**Scope discipline:** anything that does not change a *decision* is not policy — it is
presentation, and belongs in the persona. Resist the drift into "preferences."

**What M2 needs from the user, when you reach the part that needs it:** investment horizon,
contribution schedule, liquidity needs, drawdown tolerance, and any personal exclusions
beyond the Shariah gate. `memory/USER_PREFERENCES.md` lists these as not-yet-known. Do not
assume them — build the schema and the enforcement first; they are inputs, not blockers.

---

Before writing `engine/policy.py`, read `checklists/SESSION_END.md` once — it is the
discipline that keeps this file worth reading.

---

## Done: M0 — the universe, and the memory substrate

`config/universe.yaml` is `POPULATED`: 34 listings / 33 issuers, script-transcribed from
evidence record `egx/shariah_index/2026-04-30`, with `engine/universe.py` refusing to load
anything unpopulated (`decisions/0005`). Do not hand-edit the constituent list — follow
`checklists/INDEX_REBALANCE.md`.

The memory system was then completed before starting M2 (`decisions/0006`): `knowledge/`
now exists (`BRAIN.md` had been routing to a directory that was never created),
`memory/evidence/` is a registry whose hashes are recomputed from the bytes on every test
run, and `checklists/` holds the recurring procedures.

---

## Blocked on the user

- **Sector classification for the 33 issuers.** Every `sector` is `null`; the constituents
  export has no sector column. Until a primary source is opened, the sector concentration
  cap in `engine/portfolio.py` has nothing to bite on. A screenshot of the EGX or Mubasher
  sector listing is enough. **Do not fill these from recall** (`decisions/0005`).
- **Where the price list came from.** The user pasted 34 EGX quotes dated 22/07 alongside
  the workbook; it does not match the index (10 non-constituents, 10 constituents missing)
  and nothing from it was stored. Ask before treating any of it as market data.
- **1–3 real filing PDFs** for companies in the index (M5 golden set). Prefer the most
  recent annual report of a company they might actually hold.

Nothing else is blocked. No credentials are needed anywhere in V1.
