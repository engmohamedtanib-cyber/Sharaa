# ENGINE_SPEC.md — Deterministic Screening, Scoring and Decision Engine

> **Zero LLM calls in this layer.** Every function is pure: inputs in, value out. No I/O, no clock reads, no randomness. Two runs on identical input must produce identical output.

---

## 1. Configuration

All numbers below live in `config/thresholds.yaml`, versioned. Every screening and scoring row stores the `threshold_version` used, so historical results remain reconstructible after a threshold change.

```yaml
version: "1.0.0"
last_verified: "2026-07-24"
governing_standard: "AAOIFI"
denominator_convention: "MCAP_AVG_12M"   # see §2.6
```

---

## 2. Shariah Gate

### 2.1 Screen A — Business Activity

```
non_permissible_revenue_ratio = non_permissible_revenue / total_revenue
threshold = 0.05
```

`non_permissible_revenue` is summed from segment revenue matching `config/prohibited_activities.yaml`: conventional banking, conventional insurance, alcohol, tobacco, pork, gambling, adult content, conventional weapons, non-compliant entertainment, interest-based leasing/factoring/consumer finance/microfinance, conventional asset management.

**Core-activity override:** if the company's primary sector classification is itself prohibited, the gate fails immediately regardless of ratio. The 5% tolerance applies only to incidental revenue in an otherwise permissible business. Encode prohibited sectors as a hard exclusion list checked before any ratio computation.

### 2.2 Screen B — Impure Income

```
impure_income = interest_income
              + income_from_conventional_investments
              + other_non_permissible_income

impure_income_ratio = impure_income / total_revenue
threshold = 0.05
```

Denominator is total revenue **including** other income (conservative convention). Never change convention mid-series.

### 2.3 Screen C — Debt

```
interest_bearing_debt = short_term_borrowings
                      + long_term_borrowings
                      + bank_overdraft
                      + bonds_payable
                      + lease_liabilities_finance
                      − islamic_financing        # excluded

debt_ratio = interest_bearing_debt / denominator
threshold = 0.30                                  # AAOIFI
```

### 2.4 Screen D — Liquidity / Interest-Bearing Assets

```
interest_bearing_assets = cash_and_equivalents
                        + time_deposits
                        + treasury_bills_and_bonds
                        + conventional_money_market_holdings

liquidity_ratio = interest_bearing_assets / denominator
threshold = 0.30                                  # AAOIFI
```

Exclude sukuk, Islamic current accounts, and gold from the numerator.

### 2.5 Screen E — Receivables

```
receivables_ratio = accounts_receivable / denominator
threshold = 0.49
```

### 2.6 Denominator Convention

```
denominator = market_data.mcap_avg_12m
```

Trailing 12-month average market capitalisation, not spot.

**Rationale:** a spot market-cap denominator makes compliance a function of share price. A 40% price fall raises the debt ratio by ~67% with no corporate action, mechanically forcing a sale into a decline. DJIM and S&P use 24- and 36-month averages for exactly this reason; 12 months is the chosen middle ground for EGX's volatility.

Also compute and store total-asset-basis ratios (`debt / total_assets`, `interest_bearing_assets / total_assets`) as a secondary reference. They are not used for the verdict but are required for the Type 3 breach diagnostic (§3.2).

### 2.7 Status Classification

```
utilisation = ratio / threshold
```

| Status | Utilisation |
|---|---|
| `GREEN` | ≤ 0.65 |
| `AMBER` | > 0.65 and ≤ 0.85 |
| `ORANGE` | > 0.85 and ≤ 1.00 |
| `RED` | > 1.00 |

```
overall_status = worst(status(A), status(B), status(C), status(D), status(E))
```

**Worst, never average.** Averaging across screens is how a non-compliant position gets rationalised into a portfolio.

---

## 3. Breach Typing and Cure Windows

### 3.1 Types

