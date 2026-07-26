# Checklist — ending a session

`BRAIN.md` §4 states the discipline in prose: a future session with zero context
must be able to continue from `CURRENT_STATE.md` and `NEXT_TASK.md` alone. This
is that discipline as a list you can actually run.

The session is ephemeral. Anything not written down did not happen.

---

- [ ] **`uv run pytest -q` is green.** Not "green except". If something is red
      and you are stopping anyway, it goes in `KNOWN_ISSUES.md` as an open
      defect with what you know about it.

- [ ] **`uv run ruff check src tests` and `mypy` are clean.**

- [ ] **Rewrite `memory/CURRENT_STATE.md`** — rewrite, not append. Update the
      test count. Move anything newly built into the trustworthy table with an
      honest confidence rating. Move anything you *discovered* is not built
      into "What is NOT built".

- [ ] **Replace `memory/NEXT_TASK.md` with the single next task.** One task.
      If you found five things, the other four go to `FUTURE_PROPOSALS.md` or
      stay in the roadmap — a backlog in this file defeats its purpose.

- [ ] **Update "Blocked on the user"** with what you actually need and why. Be
      specific enough that they can act without asking a follow-up question.

- [ ] **Write a `decisions/NNNN-*.md`** for anything a future session might
      otherwise re-litigate or silently reverse. The test: would someone
      reading only the code think this was an oversight? Then it needs an ADR.

- [ ] **New defects → `KNOWN_ISSUES.md`.** New ideas → `FUTURE_PROPOSALS.md`,
      which does **not** change V1. New verified domain facts → `knowledge/`,
      with a citation.

- [ ] **Learned something about how the user wants to be served?** →
      `USER_PREFERENCES.md`. Only if it generalises — not for a one-off.

- [ ] **Commit and push.** The commit message carries the reasoning; the diff
      carries the change. Anything worth explaining to a reviewer belongs in
      the message, not in a comment nobody will find.

---

## The honesty pass

Before you write the summary, re-read what you are claiming:

- [ ] Did every number you are about to state come from a tool or a file?
      If you cannot point at where it came from, cut it (`BRAIN.md` §2).
- [ ] Is anything described as done that is actually untested, or tested only
      against fixtures? Say which.
- [ ] Did you leave any part of the task unfinished? Say so plainly and say
      why. Scaling the work down is the user's call, not yours.
