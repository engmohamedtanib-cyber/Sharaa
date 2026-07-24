"""Result types for the deterministic validation layer.

``validation/`` may not make a network call and contains no LLM (``CLAUDE.md``
§4). Every validator is a pure function returning a :class:`ValidationResult`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from common.decimals import ZERO


@dataclass(frozen=True)
class ValidationResult:
    code: str                 # 'V1'..'V9'
    name: str
    is_critical: bool
    passed: bool
    applicable: bool          # False when required inputs are absent
    expected: Decimal | None = None
    actual: Decimal | None = None
    tolerance: Decimal | None = None
    message: str = ""
    warning: bool = False     # non-failing flag (e.g. possible restatement)


def rel_diff(a: Decimal, b: Decimal) -> Decimal | None:
    """|a - b| / max(|a|, |b|). ``None`` when both are zero."""
    scale = max(abs(a), abs(b))
    if scale == ZERO:
        return None
    return abs(a - b) / scale


def within(a: Decimal, b: Decimal, tol: Decimal, *, denom: Decimal | None = None) -> bool:
    """True if ``|a - b|`` is within ``tol`` relative to ``denom`` (or max(|a|,|b|))."""
    base = abs(denom) if denom is not None else max(abs(a), abs(b))
    if base == ZERO:
        return a == b
    return abs(a - b) / base < tol