| Type | Condition | Action |
|---|---|---|
| `TYPE_1_ACTIVITY` | Screen A fails, or core business becomes prohibited | Immediate exit. No cure. Target ≤5 trading sessions. |
| `TYPE_2_STRUCTURAL` | Screen B/C/D/E fails and the **numerator** drove it | Reduce 50% now; full exit by next review unless cured. |
| `TYPE_3_DENOMINATOR` | Screen C/D/E fails and the **denominator** drove it | One-quarter cure window. Position frozen — no adds, no sells. |
| `TYPE_4_DATA` | `data_status ≠ VALIDATED`, or filing older than 180 days | Treat as ORANGE, frozen. Two consecutive reviews unresolved → exit. |

### 3.2 Type 2 vs Type 3 Diagnostic

```
numerator_delta_pct   = (numerator_t   − numerator_t-1)   / numerator_t-1
denominator_delta_pct = (denominator_t − denominator_t-1) / denominator_t-1

if abs(numerator_delta_pct) > abs(denominator_delta_pct):
    breach = TYPE_2_STRUCTURAL
else:
    breach = TYPE_3_DENOMINATOR
```

Both deltas are stored on `shariah_status_history` so the classification is auditable.

### 3.3 Cure Window

Opens at the review that detects a Type 3 breach; expires at the next scheduled review. Still breached at expiry → reclassify as `TYPE_2_STRUCTURAL` and exit. The window is finite by construction; it cannot be extended by the engine.

### 3.4 Newly-Compliant Admission Test

A company moving `RED → GREEN/AMBER` is admitted **only if** the improvement was numerator-driven, or denominator-driven via genuine total-asset growth. Compliance restored purely by a share-price rally is rejected — status recorded, but the company stays out of the investable universe until a real improvement occurs.

### 3.5 Compliance Streak

```
consecutive_compliant_quarters = count of consecutive prior filings
                                 with overall_status in (GREEN, AMBER)
```

Feeds scoring sub-criterion 1E.

---

## 4. Scoring Model — 100 Points

**Gate first.** Only companies with `overall_status ∈ {GREEN, AMBER}` and `data_status = VALIDATED` are scored. All others return `None`, not zero.

**Missing-data rule:** any sub-criterion whose inputs are unavailable scores **0** and records `band_matched = 'MISSING_DATA'`. Never estimate.

**Band matching:** bands are evaluated top-down; the first matching band wins. Boundary values belong to the more generous band (`≤` semantics as written).

### 4.1 Pillar 1 — Shariah Headroom (25)

| Sub | Pts | Metric | Bands |
|---|---|---|---|
| 1A | 8 | Screen A ratio | `0.0000`→8 · `≤0.005`→7 · `≤0.015`→5 · `≤0.025`→3 · `≤0.040`→1 · `≤0.050`→0.5 |
| 1B | 7 | Screen C utilisation | `≤0.30`→7 · `≤0.45`→6 · `≤0.60`→4.5 · `≤0.75`→3 · `≤0.90`→1.5 · `≤1.00`→0.5 |
| 1C | 5 | Screen D utilisation | `≤0.30`→5 · `≤0.45`→4 · `≤0.60`→3 · `≤0.75`→2 · `≤0.90`→1 · `≤1.00`→0 |
| 1D | 3 | Screen E utilisation | `≤0.40`→3 · `≤0.60`→2 · `≤0.80`→1 · `≤1.00`→0 |
| 1E | 2 | Streak + trend of worst ratio | `≥8 quarters AND trend improving/flat`→2 · `≥4 stable`→1.5 · `≥4 deteriorating`→0.75 · `<4`→0 |

Trend for 1E: linear slope of the worst screen's utilisation over the last 4 filings. Slope ≤ 0 is improving/flat.

### 4.2 Pillar 2 — Financial Strength (20)

| Sub | Pts | Metric | Bands |
|---|---|---|---|
| 2A | 5 | Net debt / EBITDA | net cash→5 · `<1.0`→4 · `<2.0`→3 · `<3.0`→1.5 · `<4.0`→0.5 · else 0 |
| 2B | 4 | EBIT / finance cost | no finance cost→4 · `>10`→4 · `>6`→3 · `>3`→2 · `>1.5`→1 · else 0 |
| 2C | 4 | Current & quick ratio | `CR>2.0 AND QR>1.2`→4 · `CR>1.5 AND QR>1.0`→3 · `CR>1.2`→2 · `CR>1.0`→1 · else 0 |
| 2D | 4 | OCF / net income (TTM) | `>1.2`→4 · `>1.0`→3.5 · `>0.8`→2.5 · `>0.6`→1 · else 0 |
| 2E | 3 | FX resilience | net FX asset or natural hedge→3 · balanced→2 · net FX liability `<20%` of equity→1 · else 0 |

