# NEXT_TASK.md

One task. Not a backlog. When it is done, replace this file with the next one.

---

## Now: M2 — Investment Policy Statement

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

## Done: M0 — the universe

`config/universe.yaml` is `POPULATED`: 34 listings / 33 issuers, script-transcribed from an
archived workbook, with `engine/universe.py` refusing to load anything unpopulated. See
`decisions/0005`. Do not hand-edit the constituent list — re-run
`research/transcribe_universe.py` when the next rebalance export arrives.

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
