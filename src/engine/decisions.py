"""Decision engine (ENGINE_SPEC §5).

Rules are evaluated in strict priority order; the first matching rule wins:
  1. Shariah override (breach / ORANGE)  — §5.1
  2. Vetoes (any one -> REMOVE)            — §5.2
  3. Score & valuation matrix              — §5.3

Every decision carries a substantive ``reason`` (>=30 chars) and a measurable
``falsification_condition`` (>=20 chars) — a rule that cannot state one has
fired without an articulable basis (§5.5). Pure module.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from engine.config import Thresholds
from engine.types import BreachType, DecisionType, ShariahStatus

# Veto identifiers (§5.2). The first two also arrive from scoring.
VETO_AUDIT = "AUDIT_OPINION_ADVERSE"
VETO_LIQUIDITY = "LIQUIDITY_6B"
VETO_RESTATEMENT = "RESTATEMENT_CONFIRMED"
VETO_MODEL_CHANGE = "BUSINESS_MODEL_CHANGE"
VETO_SHAREHOLDER = "CONTROLLING_SHAREHOLDER_ADVERSE"
VETO_OCF_DEBT = "TWO_Q_NEG_OCF_RISING_DEBT"
VETO_LATE_FILINGS = "TWO_CONSECUTIVE_LATE_FILINGS"


@dataclass(frozen=True)
class VetoInputs:
    """Structured facts for the §5.2 veto checks (pre-classified, no judgement).

    ``scoring_vetoes`` carries vetoes already surfaced by the scoring layer
    (audit-opinion adverse, liquidity 6B).
    """

    scoring_vetoes: tuple[str, ...] = ()
    auditor_resigned: bool = False
    restatement_confirmed: bool = False
    business_model_change: bool = False
    controlling_shareholder_adverse: bool = False
    two_q_negative_ocf_rising_debt: bool = False
    two_consecutive_late_filings: bool = False


@dataclass(frozen=True)
class DecisionContext:
    breach: BreachType
    status: ShariahStatus
    score: Decimal | None
    valuation_gap: Decimal | None
    held: bool
    below_target_weight: bool
    vetoes: tuple[str, ...] = ()


@dataclass(frozen=True)
class DecisionResult:
    decision: DecisionType
    trigger: str
    reason: str
    falsification_condition: str
    veto_fired: str | None = None


def collect_vetoes(v: VetoInputs) -> tuple[str, ...]:
    """Assemble the active veto list from structured inputs (§5.2)."""
    fired: list[str] = list(v.scoring_vetoes)
    if v.auditor_resigned and VETO_AUDIT not in fired:
        fired.append(VETO_AUDIT)
    if v.restatement_confirmed:
        fired.append(VETO_RESTATEMENT)
    if v.business_model_change:
        fired.append(VETO_MODEL_CHANGE)
    if v.controlling_shareholder_adverse:
        fired.append(VETO_SHAREHOLDER)
    if v.two_q_negative_ocf_rising_debt:
        fired.append(VETO_OCF_DEBT)
    if v.two_consecutive_late_filings:
        fired.append(VETO_LATE_FILINGS)
    # stable de-dup preserving order
    seen: set[str] = set()
    out: list[str] = []
    for x in fired:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return tuple(out)


def validate_decision_fields(result: DecisionResult, cfg: Thresholds) -> None:
    """Enforce §5.5 lengths in code as well as at the DB layer."""
    dec = cfg.decisions()
    if len(result.reason.strip()) < int(dec["reason_min_chars"]):
        raise ValueError(f"decision reason too short: {result.reason!r}")
    if len(result.falsification_condition.strip()) < int(dec["falsification_min_chars"]):
        raise ValueError(f"falsification condition too short: {result.falsification_condition!r}")


# ----------------------------------------------------------------------
# Priority 1 — Shariah override (§5.1)
# ----------------------------------------------------------------------
def _shariah_override(ctx: DecisionContext) -> DecisionResult | None:
    if ctx.breach in (BreachType.TYPE_1_ACTIVITY, BreachType.TYPE_2_STRUCTURAL):
        return DecisionResult(
            decision=DecisionType.REMOVE,
            trigger=f"Shariah breach {ctx.breach.value}",
            reason=(
                f"Shariah gate breach classified {ctx.breach.value}; a non-compliant "
                "position cannot be held regardless of fundamentals (R7)."
            ),
            falsification_condition=(
                "Screening returns overall_status in {GREEN, AMBER} with breach=NONE "
                "on the next validated filing."
            ),
        )
    if ctx.breach is BreachType.TYPE_3_DENOMINATOR:
        return DecisionResult(
            decision=DecisionType.HOLD_FROZEN,
            trigger="Type 3 denominator breach — cure window open",
            reason=(
                "Denominator-driven breach (Type 3): position frozen for the cure "
                "window — no adds, no sells until the window resolves."
            ),
            falsification_condition=(
                "Utilisation of the breaching screen returns to <= 1.00 by the cure "
                "window expiry review."
            ),
        )
    if ctx.breach is BreachType.TYPE_4_DATA:
        return DecisionResult(
            decision=DecisionType.HOLD_FROZEN,
            trigger="Type 4 data breach — stale/unvalidated",
            reason=(
                "Data is stale or unvalidated (Type 4): treated as ORANGE and frozen; "
                "two consecutive unresolved reviews force an exit."
            ),
            falsification_condition=(
                "A fresh filing validates (data_status=VALIDATED) with period_end within "
                "180 days at the next review."
            ),
        )
    if ctx.status is ShariahStatus.ORANGE:
        return DecisionResult(
            decision=DecisionType.HOLD_FROZEN,
            trigger="Status ORANGE — utilisation in (0.85, 1.00]",
            reason=(
                "Overall status ORANGE: within 15% of a screening limit; frozen — no "
                "adds under any circumstance until headroom is restored."
            ),
            falsification_condition=(
                "Overall utilisation falls to <= 0.85 (AMBER or better) on the next "
                "validated filing."
            ),
        )
    return None


# ----------------------------------------------------------------------
# Priority 2 — Vetoes (§5.2)
# ----------------------------------------------------------------------
def _veto(ctx: DecisionContext) -> DecisionResult | None:
    if not ctx.vetoes:
        return None
    fired = ctx.vetoes[0]
    return DecisionResult(
        decision=DecisionType.REMOVE,
        trigger=f"Veto: {fired}",
        reason=(
            f"Hard veto fired ({fired}); the company is removed irrespective of score "
            "or valuation — vetoes override the scoring model entirely."
        ),
        falsification_condition=(
            "The vetoed condition is confirmed resolved and no veto fires on the next "
            "two consecutive validated reviews."
        ),
        veto_fired=fired,
    )


# ----------------------------------------------------------------------
# Priority 3 — Score & valuation matrix (§5.3)
# ----------------------------------------------------------------------
def _fmt(x: Decimal | None) -> str:
    return "n/a" if x is None else str(x)


def _score_matrix(ctx: DecisionContext, cfg: Thresholds) -> DecisionResult:
    s = ctx.score
    if s is None:
        # A gate-passing company is always scored; reaching here means the
        # caller mis-wired the pipeline. Fail explicitly rather than guess.
        raise ValueError("score matrix requires a score; None indicates a gate/scoring wiring error")
    g = ctx.valuation_gap
    dec = cfg.decisions()
    score_high = Decimal(str(dec["score_high"]))
    score_min = Decimal(str(dec["score_min"]))
    score_watch = Decimal(str(dec["score_watch"]))
    score_reduce = Decimal(str(dec["score_reduce"]))
    gap_buy = Decimal(str(dec["gap_buy"]))
    gap_add = Decimal(str(dec["gap_add"]))
    gap_hold_low = Decimal(str(dec["gap_hold_low"]))
    gap_hold_high = Decimal(str(dec["gap_hold_high"]))
    gap_reduce = Decimal(str(dec["gap_reduce"]))
    gap_buy_starter = Decimal(str(dec["gap_buy_starter"]))

    def falsify(metric: str) -> str:
        return f"{metric}; re-underwrite if score crosses a band edge or the valuation gap moves >0.10."

    if s >= score_high:
        if (not ctx.held) and g is not None and g >= gap_buy:
            return DecisionResult(
                DecisionType.BUY, "score>=85 and discount>=0.25, not held",
                f"High-conviction score {s} with a {_fmt(g)} discount to fair value while unheld: initiate.",
                falsify(f"decision invalid if realised gap < {gap_buy} at fill or score drops below {score_high}"),
            )
        if ctx.held and g is not None and g >= gap_add and ctx.below_target_weight:
            return DecisionResult(
                DecisionType.ADD, "score>=85, discount>=0.15, held below target",
                f"Score {s}, gap {_fmt(g)} and position below target weight: add toward target.",
                falsify(f"decision invalid if position reaches target weight or gap < {gap_add}"),
            )
        if ctx.held and g is not None and gap_hold_low <= g <= gap_hold_high:
            return DecisionResult(
                DecisionType.HOLD, "score>=85, gap within +/-0.15, held",
                f"Score {s} with valuation gap {_fmt(g)} near fair value: hold the position.",
                falsify(f"decision invalid if gap exits [{gap_hold_low}, {gap_hold_high}] or score < {score_high}"),
            )
        if ctx.held and g is not None and g <= gap_reduce:
            return DecisionResult(
                DecisionType.REDUCE, "score>=85 but gap<=-0.25, held",
                f"Score {s} but price {_fmt(g)} above fair value: trim to target weight only.",
                falsify(f"decision invalid if gap rises above {gap_reduce}"),
            )
        return _default_row(ctx, s, g)

    if s >= score_min:
        if (not ctx.held) and g is not None and g >= gap_buy_starter:
            return DecisionResult(
                DecisionType.BUY, "score 75-85 and discount>=0.30, not held",
                f"Investable score {s} with a deep {_fmt(g)} discount: initiate a starter position.",
                falsify(f"decision invalid if realised gap < {gap_buy_starter} at fill or score < {score_min}"),
            )
        if ctx.held:
            return DecisionResult(
                DecisionType.HOLD, "score 75-85, held",
                f"Investable score {s}: hold; discount {_fmt(g)} insufficient to add at this band.",
                falsify(f"decision invalid if score >= {score_high} or falls below {score_min}"),
            )
        return _no_action(ctx, s, g)

    if s >= score_watch:
        if ctx.held:
            return DecisionResult(
                DecisionType.HOLD, "score 65-75, held",
                f"Watch-only score {s}: hold existing position but do not add; monitor for band change.",
                falsify(f"decision invalid if score rises to >= {score_min} or falls below {score_watch}"),
            )
        return _no_action(ctx, s, g)

    if s >= score_reduce:
        if ctx.held:
            return DecisionResult(
                DecisionType.REDUCE, "score 55-65, held",
                f"Reduce-band score {s}: trim the position; conviction no longer supports the weight.",
                falsify(f"decision invalid if score recovers to >= {score_watch} on the next review"),
            )
        return _no_action(ctx, s, g)

    # score < 55
    if ctx.held:
        return DecisionResult(
            DecisionType.SELL, "score<55, held",
            f"Exit-band score {s}: sell the position; it no longer meets the minimum investable bar.",
            falsify(f"decision invalid if score returns to >= {score_reduce} within two reviews"),
        )
    return _no_action(ctx, s, g)


def _default_row(ctx: DecisionContext, s: Decimal, g: Decimal | None) -> DecisionResult:
    if ctx.held:
        return DecisionResult(
            DecisionType.HOLD, "score>=85, uncovered gap region, held",
            f"Score {s} with valuation gap {_fmt(g)} in an uncovered region: hold pending clearer signal.",
            "decision invalid if gap moves into a defined ADD/REDUCE region (>0.15 below or <-0.25 above).",
        )
    return _no_action(ctx, s, g)


def _no_action(ctx: DecisionContext, s: Decimal, g: Decimal | None) -> DecisionResult:
    return DecisionResult(
        DecisionType.NO_ACTION, "no position and insufficient discount",
        f"Score {s} with gap {_fmt(g)} does not clear the initiation bar while unheld: no action taken.",
        "decision invalid once the valuation gap clears the initiation threshold for the score band.",
    )


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------
def decide(ctx: DecisionContext, cfg: Thresholds) -> DecisionResult:
    """Return the single winning decision under strict priority order."""
    result = _shariah_override(ctx) or _veto(ctx) or _score_matrix(ctx, cfg)
    validate_decision_fields(result, cfg)
    return result
