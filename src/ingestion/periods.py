"""Standalone-quarter derivation from cumulative interims (EXTRACTION_SPEC §4).

EGX interim statements are **cumulative**: Q1 covers 3 months, H1 six, 9M nine,
FY twelve. Treating a cumulative figure as standalone doubles growth metrics
(BUILD_SPEC §5), so standalone quarters must be derived:

    Q1 = Q1_reported
    Q2 = H1_reported - Q1_reported
    Q3 = 9M_reported - H1_reported
    Q4 = FY_reported - 9M_reported

**Balance sheet items are point-in-time and are never subtracted** — only
income-statement and cash-flow items are cumulative. Attempting to derive a
balance-sheet item raises.

If the prior cumulative filing is missing, the standalone quarter cannot be
derived: this returns ``None`` (unavailable) rather than approximating (R3).

Pure module: no I/O, no LLM.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from engine.types import PeriodType


class Statement(StrEnum):
    """Mirrors ``statement_type`` in ``db/schema.sql``."""

    BALANCE_SHEET = "BALANCE_SHEET"
    INCOME_STATEMENT = "INCOME_STATEMENT"
    CASH_FLOW = "CASH_FLOW"
    EQUITY = "EQUITY"
    NOTE = "NOTE"


#: Statements whose figures accumulate through the fiscal year.
CUMULATIVE_STATEMENTS = frozenset({Statement.INCOME_STATEMENT, Statement.CASH_FLOW})

#: Standalone quarter -> (cumulative period, period to subtract). Q1 needs no
#: subtraction; the remaining quarters are differences of adjacent cumulatives.
_DERIVATION: dict[str, tuple[PeriodType, PeriodType | None]] = {
    "Q1": (PeriodType.Q1, None),
    "Q2": (PeriodType.H1, PeriodType.Q1),
    "Q3": (PeriodType.NINE_M, PeriodType.H1),
    "Q4": (PeriodType.FY, PeriodType.NINE_M),
}

STANDALONE_QUARTERS = tuple(_DERIVATION)


class PointInTimeError(ValueError):
    """Raised when a balance-sheet item is submitted for period derivation."""


@dataclass(frozen=True)
class DerivedValue:
    """A standalone-quarter figure and the audit string explaining it."""

    quarter: str
    value: Decimal
    is_derived: bool
    derivation: str


def is_cumulative(statement: Statement) -> bool:
    """True if figures in ``statement`` accumulate across the fiscal year."""
    return statement in CUMULATIVE_STATEMENTS


def derive_standalone(
    quarter: str,
    cumulative: dict[PeriodType, Decimal],
    statement: Statement,
) -> DerivedValue | None:
    """Derive one standalone quarter from cumulative filings.

    ``cumulative`` maps the reported period to its cumulative value for a single
    line item within one fiscal year. Returns ``None`` when a required filing is
    absent — the quarter is then *unavailable*, never approximated.

    Raises :class:`PointInTimeError` for balance-sheet items, and ``KeyError``
    for an unknown quarter label.
    """
    if quarter not in _DERIVATION:
        raise KeyError(f"unknown standalone quarter {quarter!r}")
    if not is_cumulative(statement):
        raise PointInTimeError(
            f"{statement.value} items are point-in-time and must never be subtracted "
            f"(attempted to derive {quarter})"
        )

    current_period, prior_period = _DERIVATION[quarter]
    current = cumulative.get(current_period)
    if current is None:
        return None

    if prior_period is None:
        return DerivedValue(
            quarter=quarter,
            value=current,
            is_derived=False,
            derivation=f"{current_period.value} as reported (standalone == cumulative)",
        )

    prior = cumulative.get(prior_period)
    if prior is None:
        # Prior cumulative filing missing -> cannot derive (§4).
        return None

    return DerivedValue(
        quarter=quarter,
        value=current - prior,
        is_derived=True,
        derivation=f"{quarter} = {current_period.value} - {prior_period.value}",
    )


def derive_all_standalone(
    cumulative: dict[PeriodType, Decimal],
    statement: Statement,
) -> dict[str, DerivedValue]:
    """Derive every standalone quarter that the available filings support.

    Quarters that cannot be derived are simply absent from the result — an
    explicit gap, not a zero.
    """
    out: dict[str, DerivedValue] = {}
    for quarter in STANDALONE_QUARTERS:
        derived = derive_standalone(quarter, cumulative, statement)
        if derived is not None:
            out[quarter] = derived
    return out


def trailing_twelve_months(
    standalone_quarters: list[Decimal],
) -> Decimal | None:
    """Sum exactly four standalone quarters into a TTM figure.

    Returns ``None`` unless precisely four quarters are supplied — a TTM built
    from three quarters is a wrong number, not a partial one.
    """
    if len(standalone_quarters) != 4:
        return None
    return sum(standalone_quarters, Decimal(0))
