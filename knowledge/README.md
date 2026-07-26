# knowledge/ — distilled operational rules

`BRAIN.md` has routed here since it was written. The directory did not exist
until 2026-07-26; the link was dead. That is worth recording rather than quietly
fixing, because a router pointing at nothing is the failure mode this whole
memory system is meant to prevent — a future session followed the link, found
nothing, and had no way to tell "empty" from "missing".

---

## What belongs here

Facts about **the domain** that are expensive to re-derive and stable across
sessions: how EGX reports, how the market's conventions differ from the ones a
model would assume by default, what a caption means in an Egyptian filing.

The test for admission is not "is it true?" — it is:

> **Can I point at where this came from, in this repository or in an evidence
> record?**

Every claim in this directory carries a citation. A file here with an uncited
sentence in it is worse than no file, because the next session will trust it and
will have no way to check it.

## What does not belong here

| Not here | Where instead |
|---|---|
| Why we chose an approach | `decisions/` |
| What is true right now | `memory/CURRENT_STATE.md` |
| How to perform a recurring task | `examples/` (playbooks), `checklists/` |
| A number the engine uses | `config/` — thresholds are configuration (R4) |
| Anything recalled but unverified | nowhere. It stays out. |

That last row is the one that matters. A model's recollection of an EGX
convention is not knowledge, it is a prior. Priors are useful for deciding
*where to look*; they are never a source. If a fact would change a stored figure
and cannot be cited, it does not get written down — it gets checked, and if it
cannot be checked, the thing that depends on it is excluded (R3).

## Contents

| File | What it holds |
|---|---|
| `EGX_DATA_CONVENTIONS.md` | Conventions of Egyptian market data, each cited |
