"""Determinism (ENGINE_SPEC §10): 100 runs, one output hash. Severity-1 if not."""

from __future__ import annotations

import hashlib

from conftest import D, strong_inputs
from engine.decisions import DecisionContext, decide
from engine.scoring import score_company
from engine.shariah import diagnose_breach, run_gate
from engine.types import DataStatus


def _run_full_engine(cfg) -> str:
    inp = strong_inputs()
    gate = run_gate(inp.financials, inp.market.market_cap, cfg)
    breach = diagnose_breach(gate, DataStatus.VALIDATED, 30, cfg)
    res = score_company(inp, gate, cfg)
    assert res is not None
    ctx = DecisionContext(
        breach=breach.breach, status=gate.overall_status, score=res.total,
        valuation_gap=D("0.28"), held=False, below_target_weight=False, vetoes=res.vetoes,
    )
    dec = decide(ctx, cfg)
    parts = [
        gate.overall_status.value, str(gate.worst_utilisation), breach.breach.value,
        str(res.total), res.band,
        *[f"{s.subcriterion}:{s.points}:{s.band_matched}" for s in res.subscores],
        dec.decision.value, dec.reason, dec.falsification_condition,
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def test_engine_determinism(cfg):
    hashes = {_run_full_engine(cfg) for _ in range(100)}
    assert len(hashes) == 1
