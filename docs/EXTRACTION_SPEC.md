# EXTRACTION_SPEC.md — Ingestion, Extraction and Validation

> This is the hardest part of the system and the only place where an LLM touches a financial number. Everything downstream assumes this layer either produced a correct value or refused to produce one.

---

## 1. Principle

**Extraction is allowed to fail. Extraction is not allowed to be wrong.**

Every design decision below trades recall for precision. A missed company costs an opportunity. A wrong compliance verdict costs something that cannot be recovered by a later correction.

---

## 2. Pipeline

```
 discover → download → hash → dedupe → archive
     ↓
 classify (text-layer vs scanned)
     ↓
 text extraction  ──or──  OCR (tesseract -l ara+eng, deskewed)
     ↓
 normalise (Arabic digits, unit scale, sign conventions)
     ↓
 EXTRACTION PASS 1  ──┐
 EXTRACTION PASS 2  ──┴─→ agreement check (V9)
     ↓
 deterministic validation (V1–V8)
     ↓
 VALIDATED  ──or──  DATA_INSUFFICIENT
```

Passes 1 and 2 must be genuinely independent:
- Different prompt framing (Pass 1: statement-by-statement; Pass 2: line-item-by-line-item lookup)
- Different page-chunking strategy
- Neither pass sees the other's output
- Same model is acceptable; different framing is what creates independence

---

## 3. Line Item Dictionary

Canonical keys live in `config/line_items.yaml` with Arabic and English caption synonyms. Egyptian issuers vary caption wording considerably, so synonym lists must be extensible without code changes.

### 3.1 Critical items — extraction accuracy must be ≥99%

Failure to extract any of these correctly invalidates a screen.

| Key | Statement | Used by |
|---|---|---|
| `total_assets` | BS | Screens C2, D2, E2; Pillar 3A |
| `cash_and_equivalents` | BS | Screen D |
| `time_deposits` | BS | Screen D |
| `treasury_bills_and_bonds` | BS | Screen D |
| `accounts_receivable` | BS | Screen E; Pillar 3B |
| `short_term_borrowings` | BS | Screen C |
| `long_term_borrowings` | BS | Screen C |
| `bank_overdraft` | BS | Screen C |
| `bonds_payable` | BS | Screen C |
| `total_revenue` | IS | Screens A, B; Pillars 3C, 5A |
| `interest_income` | IS | **Screen B — the highest-risk item in the system** |
| `net_profit_attributable` | IS | Purification; Pillars 2D, 3A |

### 3.2 The Islamic-vs-conventional financing split

The single most consequential distinction. `config/line_items.yaml` must define both:

```yaml
conventional_debt_captions:
  ar: ["قروض بنكية", "تسهيلات ائتمانية", "سحب على المكشوف",
       "قروض طويلة الأجل", "سندات", "أوراق دفع"]
  en: ["bank loans", "credit facilities", "overdraft",
       "long-term loans", "bonds", "notes payable"]

islamic_financing_captions:
  ar: ["مرابحة", "إجارة", "مشاركة", "مضاربة", "صكوك",
       "تمويل إسلامي", "استصناع", "سلم"]
  en: ["murabaha", "ijara", "musharaka", "mudaraba", "sukuk",
       "islamic financing", "istisna", "salam"]
```

**Rule:** Islamic financing is excluded from Screen C's numerator. Misclassifying murabaha as conventional debt produces a **false non-compliance** — less dangerous than the reverse, but it will silently remove good companies from your universe.

**Ambiguity rule:** if a borrowing line cannot be confidently classified as either, mark the filing `DATA_INSUFFICIENT`. Do not default either way.

### 3.3 Interest income — mandatory note-level extraction

Egyptian income statements frequently aggregate interest income into `إيرادات أخرى` / "other income" without breaking it out on the face of the statement. **Extraction must go to the notes.**

