"""Weekly digest and quarterly review.

The weekly digest is the routine that builds trust: it goes out even when
nothing happened, because an agent that only speaks when something is wrong is
an alarm, and an alarm is not a steward (``ARCHITECTURE_V2`` §2).

The quarterly review is the one that changes things: it re-screens and
re-decides every holding after earnings season and journals the outcome. It
proposes; it never places an order (R6).
"""

from __future__ import annotations

from typing import Any

from engine.types import DecisionType
from routines.base import Routine, RoutineResult, guard
from tools.context import ToolContext
from tools.registry import call_tool

WEEKLY_DIGEST = "weekly_digest"
QUARTERLY_REVIEW = "quarterly_review"


def _events_since(ctx: ToolContext, since: str) -> list[dict[str, Any]]:
    """Ledger events on or after ``since`` (ISO date), oldest first."""
    return [
        {
            "at": e.occurred_at,
            "type": e.event_type.value,
            "ticker": e.ticker,
            "amount": str(e.amount) if e.amount is not None else None,
        }
        for e in ctx.ledger.read_all()
        if e.occurred_at >= since
    ]


def make_weekly_digest(since: str) -> Routine:
    """Build the digest for the week beginning ``since`` (ISO date)."""

    @guard(WEEKLY_DIGEST)
    def routine(ctx: ToolContext) -> RoutineResult:
        """One paragraph a week: what moved, what is owed, what needs doing."""
        state = ctx.ledger.state()
        events = _events_since(ctx, since)
        alerts = call_tool("get_compliance_alerts", {}, ctx)
        proposals = call_tool("get_order_proposals", {}, ctx)

        alert_list = alerts.data.get("alerts", []) if alerts.ok else []
        open_proposals = proposals.data.get("orders", []) if proposals.ok else []
        due = state.purification_due

        parts: list[str] = []
        if events:
            parts.append(f"{len(events)} portfolio event(s) this week")
        if alert_list:
            names = ", ".join(str(a["ticker"]) for a in alert_list)
            parts.append(f"{names} at or near a Shariah limit")
        if open_proposals:
            parts.append(f"{len(open_proposals)} order proposal(s) still awaiting your fills")
        if due > 0:
            parts.append(f"{due} EGP of purification outstanding")

        quiet = not parts
        message = (
            "Quiet week. Nothing filed, nothing breached, nothing to do."
            if quiet else "This week: " + "; ".join(parts) + "."
        )
        # Note the asymmetry: `quiet` is True but the message is still sent. A
        # quiet week is reported, not skipped.
        return RoutineResult(
            routine=WEEKLY_DIGEST,
            at=ctx.now,
            message=message,
            quiet=quiet,
            details={
                "events": len(events),
                "alerts": alert_list,
                "open_proposals": len(open_proposals),
                "purification_due": str(due),
                "cash": str(state.cash),
            },
        )

    return routine


def make_quarterly_review() -> Routine:
    """Build the post-earnings full review."""

    @guard(QUARTERLY_REVIEW)
    def routine(ctx: ToolContext) -> RoutineResult:
        """Re-screen, re-score, re-decide and journal every holding."""
        review = call_tool("run_full_review", {}, ctx)
        if not review.ok:
            return RoutineResult(
                routine=QUARTERLY_REVIEW, at=ctx.now,
                message=f"I could not complete the quarterly review: {review.error}",
                failures=(str(review.error),),
            )

        reviews: list[dict[str, Any]] = list(review.data.get("reviews", []))
        no_data: list[str] = list(review.data.get("no_data", []))

        journalled: list[str] = []
        for row in reviews:
            recorded = call_tool(
                "record_decision",
                {"ticker": str(row["ticker"]), "request_id": f"{QUARTERLY_REVIEW}:{ctx.now[:10]}:{row['ticker']}"},
                ctx,
            )
            if recorded.ok:
                journalled.append(str(recorded.data["decision_id"]))

        actionable = [
            r for r in reviews
            if r.get("decision") not in {DecisionType.HOLD.value, DecisionType.NO_ACTION.value}
        ]

        if not reviews and not no_data:
            return RoutineResult(
                routine=QUARTERLY_REVIEW, at=ctx.now, quiet=True,
                message="Quarterly review: nothing held, nothing to review.",
                details={"reviewed": 0},
            )

        if actionable:
            names = ", ".join(f"{r['ticker']} ({r['decision']})" for r in actionable)
            message = (
                f"Quarterly review done. Action on {names}. I have written the reasoning and what would "
                "prove it wrong; say the word and I will turn these into order proposals."
            )
        else:
            message = "Quarterly review done. Every holding is still compliant and still on thesis — nothing to do."

        if no_data:
            message += (
                " I could not review " + ", ".join(no_data) +
                ": no current validated filing. They are excluded from conclusions, not assumed fine."
            )

        return RoutineResult(
            routine=QUARTERLY_REVIEW,
            at=ctx.now,
            message=message,
            quiet=not actionable and not no_data,
            details={
                "reviewed": len(reviews),
                "actionable": [r["ticker"] for r in actionable],
                "journalled": journalled,
                "no_data": no_data,
            },
            failures=tuple(f"{t}: no validated data" for t in no_data),
        )

    return routine


def weekly_digest(ctx: ToolContext) -> RoutineResult:
    """Digest for the seven days ending today."""
    since = _minus_days(ctx.now[:10], 7)
    return make_weekly_digest(since)(ctx)


def quarterly_review(ctx: ToolContext) -> RoutineResult:
    """The post-earnings review."""
    return make_quarterly_review()(ctx)


def _minus_days(iso_date: str, days: int) -> str:
    """Date arithmetic on an ISO date without reading a clock."""
    from datetime import date, timedelta

    y, m, d = (int(part) for part in iso_date.split("-"))
    return (date(y, m, d) - timedelta(days=days)).isoformat()
