"""Band matching — the deterministic core of config-driven scoring.

A *band* is a comparison (``op`` + boundary ``value``) carrying a payload
(``points``, or a ``band``/``weight`` label). Bands are evaluated top-down;
the FIRST matching band wins (``ENGINE_SPEC`` §4). This mirrors the spec's
"boundary values belong to the more generous band" rule: order the most
generous band first and use ``le``/``ge`` as written.

Pure: no I/O, no clock, no randomness.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from common.decimals import D

_OPS = ("le", "lt", "gt", "ge", "eq")


@dataclass(frozen=True)
class Band:
    """One row of a band table."""

    op: str
    value: Decimal
    payload: dict[str, Any]

    def matches(self, x: Decimal) -> bool:
        if self.op == "le":
            return x <= self.value
        if self.op == "lt":
            return x < self.value
        if self.op == "gt":
            return x > self.value
        if self.op == "ge":
            return x >= self.value
        if self.op == "eq":
            return x == self.value
        raise ValueError(f"unknown band operator {self.op!r}")

    def label(self) -> str:
        return f"{self.op}:{self.value}"


def parse_bands(raw: list[dict[str, Any]]) -> tuple[Band, ...]:
    """Parse a YAML band list into typed :class:`Band` rows.

    Each row must contain exactly one operator key from ``le/lt/gt/ge/eq``.
    Remaining keys become the payload (values coerced to Decimal where numeric).
    """
    bands: list[Band] = []
    for row in raw:
        ops_present = [k for k in _OPS if k in row]
        if len(ops_present) != 1:
            raise ValueError(f"band row must have exactly one operator key, got {row!r}")
        op = ops_present[0]
        value = D(row[op])
        payload = {k: _maybe_decimal(v) for k, v in row.items() if k != op}
        bands.append(Band(op=op, value=value, payload=payload))
    return tuple(bands)


def _maybe_decimal(v: Any) -> Any:
    if isinstance(v, (bool, str)):
        return v
    if isinstance(v, (int, float)):
        return D(v)
    return v


def match(x: Decimal, bands: tuple[Band, ...]) -> Band | None:
    """Return the first band matching ``x``, or ``None`` if none match."""
    for band in bands:
        if band.matches(x):
            return band
    return None