```
net_debt = interest_bearing_debt + islamic_financing − cash_and_equivalents − time_deposits
EBITDA   = operating_profit + depreciation + amortisation
quick_ratio = (current_assets − inventory) / current_liabilities
```

2E inputs come from the FX exposure note. If absent → 0 and `MISSING_DATA`. This is deliberate: unhedged FX liability has repeatedly destroyed Egyptian corporate earnings during devaluations, and non-disclosure is itself a negative signal.

### 4.3 Pillar 3 — Earnings Quality (15)

| Sub | Pts | Metric | Bands |
|---|---|---|---|
| 3A | 4 | `(net_income − OCF) / avg_total_assets` | `<0`→4 · `<0.03`→3 · `<0.06`→2 · `<0.10`→1 · else 0 |
| 3B | 3 | receivables growth ÷ revenue growth | `<1.0`→3 · `≤1.2`→2 · `≤1.5`→1 · else 0 |
| 3C | 3 | non-operating income / PBT | `<0.05`→3 · `<0.15`→2 · `<0.30`→1 · else 0 |
| 3D | 3 | stdev of operating margin, 8 quarters | `<0.015`→3 · `<0.030`→2 · `<0.050`→1 · else 0 |
| 3E | 2 | audit & disclosure integrity | clean, no restatement, immaterial RPT→2 · clean with material RPT→1 · emphasis of matter→0.5 · qualified/adverse/disclaimer→**0 + VETO** |

```
non_operating_income = interest_income + fx_gain_loss + other_income
                     + share_of_associates
```

**3C is a Shariah leading indicator.** It shares inputs with Screen B. A falling 3C score usually precedes a Screen B breach by one or two quarters. Emit an alert when 3C drops two bands in a single quarter.

3B requires ≥5 quarters of history. Fewer → 0 with `MISSING_DATA`.

### 4.4 Pillar 4 — Valuation (15)

**Calibration:** valuation must be judged against the Egyptian risk-free rate, not global multiples. At a ~19% policy rate, a P/E of 5 is a 20% earnings yield — barely a premium to risk-free money.

```
earnings_yield = 1 / pe_ratio
erp            = earnings_yield − macro.tbill_1y_yield
```

| Sub | Pts | Metric | Bands |
|---|---|---|---|
| 4A | 4 | ERP (percentage points) | `>0.15`→4 · `>0.10`→3 · `>0.05`→2 · `>0.00`→1 · else 0 |
| 4B | 3 | EV/EBITDA vs own 5-yr median | `>30% below`→3 · `>15% below`→2 · `within ±15%`→1 · `>15% above`→0.5 · else 0 |
| 4C | 3 | P/B vs justified P/B | `justified_pb = roe / 0.20`; `pb < justified`→3 · `within 20%`→2 · `within 50%`→1 · else 0 |
| 4D | 3 | FCF yield | `>0.15`→3 · `>0.10`→2 · `>0.05`→1 · `>0`→0.5 · else 0 |
| 4E | 2 | dividend yield & sustainability | `yield>0.08 AND payout<0.60 of FCF`→2 · `yield>0.05 sustainable`→1.5 · `yield>0.03`→1 · any dividend→0.5 · none→0 |

```
fcf = operating_cash_flow − capex
fcf_yield = fcf / market_cap
```

If `pe_ratio ≤ 0` (loss-making), 4A scores 0. 4B requires ≥5 years of history; fewer → use sector median and record the substitution in `inputs_json`.

### 4.5 Pillar 5 — Growth, Real (10)

**All growth is inflation-adjusted.**

```
real_growth = ((1 + nominal_growth) / (1 + cpi_yoy)) − 1
```

