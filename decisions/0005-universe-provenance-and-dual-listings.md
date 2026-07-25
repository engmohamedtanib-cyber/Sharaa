# 0005 — Universe provenance, and listings are not issuers

**Status:** Accepted · **Date:** 2026-07-25
**Question asked:** M0 said "transcribe the EGX 33 Shariah Index constituents from a
primary source." A source arrived. What exactly may we claim about it, and why does the
file hold 34 rows for an index called 33?

## What arrived

The user uploaded `EGX33ShariahIndexMay2026.xlsx`. It carries the column layout of the
Egyptian Exchange constituents export:

| SYMBOL_CODE | SYMB_NAME_ARB | SYMB_NAME_ENG | REUTERS_CODE | Weight as of 30/04/2026 |
|---|---|---|---|---|

34 data rows with Egyptian ISINs (`EGS…`, 12 characters) and `.CA` Reuters codes, plus a
totals row summing to exactly 1.

Archived at `memory/sources/EGX33-SHARIAH_constituents_2026-05.xlsx`,
sha256 `1ad43de…4470c`.

## Decision 1 — transcribe by script, never by hand

`research/transcribe_universe.py` reads the workbook and emits the `constituents:` block.
`config/universe.yaml` says *do not hand-edit* at the top, and a test regenerates the block
from the archived bytes and requires an exact match with what shipped.

**Why:** M0's binding rule was "never fill this list from memory or recall." A script is
the cheapest available *proof* that the rule was kept — anyone with the same workbook
reproduces the same diff, and a ticker typed from recall could not survive the round-trip
test. It is also the rebalance path: the index is reviewed periodically, and the next
export should re-run this rather than be merged by hand.

## Decision 2 — claim the bytes, not the URL

`retrieved.from` records the archived filename and states plainly that the origin URL was
**not independently verified**. Network egress is blocked in this environment
(`decisions/0004`), so this session did not fetch egx.com.eg and cannot attest that the
file came from there.

**Why:** the honest claim is the one we can defend. We can prove exactly which bytes the
list came from (hash, committed alongside), and that the transcription is faithful to them.
We cannot prove the chain of custody before the upload. Writing the EGX URL into
`retrieved.from` would have looked more rigorous while asserting something unverified —
the precise habit `CLAUDE.md`'s Prime Directive exists to break.

**Consequence:** if the user later confirms the download URL, or a session with network
access re-fetches and the hash matches, `retrieved.from` is upgraded and this ADR amended.
Until then the universe is usable but its provenance is one link short, and says so.

## Decision 3 — sector stays `null`, all 34 rows

The export has no sector column. Every `sector` is `null`, and a test asserts it.

**Why:** sector is not decorative — it drives the sector concentration cap in
`engine/portfolio.py`. Filling 34 sectors from recall would put unsourced facts into the
one file whose entire purpose is to hold only sourced ones, and they would then silently
shape position sizing. A `null` is a known gap that the engine can refuse on. A guess is an
unknown error that it cannot. This is R3 applied to a non-numeric field.

## Decision 4 — a listing is not an issuer

The index is named for **33 companies**; the file holds **34 listings**. The extra row is
Faisal Islamic Bank of Egypt, listed twice:

| Ticker | ISIN | Name (as printed in the source) |
|---|---|---|
| `FAIT` | EGS60321C014 | بنك فيصل الاسلامي المصرية بالجنية / Faisal Islamic Bank of Egypt - In EGP |
| `FAITA` | EGS60322C012 | بنك فيصل الاسلامي المصري - بالدولار / Faisal Islamic Bank of Egypt - In US Dollars |

Both rows name themselves, in both languages, as the same bank in two currencies. This is
transcription, not inference — no outside knowledge was needed to pair them.

So `Constituent.issuer_id` groups listings, `Universe.issuers` is the company count, and
`engine/universe.py` checks `index.constituent_count` against **distinct issuers**, not row
count. 33 issuers across 34 listings reconciles exactly, which is itself corroboration that
the file is the complete index and not a truncated view of it.

**Why it matters beyond bookkeeping:** concentration limits are a statement about *risk*,
and two currency classes of one bank carry one bank's risk. Counting them as two companies
would let a single issuer occupy two position slots and evade the per-company cap — a
diversification failure produced entirely by a clerical convention.

`DUAL_LISTINGS` in the transcriber is an explicit, evidence-quoting table, not a
name-similarity heuristic. A heuristic here would eventually merge two genuinely different
companies with similar names, which fails in the same silent direction.

## Decision 5 — an unpopulated universe raises; it never returns empty

`parse_universe` raises `UniverseUnavailableError` unless `status == POPULATED` **and** the
list is non-empty.

**Why:** "the universe has not been transcribed" and "the universe is loaded and nothing
passed screening" produce the identical empty list and demand opposite actions — *stop*
versus *proceed, nothing to buy today*. A caller that receives `[]` cannot tell them apart,
and the failure is silent and in the dangerous direction: a screening run that reports an
all-clear it never computed. Making the first case unrepresentable as a value is the only
structural fix; a comment asking callers to check `status` first is not.

`UniverseIntegrityError` is a separate class, and neither subclasses `ValueError`, so
neither can be swallowed by a broad `except ValueError` somewhere upstream.

## What was deliberately NOT enforced

- **The 15% single-stock cap.** Weights are a snapshot at the as-of date and drift with
  price between rebalances, so a published weight above the cap is a real state, not a
  corrupt file. Refusing to load the entire universe over legitimate drift would be a
  self-inflicted outage in the wrong direction — it blocks screening rather than
  permitting anything. Only impossible weights (≤0, >1) are rejected.
- **Weight sum ≈ 1 at load.** That check belongs in the transcriber, where the workbook's
  own totals row is available to check *against*. It is the check that catches a truncated
  or column-misaligned transcription, and it ran: 34 rows summed to the declared 1.

## Standing caveat

Index weights are stored but are **informational only** and are never an engine input.
`engine/portfolio.py` derives target weights from scores and constraints. If a future
change ever reads `index_weight` into a calculation, R2 and `ENGINE_SPEC` §7 are both
violated and this ADR is the record that it was not an oversight.
