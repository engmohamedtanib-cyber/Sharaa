# 0001 — Claude subscription is the runtime, not the API

**Status:** Accepted · **Date:** 2026-07-25 · **Supersedes:** the API assumption in
`docs/ARCHITECTURE_V2.md` §3.1 (conversation plane) and `docs/BUILD_SPEC.md` §6 (cost model)

## Context

`ARCHITECTURE_V2` assumed an Anthropic API key for the extraction layer and for scheduled
headless routines. The user has a Claude subscription and does not want to pay per token
for a personal tool. `BUILD_SPEC` §6 estimated extraction as the only material recurring
cost — that cost is avoidable entirely.

## Decision

**The runtime is a Claude session working inside this repository.** Claude reads the repo,
reads filings, runs Python, runs tests, and writes files. There is no API client, no
hosted worker, and no per-token bill.

Concretely:
- Extraction happens **in-session**: Claude reads the filing and produces the structured
  candidate values, which are then handed to the *unchanged* deterministic validators.
- `ingestion/extract.py` keeps its `LLMClient` protocol. It is now satisfied by the
  session itself rather than an API client. The prompt contract, the strict parser, the
  dual-pass discipline and V9 reconciliation are **unchanged** — they were always about
  distrusting the model's output, and that need is identical.
- The repository is the memory. Static rules live in files, not in prompts.

## Consequences

**Good**
- Zero recurring cost.
- The whole system is inspectable and portable — it is files, not a service.
- No credentials to store, which also removes a class of security risk.

**Bad — and this must be stated honestly**
- **True proactive monitoring is not possible.** A subscription runtime executes only while
  a session is open. Nothing polls EGX at 3am. The "AI messages you first" experience in
  `ARCHITECTURE_V2` §2 is deferred (`memory/FUTURE_PROPOSALS.md` P1).
- V1 is therefore **user-initiated**: the user opens a session and says "check my
  portfolio" or "any new filings?" and the full pipeline runs then.
- Dual-pass extraction costs two reads of the same document within a session. Pass
  independence now comes from separate turns with different framing, and the passes must
  not see each other's output — which requires discipline, not just prompt design.

## Why not the alternatives

- **API + hosted worker:** genuinely enables 3am monitoring, but costs money the user does
  not want to spend on a personal tool, and adds a deployment surface to maintain for
  years. The proactivity is worth less than the simplicity at this stage.
- **Local cron on the user's machine:** the machine is off most of the time, and a silently
  dead cron job is exactly the "stopped reporting" failure mode we design against.

## What must not change because of this

Nothing in `CLAUDE.md`. The model reading a filing is still an untrusted source whose
output passes through V1–V10 and dual-pass reconciliation before any figure is stored. The
runtime changed; the distrust did not.
