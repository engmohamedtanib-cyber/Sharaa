"""Pure Decimal statistics used by the scoring pillars.

No ``float`` (CLAUDE.md §5), no I/O. Deterministic for a fixed input.
"""

from __future__ import annotations

from decimal import Decimal

from common.decimals import ZERO


def mean(xs: tuple[Decimal, ...]) -> Decimal:
    if not xs:
        raise ValueError("mean of empty sequence")
    return sum(xs, ZERO) / Decimal(len(xs))


def population_stdev(xs: tuple[Decimal, ...]) -> Decimal:
    """Population standard deviation (divide by N), exact until the final sqrt.

    The square root is taken with :meth:`Decimal.sqrt`, which is deterministic
    under the active context, so repeated runs are byte-identical.
    """
    if len(xs) < 2:
        raise ValueError("stdev requires at least two points")
    mu = mean(xs)
    variance = sum(((x - mu) * (x - mu) for x in xs), ZERO) / Decimal(len(xs))
    return variance.sqrt()


def linear_slope(ys: tuple[Decimal, ...]) -> Decimal:
    """OLS slope of ``ys`` against evenly spaced x = 0, 1, ... n-1.

    Used by sub-criterion 1E (trend of the worst screen's utilisation). A
    slope <= 0 means "improving/flat".
    """
    n = len(ys)
    if n < 2:
        raise ValueError("slope requires at least two points")
    xs = tuple(Decimal(i) for i in range(n))
    x_mean = mean(xs)
    y_mean = mean(ys)
    num = sum(((xs[i] - x_mean) * (ys[i] - y_mean) for i in range(n)), ZERO)
    den = sum(((xs[i] - x_mean) * (xs[i] - x_mean) for i in range(n)), ZERO)
    if den == ZERO:
        return ZERO
    return num / den