| Sub | Pts | Metric | Bands |
|---|---|---|---|
| 5A | 4 | real revenue CAGR, 3 yr | `>0.15`→4 · `>0.08`→3 · `>0.03`→2 · `>0`→1 · else 0 |
| 5B | 3 | real EPS CAGR, 3 yr | `>0.15`→3 · `>0.08`→2.25 · `>0.03`→1.5 · `>0`→0.75 · else 0 |
| 5C | 2 | physical volume growth | `>0.10`→2 · `>0.05`→1.5 · `>0`→1 · flat→0.5 · declining→0 |
| 5D | 1 | ROIC vs cost of capital | exceeds and stable/rising→1 · marginal→0.5 · below→0 |

CPI series comes from `macro_data.cpi_yoy`, matched to each period. Missing CPI for a period → 5A/5B score 0 with `MISSING_DATA`. Never fall back to nominal.

5C uses whatever physical unit the company discloses (tonnes, units, sqm delivered, subscribers). Not disclosed → 0.

### 4.6 Pillar 6 — Technical Strength & Tradability (10)

| Sub | Pts | Metric | Bands |
|---|---|---|---|
| 6A | 3 | price vs 200-DMA | above AND 200-DMA rising→3 · above, flat→2 · below by `≤10%`→1 · else 0 |
| 6B | 4 | target position ÷ ADTV(60) | `≤0.01`→4 · `≤0.03`→3 · `≤0.05`→2 · `≤0.10`→1 · `>0.10`→**0 + LIQUIDITY VETO** |
| 6C | 2 | 50/200 DMA + RSI(14) | `MA50>MA200 AND 40≤RSI≤65`→2 · either→1 · `RSI>75 or RSI<25`→0 |
| 6D | 1 | 6-month relative strength vs EGX30 | outperforming→1 · within ±5%→0.5 · else 0 |

**6B is a hard veto, not just a score.** A company you cannot exit within 5 sessions at ≤2% market impact is not investable regardless of its fundamentals. Run 6B as a universe pre-filter **before** extraction to avoid paying extraction cost on untradeable names.

### 4.7 Pillar 7 — Governance (5)

| Sub | Pts | Metric | Bands |
|---|---|---|---|
| 7A | 1.5 | filing timeliness & note completeness | on time, full notes→1.5 · on time, thin→1 · occasional delay→0.5 · repeated→0 |
| 7B | 1 | board independence | `≥1/3 independent AND chair≠CEO`→1 · `≥1/3` only→0.5 · else 0 |
| 7C | 1 | free float & minority record | `>30%` and clean record→1 · `15–30%`→0.5 · else 0 |
| 7D | 1 | related-party transactions | immaterial and disclosed→1 · material, arm's length, disclosed→0.5 · material and opaque→0 |
| 7E | 0.5 | capital allocation record | consistent and rational→0.5 · else 0 |

Governance failures act primarily as **vetoes** (§5.2), not deductions. This pillar measures gradations of good governance; bad governance removes the company.

### 4.8 Totals

```
total = P1 + P2 + P3 + P4 + P5 + P6 + P7      # must equal ≤ 100
```

| Band | Range |
|---|---|
| `EXCEPTIONAL` | 90–100 |
| `HIGH_CONVICTION` | 85–89.99 |
| `INVESTABLE` | 80–84.99 |
| `MINIMUM` | 75–79.99 |
| `WATCH_ONLY` | 65–74.99 |
| `REDUCE` | 55–64.99 |
| `EXIT` | <55 |

Assert in code that pillar maxima sum to exactly 100: `25+20+15+15+10+10+5`.

---

## 5. Decision Engine

Evaluated in strict priority order. The first matching rule wins.

### 5.1 Priority 1 — Shariah Override

| Condition | Decision |
|---|---|
| `breach ∈ {TYPE_1_ACTIVITY, TYPE_2_STRUCTURAL}` | `REMOVE` |
| `breach = TYPE_3_DENOMINATOR` | `HOLD_FROZEN` |
| `breach = TYPE_4_DATA` | `HOLD_FROZEN` |
| `status = ORANGE` | `HOLD_FROZEN` |

`HOLD_FROZEN` means: no adds under any circumstance, no sells unless the cure window expires.

### 5.2 Priority 2 — Vetoes (any one → `REMOVE`)

