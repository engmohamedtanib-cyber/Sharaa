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
    min_economic_trade_value,
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


def with_fees(cfg, **overrides):
    """A config copy with an injected execution block (config stays immutable)."""
    raw = {**cfg.raw}
    raw["portfolio"] = {**raw["portfolio"], "execution": overrides}
    return replace(cfg, raw=raw)


@pytest.mark.parametrize("absent", ["fixed_fee_egp", "commission_pct", "min_fee_egp", "levies_pct", "tax_pct"])
def test_trade_cost_gate_fails_loudly_when_any_fee_is_unset(cfg, absent):
    """ENGINE_SPEC §7.3: never ship guessed fee values — fail loudly if unset."""
    fees = {"fixed_fee_egp": 2, "commission_pct": 0.001, "min_fee_egp": 1, "levies_pct": 0.00025, "tax_pct": 0.00005}
    fees[absent] = None
    with pytest.raises(ExecutionFeesError, match=absent):
        trade_cost(D("1000"), with_fees(cfg, **fees))


def test_trade_cost_reproduces_the_brokers_own_worked_example(cfg):
    """The shipped config must reproduce Thndr's published example to the piastre.

    Source (`thndr/fee_schedule/2026-07-26`): a 5 000 EGP order filling as one
    transaction costs 2.00 brokerage fixed + 5.00 variable + 0.50 EGX + 0.50
    MCDR + 1.00 FRA (minimum applies) + 0.25 risk insurance = 9.25 per side.

    This is the test that makes the fee config self-checking rather than four
    numbers someone typed in.
    """
    tc = trade_cost(D("5000"), cfg)
    assert tc.round_trip_cost == D("18.50")  # 9.25 per side, both sides charged


def test_the_flat_fee_makes_cost_size_dependent(cfg):
    """The reason the model needed a fixed term: cost is not a constant %.

    Under the old purely-proportional model these two would have been equal,
    and a 500 EGP trade would have looked exactly as economic as a 500 000 one.
    """
    small = trade_cost(D("500"), cfg)
    large = trade_cost(D("500000"), cfg)
    assert small.cost_pct > large.cost_pct * 5
    assert small.suppressed is True
    assert large.suppressed is False


def test_a_1000_egp_trade_is_economic_but_splitting_it_three_ways_is_not(cfg):
    """The concrete question the user asked, answered by the engine.

    One 1 000 EGP position clears the 1% round-trip gate. The same 1 000 split
    across the three positions `min_holdings` requires does not — each ~333 EGP
    order pays the same flat 3 EGP.
    """
    assert trade_cost(D("1000"), cfg).suppressed is False
    assert trade_cost(D("1000") / 3, cfg).suppressed is True


def test_min_economic_trade_value_is_the_breakeven_of_the_gate(cfg):
    """Just below it suppresses, just above it does not — so the number the
    system reports is the number the gate actually enforces."""
    floor = min_economic_trade_value(cfg)
    assert trade_cost(floor - D("1"), cfg).suppressed is True
    assert trade_cost(floor + D("1"), cfg).suppressed is False


def test_min_economic_trade_value_refuses_when_percentages_alone_exceed_the_gate(cfg):
    """A broker whose proportional fees breach the ceiling cannot be served at
    any trade size. Say so rather than return a meaningless number."""
    cfg2 = with_fees(cfg, fixed_fee_egp=0, commission_pct=0.02, min_fee_egp=0, levies_pct=0, tax_pct=0)
    with pytest.raises(ExecutionFeesError, match="no trade size is economic"):
        min_economic_trade_value(cfg2)


def test_regulator_minimum_binds_only_on_small_orders(cfg):
    """FRA is max(0.005% * V, 1 EGP). The floor stops binding at 20 000 EGP."""
    below = trade_cost(D("10000"), cfg).round_trip_cost
    # 2 + 10 + 2.50 + max(0.50, 1.00) = 15.50 per side
    assert below == D("31.00")
    above = trade_cost(D("40000"), cfg).round_trip_cost
    # 2 + 40 + 10 + max(2.00, 1.00) = 54.00 per side
    assert above == D("108.00")


def test_trade_cost_rejects_a_non_positive_value(cfg):
    with pytest.raises(ValueError, match="must be positive"):
        trade_cost(D("0"), cfg)


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