Procedure:
1. Locate the interest income line on the income statement face.
2. Locate `other income` / `إيرادات أخرى` and its note reference.
3. Open that note and extract the components.
4. Sum: explicit interest income + interest components within other income + income from conventional investments.
5. **If `other_income` is material (>2% of revenue) and its note cannot be located or parsed → `DATA_INSUFFICIENT`.**

This rule exists because at ~19% policy rates, treasury income is the most common cause of Shariah breach on EGX, and it is precisely the item most likely to be hidden in aggregation. Missing it is the system's worst realistic failure mode.

### 3.4 Segment revenue

Required for Screen A when a company has any potentially non-permissible activity. Extract from the segment note. If a company operates in a sector where prohibited activity is plausible and no segment note exists → `DATA_INSUFFICIENT`.

### 3.5 Supporting items

`inventory`, `total_current_assets`, `total_current_liabilities`, `total_equity`, `shares_outstanding`, `cost_of_sales`, `gross_profit`, `operating_profit`, `finance_cost`, `fx_gain_loss`, `other_income`, `share_of_associates`, `profit_before_tax`, `income_tax`, `eps_basic`, `operating_cash_flow`, `capex`, `dividends_paid`, `closing_cash`, `lease_liabilities_finance`, `related_party_revenue`, `auditor_opinion_type`, `fx_net_exposure`.

---

## 4. Period Handling

**EGX interim statements are cumulative.** Q1 covers 3 months, H1 covers 6, 9M covers 9, FY covers 12.

Derive standalone quarters:

```
Q1_standalone = Q1_reported
Q2_standalone = H1_reported − Q1_reported
Q3_standalone = 9M_reported − H1_reported
Q4_standalone = FY_reported − 9M_reported
```

Balance sheet items are point-in-time — **never** subtract them. Only income statement and cash flow items are cumulative.

Derived values are stored with `is_derived = TRUE` and a `derivation` string. If the prior cumulative filing is missing, the standalone quarter cannot be derived — mark it unavailable rather than approximating.

---

## 5. Normalisation

### 5.1 Arabic-Indic digits
Convert `٠١٢٣٤٥٦٧٨٩` → `0123456789` before any parsing. Also handle Eastern Arabic-Indic `۰۱۲۳۴۵۶۷۸۹`. Missing this produces silent parse failures or, worse, partial numbers.

### 5.2 Unit scale
Statement headers declare the scale — `بالألف جنيه مصري` (EGP thousands), `بالمليون` (millions), or units. Detect from the header, store in `filings.reported_scale`, and normalise all values to EGP units at insert.

**This is the highest-frequency extraction error.** A ×1000 mistake makes every ratio wrong while leaving the balance sheet internally consistent — so V1 will not catch it. V7 exists specifically for this.

### 5.3 Sign conventions
Negatives appear as `(1,234)`, `-1,234`, `1,234-`, or `(١٢٣٤)`. Normalise all to a leading minus. Expenses may be presented as positive with an implied subtraction — resolve using the arithmetic chain, not the sign alone.

### 5.4 Thousands separators
Both `1,234,567` and `1.234.567` occur. Also Arabic comma `١٬٢٣٤`. Disambiguate decimal separator from thousands separator using position and the statement's stated precision.

---

## 6. The Nine Validators

All deterministic. No LLM. `validation/` may not make a network call.

