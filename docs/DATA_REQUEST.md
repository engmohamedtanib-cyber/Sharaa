# DATA_REQUEST.md — every file the system needs from you, with links

**Written:** 2026-07-27 · **For:** the user, to collect outside this sandbox
**Why this file exists:** the engine is finished and tested. It has never seen a
real number. Everything below is the gap between "correct machine" and "running
machine", listed in the order that unblocks the most.

---

## Read this first — one honest warning about the links

I could not open any of these pages. Outbound HTTP is blocked in this
environment: `egx.com.eg`, `investing.com` and `mubasher.info` all return **403
from the egress proxy**, and so does every other host, including Wikipedia. That
is an environment policy, not those sites refusing us
(`memory/CURRENT_STATE.md`, verified again today).

So the links below come from web *search results* — titles and URLs — not from
pages I read. Treat them as **starting points that need one click to confirm**,
not as verified deep links. Where a URL pattern is a guess from a search result
title, it is marked ⚠️.

**What I have NOT done, and will not do:** fill in the constituent list from
memory. Not one ticker. Putting a company into a halal universe on no evidence
is the exact failure this whole project is built to make structurally difficult
(`CLAUDE.md`, Prime Directive and R3). Section 2's list is companies I need
*documents* for so the engine can judge them — it is **not** a claim that any of
them is Shariah-compliant or an index constituent.

---

## 1. PRIORITY ONE — the constituent list (unblocks everything)

Without this the system refuses to screen at all. It does not report "nothing is
compliant"; it reports "I have not looked" and stops (`engine/universe.py`).

**What I need:** the 33 constituents of the **EGX 33 Shariah Index** — ticker,
company name, and sector if the page shows it. A screenshot, a copy-paste, or a
CSV all work equally well.

| # | Source | Link | Note |
|---|---|---|---|
| 1 | EGX official index constituents | https://www.egx.com.eg/en/currentindexconstituntes.aspx?type=22&nav=22 | The primary source. `type=22` is the Shariah index. 403 from here. |
| 2 | FRA — Shariah-compliant index page | https://fra.gov.eg/en/ (search "مؤشر البورصة المتوافق مع أحكام الشريعة") | Regulator's own page on the index and its criteria |
| 3 | Mubasher — EGX33 Shariah | https://english.mubasher.info/markets/EGX/indices/SHARIAH | Constituent table, usually current |
| 4 | Investing.com — components | https://www.investing.com/indices/egx-33-shariah-compliant-index-components | Alternative table, same data |

**When you send it, I will:** transcribe it into `config/universe.yaml` with the
retrieval date and URL recorded, flip `status` to `POPULATED`, and the whole
screening layer comes alive. The loader refuses a partial transcription — if the
index says 33 and I have 30, it stops rather than screen a truncated list.

**Also useful, same trip:** the index **methodology / rulebook** PDF, if the page
links one. It tells me the exact rebalance dates, which is when a constituent can
silently drop out between our reviews.

---

## 2. PRIORITY TWO — filings, so extraction can be trusted

The golden set (`ROADMAP_V1` M6) is the gate on trusting extraction at all. Until
it passes, the agent may screen and explain, but should not be trusted to open a
position on freshly extracted data.

### What one "filing" means here

For each company: the **full financial statements PDF**, not the press release,
not the presentation. It must contain all four statements plus the notes —
especially the borrowings note and the other-income note, where the Shariah
screens actually live.

### The 12 line items every filing must yield (≥99% accuracy bar)

| Balance sheet | Income statement |
|---|---|
| total assets · cash and equivalents · time deposits · treasury bills and bonds · accounts receivable · short-term borrowings · long-term borrowings · bank overdraft · bonds payable | total revenue · interest income · net profit attributable to owners |

Plus one classification that decides Screen C on its own: **is each borrowing
line conventional or Islamic?** Murabaha, ijara, musharaka, mudaraba, sukuk are
excluded from the debt numerator; bank loans, overdrafts and bonds are not. If a
line cannot be classified from the note, the company goes `DATA_INSUFFICIENT` —
it does not get a default.

