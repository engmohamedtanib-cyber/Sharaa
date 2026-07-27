"""Market-data derivations. Pure functions over price history.

The one that matters is :func:`trailing_average_market_cap`. Screens C, D and E
divide by the **trailing 12-month average market cap**, never by spot
(``ENGINE_SPEC`` §2.6). Using spot would let a company pass the debt screen
because its price ran up last week, and fail it because the market sold off —
compliance would oscillate with sentiment rather than with the balance sheet.

Everything here is pure: bars in, values out, no I/O and no clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from common.decimals import ZERO
from research.protocols import PriceBar


class InsufficientHistoryError(RuntimeError):
    """Not enough observations to compute the requested statistic.

    Raised rather than averaging what little there is: a "12-month average"
    computed from three weeks is a different number wearing the same name.
    """


@dataclass(frozen=True)
class MarketSnapshot:
    """Derived market inputs for one company at one date."""

    ticker: str
    as_of: str
    close_price: Decimal | None = None
    mcap_avg_12m: Decimal | None = None
    adtv_60d: Decimal | None = None
    ma_50: Decimal | None = None
    ma_200: Decimal | None = None
    ma_200_rising: bool | None = None
    observations: int = 0


def _mean(values: list[Decimal]) -> Decimal:
    return sum(values, ZERO) / Decimal(len(values))


def moving_average(bars: list[PriceBar], window: int) -> Decimal | None:
    """Mean close over the last ``window`` bars, or ``None`` if there are fewer."""
    if window <= 0:
        raise ValueError("window must be positive")
    if len(bars) < window:
        return None
    return _mean([b.close for b in bars[-window:]])


def average_daily_traded_value(bars: list[PriceBar], window: int = 60) -> Decimal | None:
    """Mean (close x volume) over the window; ``None`` when volume is missing.

    Missing volume on any bar in the window makes the average unrepresentative,
    so it returns ``None`` rather than averaging the days that happen to have it.
    """
    if len(bars) < window:
        return None
    recent = bars[-window:]
    if any(b.volume is None for b in recent):
        return None
    return _mean([b.close * b.volume for b in recent if b.volume is not None])


def trailing_average_market_cap(
    bars: list[PriceBar],
    *,
    as_of: date,
    months: int = 12,
    min_observations: int = 200,
) -> Decimal:
    """Average market cap over the trailing ``months``.

    ``min_observations`` guards the honest failure: the EGX trades roughly 245
    days a year, so a genuine 12-month window has ~245 bars. Far fewer means a
    newly listed company or a gap in the data, and either way the number would
    not be what it claims. It raises instead.
    """
    if months <= 0:
        raise ValueError("months must be positive")
    cutoff = as_of - timedelta(days=int(365.25 * months / 12))
    window = [b for b in bars if cutoff <= b.on <= as_of and b.market_cap is not None]
    if len(window) < min_observations:
        raise InsufficientHistoryError(
            f"{len(window)} market-cap observations in the trailing {months} months to {as_of}; "
            f"at least {min_observations} are needed. A shorter window is a different statistic, "
            "not an approximation of this one."
        )
    return _mean([b.market_cap for b in window if b.market_cap is not None])


def build_snapshot(
    ticker: str,
    bars: list[PriceBar],
    *,
    as_of: date,
    min_observations: int = 200,
) -> MarketSnapshot:
    """Derive every market input the engine consumes, from one price history.

    A missing statistic stays ``None``; the scoring layer treats that as
    MISSING_DATA and awards zero, which understates the score rather than
    inventing one.
    """
    ordered = sorted(bars, key=lambda b: b.on)
    try:
        mcap = trailing_average_market_cap(
            ordered, as_of=as_of, min_observations=min_observations
        )
    except InsufficientHistoryError:
        mcap = None

    ma_200 = moving_average(ordered, 200)
    ma_200_prior = moving_average(ordered[:-20], 200) if len(ordered) >= 220 else None
    return MarketSnapshot(
        ticker=ticker.strip().upper(),
        as_of=as_of.isoformat(),
        close_price=ordered[-1].close if ordered else None,
        mcap_avg_12m=mcap,
        adtv_60d=average_daily_traded_value(ordered),
        ma_50=moving_average(ordered, 50),
        ma_200=ma_200,
        ma_200_rising=(ma_200 > ma_200_prior) if (ma_200 is not None and ma_200_prior is not None) else None,
        observations=len(ordered),
    )
