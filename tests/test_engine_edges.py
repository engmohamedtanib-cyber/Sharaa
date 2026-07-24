"""Edge/guard branches across the engine to complete branch coverage."""

from __future__ import annotations

import pytest

from conftest import D
from engine.decisions import (
    VETO_LATE_FILINGS,
    VETO_MODEL_CHANGE,
    VETO_OCF_DEBT,
    VETO_RESTATEMENT,
    VETO_SHAREHOLDER,
    DecisionContext,
    VetoInputs,
    collect_vetoes,
    decide,
)
from engine.portfolio import contribution_rebalance, rebalance_needed, target_weight, trade_cost
from engine.scoring import ScoreResult
from engine.shariah import admission_test, cure_window
from engine.stats import linear_slope, mean, population_stdev
from engine.types import BreachType, DecisionType, ShariahStatus, WatchlistName
from engine.valuation import fair_value, justified_pe_fair_value, peer_relative_fair_value
from engine.watchlist import MovementSignals, WatchlistSignals, resolve_movement


# ---- stats guards ----------------------------------------------------
def test_stats_raise_on_empty_or_short():
    with pytest.raises(ValueError):
        mean(())
    with pytest.raises(ValueError):
        population_stdev((D("1"),))
    with pytest.raises(ValueError):
        linear_slope((D("1"),))


def test_linear_slope_flat_series_zero():
    assert linear_slope((D("1"), D("1"), D("1"))) == D("0")


# ---- decisions: full veto collection --------------------------------
def test_collect_all_vetoes():
    v = VetoInputs(
        auditor_resigned=True, restatement_confirmed=True, business_model_change=True,
        controlling_shareholder_adverse=True, two_q_negative_ocf_rising_debt=True,
        two_consecutive_late_filings=True,
    )
    out = collect_vetoes(v)
    for tag in (VETO_RESTATEMENT, VETO_MODEL_CHANGE, VETO_SHAREHOLDER, VETO_OCF_DEBT, VETO_LATE_FILINGS):
        assert tag in out


def test_score_matrix_requires_score():
    ctx = DecisionContext(
        breach=BreachType.NONE, status=ShariahStatus.GREEN, score=None,
        valuation_gap=D("0.1"), held=True, below_target_weight=False,
    )
    with pytest.raises(ValueError):
        decide(ctx, __import__("config_loader").load_thresholds())


def _ctx(**kw):
    base = dict(
        breach=BreachType.NONE, status=ShariahStatus.GREEN, score=D("90"),
        valuation_gap=D("0.30"), held=False, below_target_weight=False, vetoes=(),
    )
    base.update(kw)
    return DecisionContext(**base)


def test_matrix_uncovered_regions(cfg):
    # score>=85, not held, shallow discount -> NO_ACTION
    assert decide(_ctx(score=D("90"), valuation_gap=D("0.10"), held=False), cfg).decision is DecisionType.NO_ACTION
    # score>=85, held, uncovered gap region -> HOLD (default row)
    assert decide(_ctx(score=D("90"), valuation_gap=D("0.20"), held=True, below_target_weight=False), cfg).decision is DecisionType.HOLD
    # score 75-85, not held, shallow discount -> NO_ACTION
    assert decide(_ctx(score=D("78"), valuation_gap=D("0.10"), held=False), cfg).decision is DecisionType.NO_ACTION
    # watch band, held -> HOLD
    assert decide(_ctx(score=D("70"), held=True), cfg).decision is DecisionType.HOLD
    # reduce band, not held -> NO_ACTION
    assert decide(_ctx(score=D("60"), held=False), cfg).decision is DecisionType.NO_ACTION
    # exit band, not held -> NO_ACTION
    assert decide(_ctx(score=D("50"), held=False), cfg).decision is DecisionType.NO_ACTION


# ---- portfolio guards ------------------------------------------------
def test_target_weight_below_all_bands_is_zero(cfg):
    # score is always covered by the 0-floor band; drive the None path via a
    # degenerate call using an out-of-range negative score.
    assert target_weight(D("-5"), cfg) == D("0")


def test_trade_cost_rejects_nonpositive(cfg):
    with pytest.raises(ValueError):
        trade_cost(D("0"), cfg)


def test_rebalance_zero_target(cfg):
    assert rebalance_needed(D("0.05"), D("0"), cfg) is True
    assert rebalance_needed(D("0"), D("0"), cfg) is False


def test_contribution_rebalance_no_shortfall():
    assert contribution_rebalance({"A": D("0")}, D("100")) == {"A": D("0")}
    assert contribution_rebalance({"A": D("10")}, D("0")) == {"A": D("0")}


# ---- shariah cure window + admission --------------------------------
def test_cure_window_only_for_type3():
    assert cure_window(BreachType.TYPE_3_DENOMINATOR, "2026-01-01", "2026-04-01") == ("2026-01-01", "2026-04-01")
    assert cure_window(BreachType.TYPE_2_STRUCTURAL, "2026-01-01", "2026-04-01") == (None, None)


def test_admission_denominator_no_asset_delta():
    # denominator-driven with no total-asset growth info -> rejected
    assert admission_test(ShariahStatus.RED, ShariahStatus.GREEN, D("-0.02"), D("0.40"), None) is False


# ---- valuation extra branches ---------------------------------------
def test_peer_relative_rejects_nonpositive_pe():
    assert peer_relative_fair_value(D("0"), D("2")) is None


def test_justified_pe_valid():
    assert justified_pe_fair_value(D("0.5"), D("0.03"), D("0.12"), D("2")) is not None


def test_fair_value_peer_only():
    assert fair_value(eps_ttm=D("2"), sector_median_pe=D("6")) == D("12")


# ---- watchlist extra transitions ------------------------------------
def sig(**kw):
    base = dict(
        score=D("85"), status=ShariahStatus.GREEN, gap=D("0.05"),
        six_b_points=D("4"), has_veto=False, breach=BreachType.NONE,
        rsi=D("50"), pct_above_200dma=D("0.05"),
    )
    base.update(kw)
    return WatchlistSignals(**base)


def test_pullback_to_high_conviction_on_trigger(cfg):
    s = sig(score=D("85"), gap=D("0.0"))
    mov = MovementSignals(price=D("12"), trigger_price=D("11"))
    assert resolve_movement(WatchlistName.BUY_ON_PULLBACK, s, mov, cfg).list_name is WatchlistName.HIGH_CONVICTION


def test_high_conviction_to_pullback_on_deep_discount(cfg):
    s = sig(score=D("83"), gap=D("-0.20"))
    res = resolve_movement(WatchlistName.HIGH_CONVICTION, s, MovementSignals(), cfg)
    assert res.list_name is WatchlistName.BUY_ON_PULLBACK


def test_high_conviction_stays_when_still_strong(cfg):
    s = sig(score=D("90"), status=ShariahStatus.GREEN, gap=D("0.05"))
    res = resolve_movement(WatchlistName.HIGH_CONVICTION, s, MovementSignals(), cfg)
    assert res.list_name is WatchlistName.HIGH_CONVICTION


def test_no_prior_falls_back_to_entry(cfg):
    s = sig(score=D("70"))
    assert resolve_movement(None, s, MovementSignals(), cfg).list_name is WatchlistName.HOLD


def test_score_result_sub_keyerror():
    res = ScoreResult(subscores=(), pillar_totals={}, total=D("0"), band="EXIT", vetoes=(), alerts=())
    with pytest.raises(KeyError):
        res.sub("1A")
