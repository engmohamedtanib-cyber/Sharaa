"""Decision journal entries (BUILD_SPEC Phase 5, ENGINE_SPEC §5.5).

Journal entries are written **before** the order sheet, and every entry must
carry a measurable falsification condition — a statement of what would prove
the decision wrong, with a metric and a threshold. A decision that cannot state
one fired without an articulable basis, which is itself the finding.

Empty or placeholder text is rejected here as well as by the database ``CHECK``
constraints, so a malformed entry fails at the point it is built.

Pure module: no LLM (prose generation lives in ``reporting/narrative.py`` and
never touches a number), no I/O, no clock reads — the review date is passed in.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from engine.types import BreachType, DecisionType, ShariahStatus

MIN_REASON_CHARS = 30
MIN_FALSIFICATION_CHARS = 20

#: Rejected as falsification conditions: they state no metric and no threshold.
_PLACEHOLDERS = frozenset(
    {"n/a", "na", "none", "tbd", "unknown", "-", "--", "to be determined", "see above"}
)


class JournalEntryError(ValueError):
    """Raised when an entry would not be reconstructible or falsifiable."""


@dataclass(frozen=True)
class JournalEntry:
    """One auditable decision record."""

    review_code: str
    egx_code: str
    decision: DecisionType
    shariah_status: ShariahStatus
    breach: BreachType
    trigger: str
    reason: str
    falsification_condition: str
    score: Decimal | None = None
    fair_value: Decimal | None = None
    price_at_decision: Decimal | None = None
    valuation_gap_pct: Decimal | None = None
    veto_fired: str | None = None
    threshold_version: str = ""

    def __post_init__(self) -> None:
        if not self.trigger.strip():
            raise JournalEntryError(f"{self.egx_code}: trigger must be non-empty")
        if len(self.reason.strip()) < MIN_REASON_CHARS:
            raise JournalEntryError(
                f"{self.egx_code}: reason must be >= {MIN_REASON_CHARS} chars (got {len(self.reason.strip())})"
            )
        falsification = self.falsification_condition.strip()
        if len(falsification) < MIN_FALSIFICATION_CHARS:
            raise JournalEntryError(
                f"{self.egx_code}: falsification condition must be >= {MIN_FALSIFICATION_CHARS} chars"
            )
        if falsification.lower() in _PLACEHOLDERS:
            raise JournalEntryError(
                f"{self.egx_code}: falsification condition is a placeholder, not a measurable statement"
            )
        if not _has_measurable_claim(falsification):
            raise JournalEntryError(
                f"{self.egx_code}: falsification condition must name a metric and a threshold "
                f"(got {falsification!r})"
            )


def _has_measurable_claim(text: str) -> bool:
    """True if the text contains a number or a comparison word.

    A deliberately shallow check: it cannot verify that a condition is *good*,
    only that it is stated in measurable terms at all. The value is in making
    an unfalsifiable entry impossible to write silently.
    """
    lowered = text.lower()
    if any(ch.isdigit() for ch in lowered):
        return True
    comparatives = (
        "above", "below", "exceeds", "falls", "rises", "crosses", "returns",
        "drops", "reaches", ">=", "<=", ">", "<", "breach", "green", "amber",
        "orange", "red", "validated",
    )
    return any(word in lowered for word in comparatives)


def entry_from_decision(
    *,
    review_code: str,
    egx_code: str,
    decision: DecisionType,
    shariah_status: ShariahStatus,
    breach: BreachType,
    trigger: str,
    reason: str,
    falsification_condition: str,
    score: Decimal | None = None,
    fair_value: Decimal | None = None,
    price_at_decision: Decimal | None = None,
    valuation_gap_pct: Decimal | None = None,
    veto_fired: str | None = None,
    threshold_version: str = "",
) -> JournalEntry:
    """Build a validated journal entry from an engine decision."""
    return JournalEntry(
        review_code=review_code,
        egx_code=egx_code,
        decision=decision,
        shariah_status=shariah_status,
        breach=breach,
        trigger=trigger,
        reason=reason,
        falsification_condition=falsification_condition,
        score=score,
        fair_value=fair_value,
        price_at_decision=price_at_decision,
        valuation_gap_pct=valuation_gap_pct,
        veto_fired=veto_fired,
        threshold_version=threshold_version,
    )


def _fmt(value: Decimal | None) -> str:
    return "n/a" if value is None else str(value)


def render_entry(entry: JournalEntry) -> str:
    """Render one entry in the reconstructible journal format."""
    lines = [
        f"## {entry.egx_code} — {entry.decision.value}",
        "",
        f"- **Review**: {entry.review_code}",
        f"- **Shariah status**: {entry.shariah_status.value} (breach: {entry.breach.value})",
        f"- **Score**: {_fmt(entry.score)}",
        f"- **Price / fair value**: {_fmt(entry.price_at_decision)} / {_fmt(entry.fair_value)}"
        f" (gap {_fmt(entry.valuation_gap_pct)})",
        f"- **Trigger**: {entry.trigger}",
    ]
    if entry.veto_fired:
        lines.append(f"- **Veto fired**: {entry.veto_fired}")
    if entry.threshold_version:
        lines.append(f"- **Threshold version**: {entry.threshold_version}")
    lines += [
        "",
        f"**Reason.** {entry.reason}",
        "",
        f"**This decision is wrong if:** {entry.falsification_condition}",
        "",
    ]
    return "\n".join(lines)


def render_journal(review_code: str, entries: list[JournalEntry]) -> str:
    """Render the full decision journal for a review.

    Written before the order sheet so the reasoning is recorded independently
    of whether the orders are ever entered.
    """
    header = f"# Decision Journal — {review_code}\n\n"
    if not entries:
        return header + "No decisions this review.\n"
    return header + "\n".join(render_entry(e) for e in entries)