| ID | Check | Rule | Critical? |
|---|---|---|---|
| **V1** | Balance sheet identity | `abs(total_assets − (total_liabilities + total_equity)) / total_assets < 0.005` | ✅ |
| **V2** | Income statement chain | `revenue − cost_of_sales = gross_profit` and the chain down to `net_profit` reconciles within 0.5% | ✅ |
| **V3** | Cash flow tie-out | `closing_cash` in CF equals `cash_and_equivalents` in BS within 0.5% | ✅ |
| **V4** | Component sums | `short_term + long_term + overdraft + bonds = total_borrowings` (where total is disclosed) | ✅ |
| **V5** | Comparative continuity | Prior-period comparatives printed in this filing match values stored from the prior filing within 0.1%. Mismatch → flag as possible restatement, **never auto-overwrite history** | ✅ |
| **V6** | Cumulative monotonicity | Cumulative revenue and cumulative OCF are non-decreasing across Q1 → H1 → 9M → FY within the same fiscal year (allowing for genuinely negative quarters, which are flagged not failed) | ⚠️ |
| **V7** | Magnitude plausibility | `revenue` within 3 orders of magnitude of `market_cap`; `total_assets > 0`; `shares_outstanding` consistent with `market_cap / price` within 5% | ✅ |
| **V8** | Segment reconciliation | Sum of segment revenues equals `total_revenue` within 1% (when a segment note exists) | ⚠️ |
| **V9** | Dual-extraction agreement | For every critical item: `abs(p1 − p2) / max(abs(p1), abs(p2)) < 0.001`. Any critical disagreement → `CONFLICT` | ✅ |

**Outcome rules:**
- Any **critical** validator fails → `filings.data_status = 'INSUFFICIENT'`
- V9 disagreement on a critical item → `data_status = 'CONFLICT'` (routed to exception queue)
- Non-critical failure → `VALIDATED` with a recorded warning
- V7 catching a suspected scale error should trigger one automatic re-extraction with the scale hypothesis corrected, then re-validate. If still failing, `INSUFFICIENT`.

**Staleness check (V10, operational):** if `NOW() − filings.period_end > 180 days` and no newer filing exists, mark the company `DATA_INSUFFICIENT`. A company that has stopped reporting must not drive live decisions.

---

## 7. Extraction Prompt Requirements

The extraction prompt must:

1. Require a **page number for every value returned**. A value without a page number is rejected before it reaches the database.
2. Require the **raw caption exactly as printed**, in the original language, alongside the canonical key.
3. Require the **raw string** as printed, before normalisation.
4. **Forbid inference.** Explicit instruction: if a line item is not present in the document, return `null` — never compute it, never infer it from other lines, never use knowledge of the company.
5. **Forbid cross-filing knowledge.** The model sees only this document.
6. Return strict JSON, no prose, no markdown fences.
7. Include the statement header text so the unit scale can be verified independently of the model's own scale interpretation.

Expected response shape:

```json
{
  "unit_scale_header_text": "بالألف جنيه مصري",
  "items": [
    {
      "key": "total_assets",
      "raw_caption": "إجمالي الأصول",
      "raw_value": "12,345,678",
      "page_no": 4,
      "note_ref": null,
      "statement": "BALANCE_SHEET"
    }
  ],
  "not_found": ["treasury_bills_and_bonds"]
}
```

The `not_found` array is as important as `items`. An explicit "not present" is information; a silent omission is ambiguity.

---

## 8. Exception Queue Behaviour

Filings marked `INSUFFICIENT` or `CONFLICT` appear in `v_exception_queue`.

**Default behaviour is to leave them there.** An unresolved exception means the company is excluded from the investable universe that quarter. This is safe and requires no action.

Optional manual resolution writes to a separate `manual_overrides` path that is audit-logged with the resolver's identity and reasoning, and never silently merges into `line_items` — the override is a distinct, visible object.

---

## 9. Golden Set Protocol

Maintain `tests/golden/` containing hand-verified filings and their expected extracted values.

**Composition requirements:**
- Minimum 15 filings, minimum 5 distinct issuers
- At least 3 scanned documents
- At least 2 Arabic-only documents
- At least 1 with Islamic financing on the balance sheet
- At least 1 where interest income is buried in "other income"
- At least 1 in EGP thousands and 1 in EGP millions
- At least 1 with a restatement of comparatives

**Rules:**
- Every extraction change runs against the golden set before merge
- Every production bug becomes a new golden file
- Accuracy below 99% on critical items blocks the merge