1. Auditor issues qualified/adverse/disclaimer opinion, or resigns
2. Restatement of prior-period financials confirmed (V5 failure investigated and confirmed)
3. Business model change invalidating the recorded thesis
4. Controlling-shareholder action materially adverse to minorities
5. Two consecutive quarters of negative OCF with rising debt
6. Two consecutive late filings
7. Liquidity veto: 6B = 0

Every veto stores its identifier in `decisions.veto_fired`.

### 5.3 Priority 3 — Score and Valuation

```
valuation_gap = (fair_value − price) / fair_value      # positive = discount
```

| Score | Gap | Held? | Decision |
|---|---|---|---|
| ≥85 | ≥0.25 | no | `BUY` |
| ≥85 | ≥0.15 | yes, below target weight | `ADD` |
| ≥85 | −0.15 … 0.15 | yes | `HOLD` |
| ≥85 | ≤−0.25 | yes | `REDUCE` (trim to target only) |
| 75–84.99 | ≥0.30 | no | `BUY` (starter size) |
| 75–84.99 | any | yes | `HOLD` |
| 65–74.99 | any | yes | `HOLD` |
| 65–74.99 | any | no | `NO_ACTION` |
| 55–64.99 | any | yes | `REDUCE` |
| <55 | any | yes | `SELL` |

### 5.4 Fair Value

Compute at least two methods; take the **lower** (conservative):

```
# 1. Justified P/E — real terms throughout to avoid inflation double-count
justified_pe = payout_ratio * (1 + g_real) / (r_real − g_real)
fair_value_1 = justified_pe * eps_ttm

# 2. Peer relative — EGX comparables only, never global peers
fair_value_2 = sector_median_pe * eps_ttm
```

Guard: if `r_real ≤ g_real`, the justified-P/E formula is undefined — return `None` for that method rather than a nonsensical number. DCF is permitted only when cash flow visibility genuinely exists; in a 12%-inflation, volatile-FX environment it is usually false precision, and the engine should not default to it.

### 5.5 Mandatory Decision Fields

Every decision row requires a non-empty `trigger`, `reason` (≥30 chars), and `falsification_condition` (≥20 chars) — enforced by database `CHECK` constraints. The falsification condition must be a measurable statement of what would prove the decision wrong, with a metric and a threshold. A generated reason that cannot state one is a signal the rule fired without an articulable basis.

---

## 6. Watchlist Movement

| List | Entry conditions (all) |
|---|---|
| `HIGH_CONVICTION` | score ≥82 · status GREEN · 6B ≥2 pts · no veto · gap ≥0 |
| `BUY_ON_PULLBACK` | score ≥75 · status GREEN/AMBER · **and** gap ≤−0.15 or RSI>70 or price >20% above 200-DMA |
| `HOLD` | score 65–74.99 · **or** score ≥75 with status AMBER/ORANGE |
| `REMOVE` | score <65 · **or** breach TYPE_1/TYPE_2 · **or** any veto |

**Movement rules:**

| Transition | Condition |
|---|---|
| `BUY_ON_PULLBACK → HIGH_CONVICTION` | price reaches `trigger_price` and score still ≥82 |
| `HOLD → HIGH_CONVICTION` | score ≥82 for **two consecutive** reviews |
| `HIGH_CONVICTION → BUY_ON_PULLBACK` | score ≥75 but gap ≤−0.15 |
| `HIGH_CONVICTION → HOLD` | score <82, or status → AMBER/ORANGE |
| `any → REMOVE` | REMOVE entry conditions met |
| `REMOVE → HOLD` | **two consecutive** compliant reviews AND score ≥70 AND §3.4 admission test passed |

Re-entry is deliberately harder than exit. Stale-entry rule: any company in `BUY_ON_PULLBACK` for 4 consecutive reviews without triggering is force-moved to `HOLD` and must be re-underwritten from scratch.

---

## 7. Portfolio Construction

### 7.1 Target Weights

| Score | Target weight of invested capital |
|---|---|
| 90–100 | 0.275 |
| 85–89.99 | 0.225 |
| 80–84.99 | 0.175 |
| 75–79.99 | 0.125 |
| <75 | 0 |

Weights are normalised across selected holdings, then clamped by §7.2.

### 7.2 Hard Constraints

