# NEXT_TASK.md

One task. Not a backlog. When it is done, replace this file with the next one.

---

## Now: M0 — populate the investable universe (still blocked on the user)

`config/universe.yaml` has `constituents: []` **on purpose**. Everything else that
could be built without it now is.

**Task:** transcribe the EGX 33 Shariah Index constituent list from a primary source
into `config/universe.yaml`, then set the `retrieved` block (date, URL, who) and flip
`status` to `POPULATED`.

**The rule that governs this task:** never fill this list from memory or recall. Every
ticker must be read off a source open in front of you. Putting a company into a halal
universe on no evidence is the exact failure this project exists to prevent
(`CLAUDE.md` Prime Directive, R3).

**What changed this session:** the refusal is now enforced in code.
`engine/universe.py` raises `UniverseUnavailableError` rather than returning an empty
list, `parse_universe` refuses a partial transcription (33 declared, 30 transcribed),
and every tool that would screen relays the reason instead of reporting zero results.
So this task is now genuinely the only thing between the system and a live screening run.

**How to get it, in order of preference**
1. The user pastes or uploads the list — a screenshot is enough.
2. A session with network access fetches
   `https://www.egx.com.eg/en/currentindexconstituntes.aspx?type=22&nav=22`.
   ⚠️ 403 in this sandbox, along with every other host.
3. Mubasher or Investing.com constituent pages (also 403 here).

Full request, with every alternative source and what each one gives:
**`docs/DATA_REQUEST.md` §1.**

**Definition of done**
- 33 tickers with names (AR/EN where available) and sector
- `retrieved.at` and `retrieved.from` filled in
- `status` changed to `POPULATED`
- `tests/test_engine_universe.py` updated: it currently asserts the *shipped* file
  refuses, which will no longer be true — move that assertion onto a fixture and add
  one that the real file now loads and screens

---

## Then: M6 — the golden set

Blocked on the same person for a different reason: it needs 15 real filings with the
composition in `docs/DATA_REQUEST.md` §2. Nothing about extraction accuracy is knowable
until it exists, and until it passes at ≥99% on the 12 critical line items the agent
should screen and explain but not open a position on freshly extracted data.

## Then: M5 live adapters — blocked only by the environment

`research/` has the protocols, the source registry, the retry policy, the market
derivations and the discovery orchestration, all tested offline. What is missing is an
adapter that reaches a real source, which no code change in this sandbox can provide.
Write it in an environment with network access, or drive `UploadedFilingDiscovery` over
a directory of uploaded PDFs.

---

## Blocked on the user

1. **The EGX33 Shariah constituent list** (M0) — a screenshot.
2. **Thndr's fee schedule** — four numbers; unblocks all order sizing.
3. **2–3 real filing PDFs** for companies they might actually hold (M6).
4. **Their mandate** — capital, contribution, horizon, exclusions → `config/ips.yaml` v2.

`docs/DATA_REQUEST.md` is written for them, with links. Nothing else is blocked, and no
credentials are needed anywhere in V1.
