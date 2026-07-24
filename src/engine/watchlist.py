"""Watchlist assignment and movement (ENGINE_SPEC §6).

Four lists; re-entry is deliberately harder than exit. Pure module.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from engine.config import Thresholds
from engine.types import BreachType, ShariahStatus, WatchlistName


@dataclass(frozen=True)
class WatchlistSignals:
    score: Decimal | None
    status: ShariahStatus
    gap: Decimal | None
    six_b_points: Decimal
    has_veto: bool
    breach: BreachType
    rsi: Decimal | None = None
    pct_above_200dma: Decimal | None = None


@dataclass(frozen=True)
class MovementSignals:
    """Extra state needed for the documented transition rules (§6)."""

    price: Decimal | None = None
    trigger_price: Decimal | None = None
    consecutive_hc_reviews: int = 0        # HOLD -> HIGH_CONVICTION needs 2
    consecutive_compliant_reviews: int = 0  # REMOVE -> HOLD needs 2
    admission_ok: bool = False              # §3.4 test
    quarters_on_pullback: int = 0           # stale-entry rule (>=4)


def _is_remove(sig: WatchlistSignals, cfg: Thresholds) -> bool:
    wl = cfg.watchlist()
    max_score = Decimal(str(wl["remove"]["max_score"]))
    if sig.score is not None and sig.score < max_score:
        return True
    if sig.breach in (BreachType.TYPE_1_ACTIVITY, BreachType.TYPE_2_STRUCTURAL):
        return True
    return sig.has_veto


def assign_watchlist(sig: WatchlistSignals, cfg: Thresholds) -> WatchlistName | None:
    """Entry-condition assignment (first matching list wins)."""
    wl = cfg.watchlist()
    if _is_remove(sig, cfg):
        return WatchlistName.REMOVE

    hc = wl["high_conviction"]
    if (
        sig.score is not None
        and sig.score >= Decimal(str(hc["min_score"]))
        and sig.status is ShariahStatus.GREEN
        and sig.six_b_points >= Decimal(str(hc["min_6b_points"]))
        and not sig.has_veto
        and sig.gap is not None
        and sig.gap >= Decimal(str(hc["min_gap"]))
    ):
        return WatchlistName.HIGH_CONVICTION

    bp = wl["buy_on_pullback"]
    if (
        sig.score is not None
        and sig.score >= Decimal(str(bp["min_score"]))
        and sig.status in (ShariahStatus.GREEN, ShariahStatus.AMBER)
        and _pullback_trigger(sig, bp)
    ):
        return WatchlistName.BUY_ON_PULLBACK

    h = wl["hold"]
    if sig.score is not None and (
        (Decimal(str(h["score_low"])) <= sig.score <= Decimal(str(h["score_high"])))
        or (sig.score >= Decimal(str(bp["min_score"])) and sig.status in (ShariahStatus.AMBER, ShariahStatus.ORANGE))
    ):
        return WatchlistName.HOLD

    return None


def _pullback_trigger(sig: WatchlistSignals, bp: dict[str, object]) -> bool:
    return (
        (sig.gap is not None and sig.gap <= Decimal(str(bp["gap_trigger"])))
        or (sig.rsi is not None and sig.rsi > Decimal(str(bp["rsi_trigger"])))
        or (sig.pct_above_200dma is not None and sig.pct_above_200dma > Decimal(str(bp["above_200dma_trigger"])))
    )


@dataclass(frozen=True)
class MovementResult:
    list_name: WatchlistName | None
    needs_reunderwrite: bool = False


def resolve_movement(
    prev: WatchlistName | None,
    sig: WatchlistSignals,
    mov: MovementSignals,
    cfg: Thresholds,
) -> MovementResult:
    """Apply the §6 transition rules given the prior list and current signals."""
    wl = cfg.watchlist()
    m = wl["movements"]

    # any -> REMOVE always wins.
    if _is_remove(sig, cfg):
        return MovementResult(WatchlistName.REMOVE)

    score = sig.score
    hc_min = Decimal(str(wl["high_conviction"]["min_score"]))
    pull_min = Decimal(str(wl["buy_on_pullback"]["min_score"]))
    gap_trigger = Decimal(str(wl["buy_on_pullback"]["gap_trigger"]))

    if prev is WatchlistName.BUY_ON_PULLBACK:
        if (
            mov.price is not None
            and mov.trigger_price is not None
            and mov.price >= mov.trigger_price
            and score is not None
            and score >= hc_min
        ):
            return MovementResult(WatchlistName.HIGH_CONVICTION)
        if mov.quarters_on_pullback >= int(m["stale_pullback_reviews"]):
            # Force to HOLD and re-underwrite from scratch (§6 stale-entry rule).
            return MovementResult(WatchlistName.HOLD, needs_reunderwrite=True)

    if prev is WatchlistName.HOLD and (
        score is not None
        and score >= hc_min
        and mov.consecutive_hc_reviews >= int(m["hold_to_high_conviction_reviews"])
    ):
        return MovementResult(WatchlistName.HIGH_CONVICTION)

    if prev is WatchlistName.HIGH_CONVICTION:
        if score is not None and score >= pull_min and sig.gap is not None and sig.gap <= gap_trigger:
            return MovementResult(WatchlistName.BUY_ON_PULLBACK)
        if (score is not None and score < hc_min) or sig.status in (ShariahStatus.AMBER, ShariahStatus.ORANGE):
            return MovementResult(WatchlistName.HOLD)

    if prev is WatchlistName.REMOVE:
        if (
            mov.consecutive_compliant_reviews >= int(m["remove_to_hold_reviews"])
            and score is not None
            and score >= Decimal(str(m["remove_to_hold_min_score"]))
            and mov.admission_ok
        ):
            return MovementResult(WatchlistName.HOLD)
        return MovementResult(WatchlistName.REMOVE)

    # No explicit transition fired: fall back to entry assignment, but never
    # let the fallback *promote* into HIGH_CONVICTION from a gated prior list —
    # HC is reachable only via the price-trigger (from BUY_ON_PULLBACK) or the
    # two-review (from HOLD) gates above. Re-entry is deliberately harder (§6).
    fallback = assign_watchlist(sig, cfg)
    if fallback is WatchlistName.HIGH_CONVICTION and prev in (
        WatchlistName.HOLD,
        WatchlistName.BUY_ON_PULLBACK,
    ):
        return MovementResult(prev)
    return MovementResult(fallback)