### Golden-set composition (15 filings minimum)

Not 15 of the same kind. The set has to contain the hard cases, because the hard
cases are where extraction quietly fails:

- ≥ 5 different issuers
- ≥ 3 **scanned** (image) PDFs, not text PDFs
- ≥ 2 **Arabic-only** filings
- ≥ 1 with **Islamic financing** on the balance sheet
- ≥ 1 with **interest income buried inside "other income"**
- ≥ 1 reported in **thousands** and ≥ 1 in **millions**
- ≥ 1 containing a **restatement** of a prior period

### Where to get them

| Route | Link | Best for |
|---|---|---|
| EGX financial statements feed | https://www.egx.com.eg/en/newslist.aspx?ID=15 | The exchange's own filing announcements — the record |
| EGX disclosure forms | https://www.egx.com.eg/en/Disclosure_forms.aspx | Disclosure filings by company |
| EGX bulletins (direct PDFs) | `https://www.egx.com.eg/downloads/Bulletins/{id}_{n}.pdf` ⚠️ | Statements are served from here; ids come from the pages above |
| Mubasher per-company statements | `https://english.mubasher.info/markets/EGX/stocks/{TICKER}/financial-statements` | Fast per-ticker access, AR + EN |
| Company IR page | see §2.1 | Usually the cleanest full-annual PDF |

### 2.1 Candidate companies — *for screening, not a compliance claim*

These are large, liquid EGX names whose filings would exercise the extractor
well. **I have not verified that any of them is in the Shariah index.** They are
here because the engine needs documents to judge, and these are the ones whose
documents are easiest for you to reach. If the constituent list (§1) contradicts
this list, the constituent list wins, always.

| Ticker | Company | Filings via | Why it is a useful test case |
|---|---|---|---|
| ABUK | Abu Qir Fertilizers | https://abuqir.net/investor-relations/financial-statements/ | Clean IR page; heavy cash and T-bill balances — exercises Screens D and E |
| SWDY | Elsewedy Electric | https://ir.elsewedyelectric.com/ | Large, complex group; FX and borrowings notes — exercises Screen C classification |
| ARCC | Arabian Cement | https://arabiancementcompany.com/investor-relations/ ⚠️ | Named in search results as a constituent; cement = capital-intensive, real debt |
| EAST | Eastern Company | https://www.easternegypt.com/ ⚠️ | Tobacco — I expect a Screen A activity failure, and I want to see the gate refuse it correctly |
| CIRA | Cairo Investment & Real Estate Development | https://cira-edu.com/investor-relations/ ⚠️ | Education; named in search results as a constituent |
| ORAS | Orascom Construction | https://www.orascom.com/investor-relations/ ⚠️ | Reports in USD — exercises currency handling and the EGP-only rule |
| FWRY | Fawry | https://fawry.com/investor-relations/ ⚠️ | Fintech; receivables-heavy — exercises Screen D |
| ISPH | Ibnsina Pharma | https://ibnsina-pharma.com/investor-relations/ ⚠️ | Distribution; huge receivables relative to market cap — a Screen D stress case |

⚠️ = URL inferred from a search result, not opened. One click confirms or corrects it.

**Priority within this list:** whichever 2–3 of these you would actually consider
holding. A golden set built from companies you will never own is a test suite for
someone else's portfolio.

---

## 3. PRIORITY THREE — market and macro data

Every market-dependent sub-criterion (4A, 4D, 6A–6D) currently scores
`MISSING_DATA` and awards zero. That is correct behaviour — scores are
*understated*, never inflated — but it means today's scores are structurally low.

### Per company

| What | Why it is needed | Source |
|---|---|---|
| **Daily close + market cap, 12 months** | Screens C, D and E divide by the **trailing 12-month average** market cap, never spot. Without ~245 daily observations the engine raises rather than average a short window | Mubasher, Investing.com, or a broker export |
| 60-day traded value | Liquidity check, position sizing | same |
| Shares outstanding | Market cap sanity check against the filing | filing cover page or EGX profile |

