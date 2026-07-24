"""Decimal helpers shared by the deterministic layers.

All money and all ratios are :class:`~decimal.Decimal` (``CLAUDE.md`` §5).
``float`` never appears in a screening, scoring or validation computation.

This module is pure: no I/O, no clock reads, no randomness.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

# A high working precision keeps intermediate ratios exact; rounding happens
# only at the point of comparison (CLAUDE.md §5), never during calculation.
ZERO = Decimal(0)
ONE = Decimal(1)


def D(value: Any) -> Decimal:  # noqa: N802  (short deliberate name)
    """Coerce ``value`` to :class:`Decimal` without ever going through ``float``.

    Ints, strings and existing Decimals convert exactly. A ``float`` is
    stringified first so that e.g. ``0.1`` becomes ``Decimal('0.1')`` rather
    than the binary-noise expansion. ``None`` raises — callers must decide
    what a missing value means, not silently coerce it to zero.
    """
    if value is None:
        raise ValueError("cannot convert None to Decimal (missing values must be handled explicitly)")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        # bool is an int subclass; reject to avoid True -> Decimal(1) surprises.
        raise TypeError("refusing to convert bool to Decimal")
    if isinstance(value, float):
        return Decimal(str(value))
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"cannot convert {value!r} to Decimal") from exc


def is_zero(value: Decimal) -> bool:
    """True if exactly zero. Used to guard divisions explicitly."""
    return value == ZERO


def safe_div(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    """Divide, returning ``None`` when the denominator is zero.

    A ``None`` result is a signal to the caller that the ratio is undefined —
    it must be handled (usually as MISSING_DATA or an undefined method),
    never treated as zero.
    """
    if denominator == ZERO:
        return None
    return numerator / denominator


def pct_delta(current: Decimal, prior: Decimal) -> Decimal | None:
    """``(current - prior) / prior`` with a zero-prior guard."""
    if prior == ZERO:
        return None
    return (current - prior) / prior
