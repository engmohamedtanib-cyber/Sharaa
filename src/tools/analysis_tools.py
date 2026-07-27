"""Analysis tools: screening, scoring, decisions, plans, explanations.

These are the tools that answer "is this halal?", "is it any good?", "what
should I do?" — and, right now, mostly answer "we don't have the data yet", out
loud and with a reason.

That is the intended behaviour, not a gap being papered over. The engine is
complete and tested; no filing has been ingested; so a screening request has
exactly two honest outcomes: a result computed from validated stored figures, or
a refusal naming what is missing. There is no third path where a number gets
estimated (R3).

Every tool here is a thin wrapper. The gate is ``engine.shariah``, the score is
``engine.scoring``, the decision is ``engine.decisions``, the sizing is
``engine.portfolio``. This module chooses no thresholds and computes no ratios.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from common.decimals import ZERO
from engine.decisions import DecisionContext, decide
from engine.policy import apply_policy
from engine.portfolio import Candidate, ExecutionFeesError, build_weights, trade_cost
from engine.scoring import score_company
from engine.shariah import GateResult, diagnose_breach, run_gate
from engine.types import DataStatus, DecisionType, ShariahStatus
from engine.universe import UniverseUnavailableError
from engine.valuation import fair_value, valuation_gap
from reporting.order_sheet import Side, Validity, limit_price, side_for, whole_shares
from store.jsonl_decisions import DecisionRecord, decision_id
from tools.audit import digest
from tools.context import ToolContext
from tools.data import CompanyFacts
from tools.errors import ToolRefusal
from tools.registry import Param, Tool, as_ticker, money, register

_NO_DATA_HINT = (
    "No validated financial data is stored for {ticker}. Screening runs on figures extracted from a "
    "filing and checked by the validation layer — never on recall, an estimate, or a peer. "
    "Upload the company's latest financial statements and I will run the gate on them."
)


def _facts_or_refuse(ctx: ToolContext, ticker: str) -> CompanyFacts:
    facts = ctx.data.get(ticker)
    if facts is None:
        raise ToolRefusal(_NO_DATA_HINT.format(ticker=ticker))
    return facts


def _gate(ctx: ToolContext, facts: CompanyFacts) -> GateResult:
    """Run Screens A-E on stored figures.

    The screening denominator is the trailing 12-month average market cap
    (``ENGINE_SPEC`` §2.6), not spot: a company must not pass because its price
    spiked last week. ``None`` propagates through the screens as MISSING_DATA.
    """
    return run_gate(
        facts.inputs.financials,
        facts.inputs.market.mcap_avg_12m,
        ctx.cfg,
        core_prohibited=facts.core_prohibited,
    )


def _valuation_gap(facts: CompanyFacts) -> Decimal | None:
    """Discount to fair value, or ``None`` when no method can be computed.

    ``None`` is a real answer here: the decision matrix has rows for an unknown
    gap, and an unknown gap is not a zero gap.
    """
    val = facts.inputs.valuation
    price = facts.inputs.market.close_price
    if val is None or price is None:
        return None
    fv = fair_value(
        sector_median_pe=val.sector_median_pe,
        eps_ttm=facts.inputs.financials.eps_basic,
    )
    return valuation_gap(fv, price)


def _require_universe(ctx: ToolContext) -> None:
    try:
        ctx.universe.require_available()
    except UniverseUnavailableError as exc:
        raise ToolRefusal(str(exc)) from exc


# ======================================================================
# Universe and policy
# ======================================================================
def _get_universe_status(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    u = ctx.universe
    payload: dict[str, Any] = {
        "index": u.index_name,
        "status": u.status.value,
        "available": u.available,
        "constituent_count": len(u.constituents),
        "expected_count": u.expected_count,
        "off_index_watch": [c.ticker for c in u.off_index_watch],
        "retrieved_at": u.retrieved.at,
        "retrieved_from": u.retrieved.source,
        "tickers": list(u.tickers),
    }
    payload["_message"] = (
        f"{u.index_name}: {len(u.constituents)} constituents, retrieved {u.retrieved.at}."
        if u.available
        else (
            "The investable universe has never been populated from a primary source, so no screening "
            "run is possible. This is not the same as 'nothing is compliant'. I need the EGX 33 "
            "Shariah constituent list — a screenshot or copy-paste of the official page is enough."
        )
    )
    return payload


register(Tool(
    name="get_universe_status",
    description="Whether the investable universe is populated, from where, and what is missing if not.",
    params=(),
    handler=_get_universe_status,
))


def _get_policy(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    p = ctx.policy
    return {
        "version": p.version,
        "effective_from": p.effective_from,
        "objective": p.objective,
        "horizon_years": p.horizon_years,
        "monthly_contribution": money(p.monthly_contribution),
        "effective_limits": {
            "max_single_position": str(p.effective_max_position(ctx.cfg)),
            "max_single_sector": str(p.effective_max_sector(ctx.cfg)),
            "cash_reserve_min": str(p.effective_cash_reserve_min(ctx.cfg)),
            "max_holdings": p.effective_max_holdings(ctx.cfg),
        },
        "excluded_tickers": [{"key": e.key, "reason": e.reason} for e in p.excluded_tickers],
        "excluded_sectors": [{"key": e.key, "reason": e.reason} for e in p.excluded_sectors],
        "accept_amber": p.accept_amber,
        "require_shariah_gate": p.require_shariah_gate,
        "note": p.note,
    }


register(Tool(
    name="get_policy",
    description="The investment policy in force: horizon, limits, personal exclusions, and the version number.",
    params=(),
    handler=_get_policy,
))


# ======================================================================
# Compliance
# ======================================================================
def _gate_payload(ctx: ToolContext, facts: CompanyFacts) -> dict[str, Any]:
    gate = _gate(ctx, facts)
    breach = diagnose_breach(
        gate,
        DataStatus.VALIDATED,
        facts.filing_age_days,
        ctx.cfg,
    )
    policy_decision = apply_policy(facts.ticker, facts.sector, gate.passed, gate.overall_status, ctx.policy)
    return {
        "ticker": facts.ticker,
        "sector": facts.sector,
        "period": facts.period_label,
        "status": gate.overall_status.value,
        "passed": gate.passed,
        "breach": breach.breach.value,
        "worst_screen": gate.worst_screen_code,
        "worst_utilisation": str(gate.worst_utilisation) if gate.worst_utilisation is not None else None,
        "activity_hard_fail": gate.activity_hard_fail,
        "screens": [
            {
                "code": s.code,
                "name": s.name,
                "ratio": str(s.ratio) if s.ratio is not None else None,
                "threshold": str(s.threshold),
                "utilisation": str(s.utilisation) if s.utilisation is not None else None,
                # Headroom is what the index never reports and the whole reason
                # we re-screen constituents ourselves (config/universe.yaml).
                "headroom": str(1 - s.utilisation) if s.utilisation is not None else None,
                "status": s.status.value,
            }
            for s in gate.screens
        ],
        "policy_verdict": policy_decision.verdict.value,
        "policy_reason": policy_decision.reason,
        "policy_version": policy_decision.policy_version,
        "thresholds_version": ctx.cfg.version,
        "source": facts.source_note,
    }


def _get_compliance_status(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    ticker = as_ticker(args["ticker"])
    payload = _gate_payload(ctx, _facts_or_refuse(ctx, ticker))
    payload["_message"] = (
        f"{ticker} is {payload['status']} on the Shariah gate"
        + (f" (worst screen {payload['worst_screen']}, {payload['worst_utilisation']} of the limit used)."
           if payload["worst_screen"] else ".")
    )
    return payload


register(Tool(
    name="get_compliance_status",
    description=(
        "Run Screens A-E for one company and report status, the binding screen, and headroom to each limit."
    ),
    params=(Param("ticker", "string", "EGX code."),),
    handler=_get_compliance_status,
))


def _run_screening(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    ticker_arg = (args.get("ticker") or "").strip().upper()
    if ticker_arg:
        return {"results": [_gate_payload(ctx, _facts_or_refuse(ctx, ticker_arg))], "screened": 1, "skipped": []}

    _require_universe(ctx)
    results: list[dict[str, Any]] = []
    skipped: list[str] = []
    for constituent in ctx.universe.screenable():
        facts = ctx.data.get(constituent.ticker)
        if facts is None:
            # DATA_INSUFFICIENT, listed by name. A company we could not check is
            # never silently dropped from the run (R3).
            skipped.append(constituent.ticker)
            continue
        results.append(_gate_payload(ctx, facts))
    return {
        "screened": len(results),
        "results": results,
        "skipped": skipped,
        "_message": (
            f"Screened {len(results)} of {len(ctx.universe.screenable())} companies. "
            f"{len(skipped)} have no validated data and are excluded as DATA_INSUFFICIENT, not as failures."
            if skipped else f"Screened {len(results)} companies."
        ),
    }


register(Tool(
    name="run_screening",
    description=(
        "Run the Shariah gate over one company or the whole universe. Companies without validated data are "
        "reported as skipped, never as compliant or non-compliant."
    ),
    params=(Param("ticker", "string", "Screen one company instead of the universe.", required=False),),
    handler=_run_screening,
))


# ======================================================================
# Scoring and decisions
# ======================================================================
def _score_payload(ctx: ToolContext, facts: CompanyFacts) -> dict[str, Any]:
    gate = _gate(ctx, facts)
    if not gate.passed:
        # R7: a company that fails the gate is never scored. Returning a score
        # here would let a strong number argue with a compliance verdict.
        raise ToolRefusal(
            f"{facts.ticker} fails the Shariah gate ({gate.overall_status.value}, worst screen "
            f"{gate.worst_screen_code}). It is not scored, not ranked and cannot appear in a plan."
        )
    result = score_company(facts.inputs, gate, ctx.cfg)
    if result is None:
        raise ToolRefusal(
            f"{facts.ticker} cannot be scored: the scoring layer refused the inputs (gate or data status). "
            "No partial score is produced — a score built on unvalidated figures is worse than none."
        )
    return {
        "ticker": facts.ticker,
        "total": str(result.total),
        "band": result.band,
        "pillars": {k: str(v) for k, v in sorted(result.pillar_totals.items())},
        "vetoes": list(result.vetoes),
        "alerts": list(result.alerts),
        "subscores": [
            {
                "code": s.subcriterion,
                "pillar": s.pillar,
                "points": str(s.points),
                "points_max": str(s.points_max),
                "band": s.band_matched,
                "rationale": s.rationale,
            }
            for s in result.subscores
        ],
        "thresholds_version": ctx.cfg.version,
    }


def _get_score_breakdown(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    ticker = as_ticker(args["ticker"])
    payload = _score_payload(ctx, _facts_or_refuse(ctx, ticker))
    payload["_message"] = f"{ticker} scores {payload['total']}/100 ({payload['band']})."
    return payload


register(Tool(
    name="get_score_breakdown",
    description="The 100-point score for a gate-passing company, pillar by pillar. Gate failures are not scored.",
    params=(Param("ticker", "string", "EGX code."),),
    handler=_get_score_breakdown,
))


def _decide_for(ctx: ToolContext, facts: CompanyFacts) -> tuple[dict[str, Any], DecisionContext]:
    gate = _gate(ctx, facts)
    breach = diagnose_breach(gate, DataStatus.VALIDATED, facts.filing_age_days, ctx.cfg)
    state = ctx.ledger.state()
    held = state.shares_of(facts.ticker) > ZERO

    score: Decimal | None = None
    vetoes: tuple[str, ...] = ()
    if gate.passed:
        scored = score_company(facts.inputs, gate, ctx.cfg)
        if scored is not None:
            score = scored.total
            vetoes = scored.vetoes

    ctx_dec = DecisionContext(
        breach=breach.breach,
        status=gate.overall_status,
        score=score,
        valuation_gap=_valuation_gap(facts),
        held=held,
        below_target_weight=False,
        vetoes=vetoes,
    )
    result = decide(ctx_dec, ctx.cfg)
    payload = {
        "ticker": facts.ticker,
        "decision": result.decision.value,
        "trigger": result.trigger,
        "reason": result.reason,
        "falsification_condition": result.falsification_condition,
        "veto_fired": result.veto_fired,
        "shariah_status": gate.overall_status.value,
        "breach": breach.breach.value,
        "score": str(score) if score is not None else None,
        "held": held,
        "thresholds_version": ctx.cfg.version,
        "policy_version": ctx.policy.version,
    }
    return payload, ctx_dec


def _get_decision(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    ticker = as_ticker(args["ticker"])
    facts = _facts_or_refuse(ctx, ticker)
    payload, _ = _decide_for(ctx, facts)
    payload["_message"] = f"{ticker}: {payload['decision']} — {payload['reason']}"
    return payload


register(Tool(
    name="get_decision",
    description=(
        "The engine's decision for one company: BUY/ADD/HOLD/REDUCE/SELL, its trigger, "
        "and what would falsify it."
    ),
    params=(Param("ticker", "string", "EGX code."),),
    handler=_get_decision,
))


def _record_decision(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """Journal a decision the engine produced. The figures are the engine's, not the model's."""
    ticker = as_ticker(args["ticker"])
    facts = _facts_or_refuse(ctx, ticker)
    payload, _ = _decide_for(ctx, facts)

    inputs_digest = digest(payload)
    did = decision_id(ticker, str(payload["decision"]), inputs_digest)
    record = DecisionRecord(
        decision_id=did,
        at=ctx.now,
        ticker=ticker,
        decision=str(payload["decision"]),
        shariah_status=str(payload["shariah_status"]),
        breach=str(payload["breach"]),
        trigger=str(payload["trigger"]),
        reason=str(payload["reason"]),
        falsification_condition=str(payload["falsification_condition"]),
        score=payload["score"],
        veto_fired=payload["veto_fired"],
        threshold_version=ctx.cfg.version,
        policy_version=ctx.policy.version,
        inputs_digest=inputs_digest,
    )
    ctx.decisions.append(record)
    return {
        "decision_id": did,
        "ticker": ticker,
        "decision": record.decision,
        "_message": f"Decision {did} journalled: {record.decision} on {ticker}.",
    }


register(Tool(
    name="record_decision",
    description="Journal the engine's current decision for a company, append-only, with its falsification condition.",
    params=(Param("ticker", "string", "EGX code."),),
    handler=_record_decision,
    write=True,
))


def _get_decision_history(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    ticker = (args.get("ticker") or "").strip().upper()
    records = ctx.decisions.for_ticker(ticker) if ticker else ctx.decisions.read_all()
    return {
        "count": len(records),
        "decisions": [
            {
                "decision_id": r.decision_id,
                "at": r.at,
                "ticker": r.ticker,
                "decision": r.decision,
                "reason": r.reason,
                "falsification_condition": r.falsification_condition,
                "score": r.score,
                "policy_version": r.policy_version,
            }
            for r in records
        ],
        "_message": "No decisions have been journalled yet." if not records else "",
    }


register(Tool(
    name="get_decision_history",
    description="Every journalled decision, or those for one company, oldest first.",
    params=(Param("ticker", "string", "Filter to one EGX code.", required=False),),
    handler=_get_decision_history,
))


def _explain_decision(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    did = str(args["decision_id"]).strip()
    record = ctx.decisions.get(did)
    if record is None:
        raise ToolRefusal(f"no journalled decision {did!r}.")
    return {
        "decision_id": record.decision_id,
        "at": record.at,
        "ticker": record.ticker,
        "decision": record.decision,
        "trigger": record.trigger,
        "reason": record.reason,
        "falsification_condition": record.falsification_condition,
        "shariah_status": record.shariah_status,
        "breach": record.breach,
        "score": record.score,
        "veto_fired": record.veto_fired,
        "thresholds_version": record.threshold_version,
        "policy_version": record.policy_version,
        "inputs_digest": record.inputs_digest,
        "_message": (
            f"On {record.at[:10]} we decided {record.decision} on {record.ticker}: {record.reason} "
            f"We said we would be wrong if: {record.falsification_condition}"
        ),
    }


register(Tool(
    name="explain_decision",
    description="Reconstruct one past decision: what was decided, on what numbers, and what would have falsified it.",
    params=(Param("decision_id", "string", "Id from get_decision_history."),),
    handler=_explain_decision,
))


# ======================================================================
# Planning
# ======================================================================
def _propose_investment_plan(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """Build target weights and limit orders, then record them as proposals.

    Everything numeric here comes from the engine: weights from
    ``engine.portfolio``, limit prices from ``reporting.order_sheet``, quantities
    from whole-share arithmetic. The model contributes nothing but the request.
    """
    _require_universe(ctx)
    state = ctx.ledger.state()
    deployable = args.get("cash")
    if deployable is None:
        cash_floor = ctx.policy.effective_cash_reserve_min(ctx.cfg)
        deployable = state.cash * (1 - cash_floor)
    if deployable <= ZERO:
        raise ToolRefusal(
            f"nothing to deploy: cash is {state.cash} EGP and the policy keeps "
            f"{ctx.policy.effective_cash_reserve_min(ctx.cfg)} of the portfolio in reserve."
        )

    candidates: list[Candidate] = []
    considered: list[dict[str, Any]] = []
    skipped: list[str] = []
    for constituent in ctx.universe.screenable():
        facts = ctx.data.get(constituent.ticker)
        if facts is None:
            skipped.append(constituent.ticker)
            continue
        gate = _gate(ctx, facts)
        verdict = apply_policy(facts.ticker, facts.sector, gate.passed, gate.overall_status, ctx.policy)
        considered.append({
            "ticker": facts.ticker,
            "status": gate.overall_status.value,
            "policy_verdict": verdict.verdict.value,
        })
        if not verdict.admitted:
            continue
        scored = score_company(facts.inputs, gate, ctx.cfg)
        if scored is None or scored.vetoes:
            continue
        candidates.append(Candidate(ident=facts.ticker, score=scored.total, sector=facts.sector))

    if not candidates:
        raise ToolRefusal(
            "no company currently clears the gate, the policy and the veto checks on validated data"
            + (f"; {len(skipped)} companies have no data at all and were not assessed." if skipped else ".")
        )

    weights = build_weights(candidates, ctx.cfg)
    proposals: list[dict[str, Any]] = []
    for ticker, weight in sorted(weights.items()):
        price = ctx.prices.get(ticker)
        if price is None:
            # No price, no order. An order without a current price cannot have a
            # defensible limit, and a market order is unrepresentable by design.
            skipped.append(ticker)
            continue
        trade_value = deployable * weight
        quantity = whole_shares(trade_value, price)
        if quantity <= ZERO:
            continue
        try:
            cost = trade_cost(quantity * price, ctx.cfg)
        except ExecutionFeesError as exc:
            # Loud, not silent: without a fee schedule we cannot tell whether a
            # trade is economic, and guessing the fees would guess the answer.
            raise ToolRefusal(
                f"cannot size orders: {exc}. Confirm Thndr's commission, minimum fee, levies and tax, "
                "and I will put them in config/thresholds.yaml."
            ) from exc
        if cost.suppressed:
            continue
        limit = limit_price(price, Side.BUY, Decimal("0.01"))
        order_id = f"{ticker}-{ctx.now[:10]}-{digest({'t': ticker, 'q': str(quantity), 'p': str(limit)})[:6]}"
        if ctx.orders.get(order_id) is None:
            ctx.orders.propose(
                order_id=order_id,
                at=ctx.now,
                ticker=ticker,
                side=Side.BUY,
                quantity=quantity,
                limit_price=limit,
                validity=Validity.DAY,
                note=f"target weight {weight}",
            )
        proposals.append({
            "order_id": order_id,
            "ticker": ticker,
            "side": Side.BUY.value,
            "quantity": str(quantity),
            "limit_price": str(limit),
            "target_weight": str(weight),
            "estimated_cost": str(cost.round_trip_cost),
        })

    return {
        "deployable_cash": money(deployable),
        "proposals": proposals,
        "considered": considered,
        "no_data": sorted(set(skipped)),
        "policy_version": ctx.policy.version,
        "_message": (
            f"{len(proposals)} order(s) proposed. Place them in your broker and tell me what filled — "
            "I record nothing until you confirm."
            if proposals else
            "Nothing to propose: no admitted candidate has a current market price to set a limit against."
        ),
    }


register(Tool(
    name="propose_investment_plan",
    description=(
        "Build a plan from screened, scored, policy-admitted companies: target weights, whole-share "
        "quantities and limit prices, recorded as proposals awaiting the user's fills."
    ),
    params=(Param("cash", "money", "EGP to deploy; defaults to cash above the policy reserve.", required=False),),
    handler=_propose_investment_plan,
    write=True,
))


def _run_full_review(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """Re-screen and re-decide every holding. The quarterly heartbeat."""
    state = ctx.ledger.state()
    held = sorted(state.open_holdings)
    if not held:
        return {
            "holdings_reviewed": 0,
            "reviews": [],
            "no_data": [],
            "_message": "No holdings to review.",
        }
    reviews: list[dict[str, Any]] = []
    no_data: list[str] = []
    for ticker in held:
        facts = ctx.data.get(ticker)
        if facts is None:
            no_data.append(ticker)
            continue
        payload, _ = _decide_for(ctx, facts)
        reviews.append(payload)

    actionable = [r for r in reviews if r["decision"] not in {DecisionType.HOLD.value, DecisionType.NO_ACTION.value}]
    return {
        "holdings_reviewed": len(reviews),
        "reviews": reviews,
        "no_data": no_data,
        "actionable": [r["ticker"] for r in actionable],
        "_message": (
            "Nothing to do — every holding is still on thesis and still compliant."
            if not actionable and not no_data else
            f"{len(actionable)} holding(s) need action"
            + (f"; {len(no_data)} have no current data and were not reviewed." if no_data else ".")
        ),
    }


register(Tool(
    name="run_full_review",
    description="Re-screen, re-score and re-decide every current holding. Says so plainly when nothing needs doing.",
    params=(),
    handler=_run_full_review,
))


def _get_compliance_alerts(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """Early warnings on holdings: drift towards a limit, before it becomes a breach."""
    state = ctx.ledger.state()
    alerts: list[dict[str, Any]] = []
    unchecked: list[str] = []
    for ticker in sorted(state.open_holdings):
        facts = ctx.data.get(ticker)
        if facts is None:
            unchecked.append(ticker)
            continue
        gate_payload = _gate_payload(ctx, facts)
        if gate_payload["status"] in {ShariahStatus.AMBER.value, ShariahStatus.RED.value}:
            alerts.append({
                "ticker": ticker,
                "status": gate_payload["status"],
                "screen": gate_payload["worst_screen"],
                "utilisation": gate_payload["worst_utilisation"],
                "breach": gate_payload["breach"],
            })
    return {
        "alerts": alerts,
        "unchecked": unchecked,
        "_message": (
            "No compliance alerts on current holdings."
            if not alerts else f"{len(alerts)} holding(s) are at or near a Shariah limit."
        ),
    }


register(Tool(
    name="get_compliance_alerts",
    description="Holdings at AMBER or RED on the Shariah gate — the early-warning list, before a breach forces a sale.",
    params=(),
    handler=_get_compliance_alerts,
))


def side_for_decision(decision: DecisionType) -> Side | None:
    """Which side a decision implies, or ``None`` when it generates no order.

    Re-exported for the routines layer so it does not import ``reporting``
    directly for one mapping.
    """
    return side_for(decision)
