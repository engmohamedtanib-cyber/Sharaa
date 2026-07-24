"""Decision engine priority order, vetoes, matrix, field enforcement (§5)."""

from __future__ import annotations

import pytest

from conftest import D
from engine.decisions import (
    VETO_LATE_FILINGS,
    DecisionContext,
    VetoInputs,
    collect_vetoes,
    decide,
)
from engine.types import BreachType, DecisionType, ShariahStatus


def ctx(**kw) -> DecisionContext:
    base = dict(
        breach=BreachType.NONE, status=ShariahStatus.GREEN, score=D("90"),
        valuation_gap=D("0.30"), held=False, below_target_weight=False, vetoes=(),
    )
    base.update(kw)
    return DecisionContext(**base)


# ---- Priority 1: Shariah override (§5.1) ----------------------------
@pytest.mark.parametrize(
    "breach,expected",
    [
        (BreachType.TYPE_1_ACTIVITY, DecisionType.REMOVE),
        (BreachType.TYPE_2_STRUCTURAL, DecisionType.REMOVE),
        (BreachType.TYPE_3_DENOMINATOR, DecisionType.HOLD_FROZEN),
        (BreachType.TYPE_4_DATA, DecisionType.HOLD_FROZEN),
    ],
)
def test_shariah_override(cfg, breach, expected):
    assert decide(ctx(breach=breach, status=ShariahStatus.RED), cfg).decision is expected


def test_orange_status_frozen(cfg):
    assert decide(ctx(status=ShariahStatus.ORANGE), cfg).decision is DecisionType.HOLD_FROZEN


def test_shariah_override_beats_score(cfg):
    # A stellar score cannot rescue a Type 1 breach (R7).
    d = decide(ctx(breach=BreachType.TYPE_1_ACTIVITY, score=D("99"), status=ShariahStatus.RED), cfg)
    assert d.decision is DecisionType.REMOVE


# ---- Priority 2: vetoes (§5.2) --------------------------------------
def test_veto_removes(cfg):
    d = decide(ctx(vetoes=("LIQUIDITY_6B",)), cfg)
    assert d.decision is DecisionType.REMOVE
    assert d.veto_fired == "LIQUIDITY_6B"


def test_collect_vetoes_dedups_and_orders():
    v = VetoInputs(scoring_vetoes=("LIQUIDITY_6B",), two_consecutive_late_filings=True)
    out = collect_vetoes(v)
    assert out[0] == "LIQUIDITY_6B"
    assert VETO_LATE_FILINGS in out
    assert len(out) == len(set(out))


# ---- Priority 3: score & valuation matrix (§5.3) --------------------
def test_buy_high_score_deep_discount(cfg):
    assert decide(ctx(score=D("90"), valuation_gap=D("0.25"), held=False), cfg).decision is DecisionType.BUY


def test_add_high_score_held_below_target(cfg):
    d = decide(ctx(score=D("88"), valuation_gap=D("0.15"), held=True, below_target_weight=True), cfg)
    assert d.decision is DecisionType.ADD


def test_hold_high_score_near_fair_value(cfg):
    d = decide(ctx(score=D("88"), valuation_gap=D("0.00"), held=True), cfg)
    assert d.decision is DecisionType.HOLD


def test_reduce_high_score_overvalued(cfg):
    d = decide(ctx(score=D("88"), valuation_gap=D("-0.25"), held=True), cfg)
    assert d.decision is DecisionType.REDUCE


def test_starter_buy_mid_score_deep_discount(cfg):
    d = decide(ctx(score=D("78"), valuation_gap=D("0.30"), held=False), cfg)
    assert d.decision is DecisionType.BUY


def test_mid_score_held_holds(cfg):
    assert decide(ctx(score=D("78"), valuation_gap=D("0.0"), held=True), cfg).decision is DecisionType.HOLD


def test_watch_band_not_held_no_action(cfg):
    assert decide(ctx(score=D("70"), held=False), cfg).decision is DecisionType.NO_ACTION


def test_reduce_band_held(cfg):
    assert decide(ctx(score=D("60"), held=True), cfg).decision is DecisionType.REDUCE


def test_exit_band_sells(cfg):
    assert decide(ctx(score=D("50"), held=True), cfg).decision is DecisionType.SELL


# ---- Mandatory fields (§5.5) ----------------------------------------
def test_every_decision_has_substantive_fields(cfg):
    for c in [
        ctx(),
        ctx(breach=BreachType.TYPE_3_DENOMINATOR),
        ctx(vetoes=("LIQUIDITY_6B",)),
        ctx(score=D("50"), held=True),
    ]:
        d = decide(c, cfg)
        assert len(d.reason.strip()) >= 30
        assert len(d.falsification_condition.strip()) >= 20