```yaml
max_single_position: 0.30
max_single_sector:   0.40
min_position_size:   0.10
min_holdings:        3       # applies once capital >= 3000 EGP
max_holdings:        8       # applies below 50000 EGP
cash_reserve_min:    0.10
cash_reserve_max:    0.20
rebalance_band:      0.25    # relative drift from target
max_trade_cost_pct:  0.010   # round-trip cost ceiling per trade
```

### 7.3 Trade Cost Gate

```
estimated_round_trip_cost = 2 * (commission + min(min_fee, ...) + levies + taxes)
if estimated_round_trip_cost / trade_value > max_trade_cost_pct:
    suppress trade, log SUPPRESSED_UNECONOMIC
```

Fee parameters live in `config/thresholds.yaml` under `execution:` and **must be verified against Thndr's current published schedule** before the first live run. The engine must not ship with guessed fee values — leave them null and fail loudly if unset.

### 7.4 Rebalancing

Band-based only. Rebalance when `abs(actual_weight − target_weight) / target_weight > rebalance_band`, or when a decision requires it. No calendar rebalancing — it generates cost without information.

**At low capital, rebalance by directing new contributions to underweight positions, not by selling.** The engine should prefer a contribution-based rebalance plan whenever a contribution is pending.

### 7.5 Compliant Cash

Cash reserve must not be modelled as earning conventional interest. The engine treats cash as non-yielding and flags in the report that a certified compliant parking instrument is required. Do not model a yield on it.

---

## 8. Purification

```
purification_ratio = impure_income / net_profit_attributable
amount_due         = dividends_received * purification_ratio
```

Computed every review for every held company, accrued to `purification_ledger`, settled at least annually. If `net_profit_attributable ≤ 0`, ratio is undefined — carry the impure income forward to the next profitable period rather than dividing by a negative number.

An optional conservative mode applies the ratio to total return (dividends + realised gains) rather than dividends alone. Configurable via `purification.basis: DIVIDENDS | TOTAL_RETURN`.

---

## 9. Material Change Detection

Computed each review across QoQ, YoY, vs-FY, and vs-8-quarter-median frames.

| Metric | Trigger |
|---|---|
| real revenue YoY | ±10 pp vs prior trend |
| net profit YoY | ±25% |
| operating margin | ±300 bps |
| gross margin | ±200 bps |
| OCF | ±30% or turns negative |
| OCF / NI (TTM) | falls below 0.7 |
| FCF | negative 2 consecutive quarters |
| interest-bearing debt | +20% absolute or +500 bps on debt/assets |
| net debt / EBITDA | crosses 2.0 or 3.0 |
| interest income | crosses 3% of revenue (**early Shariah warning**) |
| receivables | DSO +20 days, or receivables growth >1.5× revenue growth |
| cash balance | ±40% |
| current ratio | falls below 1.2 |
| P/E | ±30% vs 8-quarter median |
| share count | dilution >3% |
| any Shariah screen | any status colour change |

**Seasonality rule:** QoQ is a momentum signal only. Any conclusion about direction must be based on YoY. Encode this — the engine must not emit a "deteriorating" narrative flag from QoQ alone.

---

## 10. Determinism Test

Required in the test suite:

```python
def test_engine_determinism():
    snapshot = load_golden_snapshot()
    hashes = {hash_output(run_full_engine(snapshot)) for _ in range(100)}
    assert len(hashes) == 1
```

Any failure of this test is a severity-1 bug. Non-determinism in this layer means the audit trail is fiction.

---

## 11. Threshold Verification Schedule

At every annual R4 review, re-verify against primary published sources and bump `config/thresholds.yaml` version:

- [ ] AAOIFI Standard 21 thresholds
- [ ] DJIM / S&P Shariah / MSCI Islamic / FTSE Shariah / Meezan methodologies
- [ ] Existence and methodology of any EGX Shariah index
- [ ] EGX / FRA filing deadlines
- [ ] Thndr commission schedule, minimum fee, settlement cycle
- [ ] EGX levies and current capital gains / dividend tax treatment for individuals
- [ ] CBE policy rate and 1-year T-bill yield source reliability

A threshold change never rewrites history. Prior results keep their original `threshold_version`.