A CSV export from Thndr or any terminal is ideal: `date,close,volume,market_cap`.

### Macro (one set of numbers, not per company)

| Series | Used by | Source |
|---|---|---|
| CPI year-on-year | Pillar 5 — growth is deflated to real terms, so nominal EGP growth is not mistaken for performance | CAPMAS / CBE monthly bulletin |
| 1-year T-bill yield | Pillar 4 — the equity risk premium is measured against it | CBE auction results |
| Policy rate (overnight deposit) | Context for the above | https://www.cbe.org.eg/en/economic-research/statistics |
| USD/EGP | FX resilience scoring | CBE daily rates |

---

## 4. PRIORITY FOUR — the two numbers only you can get

These are small, and each one blocks a specific capability entirely.

| What | Blocks | Where |
|---|---|---|
| **Thndr's fee schedule** — commission %, minimum fee in EGP, levies %, tax % | **All order sizing.** `config/thresholds.yaml` has these as `null` on purpose, and the trade-cost gate refuses to size an order rather than guess. Tested: `propose_investment_plan` returns a refusal naming exactly these four fields | Thndr app → fees/pricing, or a past trade confirmation |
| **Your mandate** — capital, monthly contribution, horizon, any personal exclusions | Nothing hard-blocks, but every plan is generic until this exists. It becomes `config/ips.yaml` v2 | Just tell me, in a sentence |

---

## 5. How to send files so they land correctly

Upload into the repo (or attach in chat) using this exact naming, which the
upload discovery adapter parses (`research/offline.py`):

```
TICKER_FYyyyy_PERIOD.pdf

ABUK_FY2025_FY.pdf     ← full-year 2025
ABUK_FY2026_H1.pdf     ← first half 2026
SWDY_FY2025_9M.pdf     ← nine months 2025
```

`PERIOD` ∈ `Q1 | H1 | 9M | FY`. A filename that does not parse is **skipped and
named**, never guessed — a filing filed under the wrong period is worse than one
not filed at all. EGX interim reporting is cumulative, so Q2 standalone is
derived as `H1 − Q1` by the engine; do not try to send a "Q2" file.

Market and macro data: any CSV, any column order, just say which is which.

---

## 6. The one-line version

**If you only do one thing:** send the EGX 33 Shariah constituent list (§1). It
is a screenshot, and it unblocks the entire screening layer.

**If you do two:** add the Thndr fee schedule (§4). It is four numbers, and it
unblocks order sizing.

**If you do three:** add 2–3 annual reports for companies you would actually
hold (§2.1). That starts the golden set, and the golden set is what makes
extraction trustworthy enough to act on.

---

### Sources for the links in this document

- [EGX — Financial Statements feed](https://www.egx.com.eg/en/newslist.aspx?ID=15)
- [EGX — Disclosure forms](https://www.egx.com.eg/en/Disclosure_forms.aspx)
- [EGX — index constituents (Shariah, type=22)](https://www.egx.com.eg/en/currentindexconstituntes.aspx?type=22&nav=22)
- [FRA — Shariah-compliant stock market index](https://fra.gov.eg/en/)
- [Mubasher — EGX33 Shariah index](https://english.mubasher.info/markets/EGX/indices/SHARIAH)
- [Investing.com — EGX 33 Shariah components](https://www.investing.com/indices/egx-33-shariah-compliant-index-components)
- [Abu Qir Fertilizers — financial statements](https://abuqir.net/investor-relations/financial-statements/)
- [Elsewedy Electric — investor relations](https://ir.elsewedyelectric.com/)
- [Mubasher — Elsewedy Electric statements](https://english.mubasher.info/markets/EGX/stocks/SWDY/financial-statements)
- [CBE — statistics](https://www.cbe.org.eg/en/economic-research/statistics)
