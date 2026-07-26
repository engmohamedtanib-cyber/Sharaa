# FUTURE_PROPOSALS.md

Ideas deferred past V1. **Do not implement anything in this file.** The V1 architecture is
frozen (`docs/ARCHITECTURE_V2.md` §13); this is where good ideas wait so they stop causing
churn.

Each entry: what, why it was deferred, what would justify revisiting.

---

## P1 — Scheduled proactive monitoring
**What:** daily filing polls, weekly digests, alerting without the user asking.
**Deferred because:** a subscription runtime only executes while a session is running
(`decisions/0001`). Real autonomy needs either scheduled Claude Code sessions or an API
worker, and the latter costs money the user explicitly wants to avoid.
**Revisit when:** the user wants proactivity badly enough to accept scheduled sessions, or
when a session-scheduling primitive proves reliable over a month of use.

## P2 — Supabase / hosted Postgres
**What:** move storage from git-native JSONL to a managed database.
**Deferred because:** at this scale (one portfolio, hundreds of events, ~30 companies)
JSONL in git is simpler, diffable, free, and needs no credentials (`decisions/0002`).
**Revisit when:** multi-device concurrent writes are needed, or the event log passes
~100k rows and reading becomes slow.

## P3 — Dashboard / web UI
**What:** a visual portfolio view.
**Deferred because:** the product is conversation-first. A dashboard is a place the user
must remember to visit; a conversation comes to them.
**Revisit when:** a read pattern proves genuinely awkward in text (a five-year performance
chart is the likely first case).

## P4 — Telegram / WhatsApp surface
**What:** reach the user on their phone outside Claude.
**Deferred because:** requires a hosted worker and an API key. The agent plane is
surface-agnostic by design, so this costs nothing to defer.

## P5 — DCF valuation
**What:** discounted cash flow as a third fair-value method.
**Deferred because:** `ENGINE_SPEC` §5.4 argues it is usually false precision in a
12%-inflation, volatile-FX environment. Two methods with lower-of is more honest.
**Revisit when:** a holding has genuine multi-year cash-flow visibility (a utility or a
long-contract infrastructure name).

## P6 — Multi-currency / non-EGX holdings
**Deferred because:** the Shariah gate, the denominators, and the fee model are all
EGX/EGP-specific. Adding a market is a V2-sized change, not a feature.

## P7 — Automatic rights-issue handling
**What:** treat a rights issue like a split.
**Deferred because:** a rights issue requires a *cash decision* — it is a proposal, not an
automatic adjustment. Currently routed to the user as a normal plan, which is correct.

## P8 — Tax-lot accounting (FIFO/specific-lot)
**What:** replace weighted-average cost basis with per-lot tracking.
**Deferred because:** Egyptian individual CGT treatment does not currently make lot
selection advantageous, and weighted average matches the existing `positions.avg_cost`.
**Revisit when:** tax law changes, or the user asks about tax-loss harvesting.

## P9 — GitHub Spec Kit (spec-driven development toolkit)
**Link:** https://github.com/github/spec-kit · docs: https://github.github.com/spec-kit/
**Saved:** 2026-07-26, at the user's request, for use on **future** projects.

⚠️ **Second-hand.** This session could not open the repository — egress is blocked, and
GitHub access is scoped to `engmohamedtanib-cyber/sharaa` (`add_repo` refuses cross-owner
adds). Everything below comes from WebSearch result summaries, **not from reading the
source**. Verify before relying on any detail.

**What it reportedly is:** a toolkit for "Spec-Driven Development" — the specification is
the source of truth that generates the implementation, rather than documentation that
trails behind it. Workflow: a `constitution.md` of non-negotiable principles, then
`/specify` → `/plan` → `/tasks` → `/implement`, with `/analyze` as a consistency gate
across those artefacts.

**Why it is NOT being adopted into this project:**

This project already independently arrived at the same architecture, and in places went
further:

| Spec Kit concept | Already here |
|---|---|
| `constitution.md` — non-negotiable principles | `CLAUDE.md`, literally subtitled "Project Constitution"; R1–R8; "this file wins" |
| `/specify` — spec as source of truth | `docs/ENGINE_SPEC.md`, `docs/EXTRACTION_SPEC.md` |
| `/plan` — technical approach | `docs/ARCHITECTURE_V2.md`, `docs/ROADMAP_V1.md` |
| `/tasks` — the breakdown | `memory/NEXT_TASK.md` — deliberately **one** task, not a backlog |
| `/analyze` — consistency gate | `uv run pytest` (750 tests) + `decisions/` ADRs |

`CLAUDE.md` R8 — *"If you believe a rule in `ENGINE_SPEC.md` is wrong, say so and propose
the change. Do not implement your own version. The specification is the contract"* — is the
spec-driven thesis stated as a binding rule.

Retrofitting would mean re-expressing ~2 000 lines of working specification in another
tool's format, and running two constitutions at once, for no behavioural gain. The V1
architecture is frozen (`docs/ARCHITECTURE_V2.md` §13). Declining is the frozen-architecture
rule working as intended, not a judgement on the tool.

**The one idea worth stealing now:** `/analyze`, the *cross-artefact* consistency gate.
Tests prove the code is self-consistent. Nothing currently proves that `CURRENT_STATE.md`,
`NEXT_TASK.md`, `KNOWN_ISSUES.md`, `config/` and the specs still agree with each other —
that gap is checked only by a human noticing. A small `checklists/`-driven or scripted
consistency check would close it without adopting the toolkit. This is a real gap in the
memory system; it is recorded here rather than built, per the rules of this file.

**Revisit when:** starting a **new** project from zero — that is where it earns its keep,
because the constitution and specs get written in its format from the first commit instead
of being translated into it.
