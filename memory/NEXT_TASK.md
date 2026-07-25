# NEXT_TASK.md

One task. Not a backlog. When it is done, replace this file with the next one.

---

## Now: M0 — populate the investable universe

`config/universe.yaml` exists but `constituents: []` is **empty on purpose**.

**Task:** transcribe the EGX 33 Shariah Index constituent list from a primary source into
`config/universe.yaml`, then set the `retrieved` block (date, URL, who).

**The rule that governs this task:** never fill this list from memory or recall. Every
ticker must be read off a source that is open in front of you. Putting a company into a
halal universe on no evidence is the exact failure this project exists to prevent
(`CLAUDE.md` Prime Directive, R3). If you cannot open a source, leave it empty and say so.

**How to get it, in order of preference**
1. The user pastes or uploads the list (screenshot, CSV, or copied text).
2. A session with working network access fetches
   `https://www.egx.com.eg/en/currentindexconstituntes.aspx?type=22&nav=22`.
   ⚠️ In the current sandbox this returns **403** — see `decisions/0004`.
3. Mubasher or Investing.com constituent pages (also 403 here).

**Definition of done**
- 33 tickers with names (AR/EN where available) and sector
- `retrieved.at` and `retrieved.from` filled in
- `status` changed from `AWAITING_CONSTITUENTS` to `POPULATED`
- A loader + test proving the engine refuses to run against an empty universe rather than
  treating it as "no compliant companies"

---

## Then: M2 — Investment Policy Statement

Build `engine/policy.py` + `config/ips_schema.yaml`. Versioned, append-only, and it may
only **add** exclusions — a policy that would admit a gate-failing company is rejected at
load (R7). Full brief in `docs/ROADMAP_V1.md` M2.

M2 needs nothing external and can proceed in parallel if M0 is blocked on the user.

---

## Blocked on the user

- **The EGX33 Shariah constituent list** (M0) — a screenshot or copy-paste is enough.
- **1–3 real filing PDFs** for companies in that list (M5 golden set). Prefer the most
  recent annual report of a company they might actually hold.

Nothing else is blocked. No credentials are needed anywhere in V1.
