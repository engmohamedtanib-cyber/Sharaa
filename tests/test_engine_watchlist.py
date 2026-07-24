"""Watchlist assignment and movement (ENGINE_SPEC §6)."""

from __future__ import annotations

from conftest import D
from engine.types import BreachType, ShariahStatus, WatchlistName
from engine.watchlist import (
    MovementSignals,
    WatchlistSignals,
    assign_watchlist,
    resolve_movement,
)


def sig(**kw) -> WatchlistSignals:
    base = dict(
        score=D("85"), status=ShariahStatus.GREEN, gap=D("0.05"),
        six_b_points=D("4"), has_veto=False, breach=BreachType.NONE,
        rsi=D("50"), pct_above_200dma=D("0.05"),
    )
    base.update(kw)
    return WatchlistSignals(**base)


def test_high_conviction_entry(cfg):
    assert assign_watchlist(sig(score=D("85"), gap=D("0.0")), cfg) is WatchlistName.HIGH_CONVICTION


def test_remove_on_low_score(cfg):
    assert assign_watchlist(sig(score=D("60")), cfg) is WatchlistName.REMOVE


def test_remove_on_breach(cfg):
    assert assign_watchlist(sig(breach=BreachType.TYPE_1_ACTIVITY), cfg) is WatchlistName.REMOVE


def test_remove_on_veto(cfg):
    assert assign_watchlist(sig(has_veto=True), cfg) is WatchlistName.REMOVE


def test_buy_on_pullback_via_gap(cfg):
    s = sig(score=D("80"), gap=D("-0.20"))
    assert assign_watchlist(s, cfg) is WatchlistName.BUY_ON_PULLBACK


def test_buy_on_pullback_via_rsi(cfg):
    s = sig(score=D("80"), gap=D("0.0"), rsi=D("75"))
    assert assign_watchlist(s, cfg) is WatchlistName.BUY_ON_PULLBACK


def test_hold_band(cfg):
    assert assign_watchlist(sig(score=D("70")), cfg) is WatchlistName.HOLD


def test_hold_when_amber_high_score(cfg):
    assert assign_watchlist(sig(score=D("85"), status=ShariahStatus.AMBER), cfg) is WatchlistName.HOLD


# ---- movement rules (§6) --------------------------------------------
def test_hold_to_high_conviction_needs_two_reviews(cfg):
    s = sig(score=D("83"), gap=D("0.0"))
    m1 = MovementSignals(consecutive_hc_reviews=1)
    m2 = MovementSignals(consecutive_hc_reviews=2)
    assert resolve_movement(WatchlistName.HOLD, s, m1, cfg).list_name is WatchlistName.HOLD
    assert resolve_movement(WatchlistName.HOLD, s, m2, cfg).list_name is WatchlistName.HIGH_CONVICTION


def test_stale_pullback_forces_hold_and_reunderwrite(cfg):
    s = sig(score=D("80"), gap=D("0.0"), rsi=D("50"), pct_above_200dma=D("0.0"))
    res = resolve_movement(WatchlistName.BUY_ON_PULLBACK, s, MovementSignals(quarters_on_pullback=4), cfg)
    assert res.list_name is WatchlistName.HOLD
    assert res.needs_reunderwrite is True


def test_remove_to_hold_requires_two_compliant_and_admission(cfg):
    s = sig(score=D("72"))
    ok = MovementSignals(consecutive_compliant_reviews=2, admission_ok=True)
    not_ok = MovementSignals(consecutive_compliant_reviews=1, admission_ok=True)
    assert resolve_movement(WatchlistName.REMOVE, s, ok, cfg).list_name is WatchlistName.HOLD
    assert resolve_movement(WatchlistName.REMOVE, s, not_ok, cfg).list_name is WatchlistName.REMOVE


def test_high_conviction_to_hold_on_score_drop(cfg):
    s = sig(score=D("80"), gap=D("0.0"))
    res = resolve_movement(WatchlistName.HIGH_CONVICTION, s, MovementSignals(), cfg)
    assert res.list_name is WatchlistName.HOLD
