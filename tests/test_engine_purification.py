"""Purification / tathir ledger (ENGINE_SPEC §8)."""

from __future__ import annotations

from dataclasses import replace

from conftest import D
from engine.purification import purify


def test_purification_ratio_and_amount(cfg):
    r = purify(D("10"), D("100"), D("50"), cfg)
    assert r.ratio == D("0.1")
    assert r.amount_due == D("5")   # 50 dividends * 0.1
    assert r.carried_forward == D("0")


def test_loss_period_carries_forward(cfg):
    r = purify(D("10"), D("-5"), D("50"), cfg)
    assert r.ratio is None
    assert r.amount_due is None
    assert r.carried_forward == D("10")
    assert r.net_profit_nonpositive is True


def test_carried_in_folds_into_next_profitable_period(cfg):
    r = purify(D("10"), D("100"), D("50"), cfg, carried_in=D("10"))
    assert r.ratio == D("0.2")       # (10 + 10) / 100
    assert r.amount_due == D("10")   # 50 * 0.2


def test_total_return_basis(cfg):
    raw = {**cfg.raw, "purification": {"basis": "TOTAL_RETURN"}}
    cfg2 = replace(cfg, raw=raw)
    r = purify(D("10"), D("100"), D("50"), cfg2, realised_gains=D("30"))
    # base = dividends 50 + gains 30 = 80 ; * 0.1 = 8
    assert r.amount_due == D("8")
    assert r.basis == "TOTAL_RETURN"
