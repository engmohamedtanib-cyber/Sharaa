"""Portfolio construction, trade-cost gate, rebalancing (ENGINE_SPEC §7)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from conftest import D
from engine.portfolio import (
    Candidate,
    ExecutionFeesError,
    build_weights,
    contribution_rebalance,
    holdings_bounds,
    rebalance_needed,
    target_weight,
    trade_cost,
)


def test_target_weight_bands(cfg):
    assert target_weight(D("92"), cfg) == D("0.275")
    assert target_weight(D("85"), cfg) == D("0.225")
    assert target_weight(D("80"), cfg) == D("0.175")
    assert target_weight(D("75"), cfg) == D("0.125")
    assert target_weight(D("74.99"), cfg) == D("0")


def test_trade_cost_gate_fails_loudly_when_fees_unset(cfg):
    with pytest.raises(ExecutionFeesError):
        trade_cost(D("1000"), cfg)


def test_trade_cost_gate_computes_when_fees_set(cfg):
    # Inject a fee schedule into a copy of the raw config (config stays immutable).
    raw = {**cfg.raw}
    raw["portfolio"] = {
        **raw["portfolio"],
        "execution": {"commission_pct": 0.001, "min_fee_egp": 5, "levies_pct": 0.0005, "tax_pct": 0.00125},
    }
    cfg2 = replace(cfg, raw=raw)
    tc = trade_cost(D("100000"), cfg2)
    assert tc.round_trip_cost > 0
    assert isinstance(tc.suppressed, bool)


def test_trade_cost_suppresses_uneconomic_small_trade(cfg):
    raw = {**cfg.raw}
    raw["portfolio"] = {
        **raw["portfolio"],
        "execution": {"commission_pct": 0.001, "min_fee_egp": 20, "levies_pct": 0.0005, "tax_pct": 0.00125},
    }
    cfg2 = replace(cfg, raw=raw)
    # A 500 EGP trade pays the 20 EGP minimum twice -> cost >> 1% -> suppressed.
    assert trade_cost(D("500"), cfg2).suppressed is True


def test_holdings_bounds_by_capital(cfg):
    assert holdings_bounds(D("1000"), cfg) == (None, 8)
    assert holdings_bounds(D("5000"), cfg) == (3, 8)
    assert holdings_bounds(D("60000"), cfg) == (3, None)


def test_rebalance_band(cfg):
    # rebalance_band = 0.25 relative drift.
    assert rebalance_needed(D("0.20"), D("0.25"), cfg) is False   # 0.05/0.25 = 0.20
    assert rebalance_needed(D("0.18"), D("0.25"), cfg) is True    # 0.07/0.25 = 0.28


def test_build_weights_respects_caps(cfg):
    cands = [Candidate("A", D("92"), "s1"), Candidate("B", D("86"), "s2"), Candidate("C", D("81"), "s1")]
    w = build_weights(cands, cfg)
    for v in w.values():
        assert v <= D("0.30") + D("1e-9")
    # s1 (A+C) capped at 0.40 sector limit.
    s1 = sum(v for k, v in w.items() if k in ("A", "C"))
    assert s1 <= D("0.40") + D("1e-9")


def test_build_weights_diversified_deploys_full_investable(cfg):
    cands = [Candidate(f"X{i}", D("80"), f"s{i}") for i in range(5)]
    w = build_weights(cands, cfg)
    assert sum(w.values()) == D("0.9")  # investable = 1 - cash_reserve_min


def test_build_weights_empty_when_all_below_threshold(cfg):
    assert build_weights([Candidate("A", D("70"), "s1")], cfg) == {}


def test_contribution_rebalance_prorata():
    out = contribution_rebalance({"A": D("30"), "B": D("10"), "C": D("0")}, D("20"))
    assert out["A"] == D("15")  # 20 * 30/40
    assert out["B"] == D("5")
    assert "C" not in {k for k, v in out.items() if v > 0}


def test_contribution_rebalance_covers_all_when_ample():
    out = contribution_rebalance({"A": D("30"), "B": D("10")}, D("100"))
    assert out == {"A": D("30"), "B": D("10")}
