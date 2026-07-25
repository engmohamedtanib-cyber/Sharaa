# NEXT_TASK.md

One task. Not a backlog. When it is done, replace this file with the next one.

---

## Now: M2 — Investment Policy Statement

Build `engine/policy.py` and wire it in.

**Why this is next:** every decision must record the mandate it was made under. Retrofitting
policy after the conversational layer exists is far more expensive than building it first.

**What to build**
1. `config/ips_schema.yaml` — shape + safe defaults for a policy.
2. `engine/policy.py` — typed, immutable `Policy` with:
   - versioning (`policy_version`, `effective_from`, `adopted_reason`)
   - objective, horizon, contributions, liquidity reserve
   - personal exclusions (sectors, tickers)
   - purification basis and cadence
   - `is_excluded(ticker, sector) -> bool`
   - `investable_cash(state) -> Decimal` (total cash minus liquidity reserve)
3. **The rule that matters:** policy may only *add* exclusions. A policy that tries to
   admit a company failing the Shariah gate must be **rejected at load** (R7).
4. `store/` persistence for policy versions (append-only JSONL, same pattern as ledger).
5. Tests: 100% branch coverage; explicit test that a gate-loosening policy is refused.

**Definition of done**
- `uv run pytest` green
- `uv run ruff check .` clean
- `uv run mypy src/engine src/validation src/ingestion src/reporting` clean
- `memory/CURRENT_STATE.md` updated, committed and pushed

**Do not** start M3 (tool API) in the same session. One milestone per session keeps the
memory files honest.

---

## After this (do not start yet)

M3 tool API → M4 live data adapters → M5 golden set (needs real PDFs from the user)
→ M6 routines → M7 CIO persona. See `docs/ROADMAP_V1.md`.

## Blocked on the user

- **Real EGX filing PDFs** (3–5 to start) — gates all extraction trust.
- Nothing else. No credentials are needed for M2–M4.
