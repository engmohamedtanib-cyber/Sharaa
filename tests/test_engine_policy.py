"""Investment Policy Statement: tightening allowed, loosening refused.

Every test here is about the same asymmetry. A policy narrows what the engine
may do. Nothing in a policy may widen it, and no mandate switches off the
Shariah gate.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from config_loader import load_policy, load_thresholds
from engine.config import Thresholds
from engine.policy import (
    DEFAULT_POLICY,
    Exclusion,
    InvestmentPolicy,
    PolicyConfigError,
    PolicyRejectedError,
    PolicySupersessionError,
    Verdict,
    apply_policy,
    filter_candidates,
    parse_policy,
    validate_policy,
    validate_supersession,
)
from engine.types import ShariahStatus


def _raw(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "version": 1,
        "effective_from": "2026-07-27",
        "objective": "Grow capital in halal EGX equities.",
        "horizon_years": 5,
        "base_currency": "EGP",
        "limits": {},
        "exclusions": {"tickers": [], "sectors": []},
        "require_shariah_gate": True,
    }
    base.update(overrides)
    return base


# ----------------------------------------------------------------------
# The shipped default
# ----------------------------------------------------------------------
def test_shipped_ips_loads_and_adds_nothing(cfg: Thresholds) -> None:
    p = load_policy()
    assert p.version == 1
    assert p.require_shariah_gate is True
    assert p.excluded_tickers == ()
    # A null limit inherits the engine's own — behaviour equals no policy at all.
    assert p.effective_max_position(cfg) == cfg.constraint("max_single_position")
    assert p.effective_max_sector(cfg) == cfg.constraint("max_single_sector")
    assert p.effective_cash_reserve_min(cfg) == cfg.constraint("cash_reserve_min")
    assert p.effective_max_holdings(cfg) == int(cfg.constraint("max_holdings"))


def test_default_policy_constant_is_valid(cfg: Thresholds) -> None:
    validate_policy(DEFAULT_POLICY, cfg)


# ----------------------------------------------------------------------
# R7 — the gate is not negotiable
# ----------------------------------------------------------------------
def test_policy_cannot_switch_off_the_shariah_gate(cfg: Thresholds) -> None:
    with pytest.raises(PolicyRejectedError, match="R7"):
        parse_policy(_raw(require_shariah_gate=False), cfg)


def test_gate_failure_short_circuits_policy() -> None:
    d = apply_policy("COMI", "Banks", gate_passed=False, status=ShariahStatus.RED, policy=DEFAULT_POLICY)
    assert d.verdict is Verdict.REFUSED_BY_GATE
    assert d.admitted is False
    assert "policy is not consulted" in d.reason


def test_an_excluded_ticker_that_fails_the_gate_reports_the_gate_first() -> None:
    """The gate is the reason, always. Policy never gets credit for the refusal."""
    policy = InvestmentPolicy(
        version=2,
        effective_from="",
        objective="",
        horizon_years=5,
        excluded_tickers=(Exclusion("COMI", "conventional bank", 2),),
    )
    d = apply_policy("COMI", "Banks", gate_passed=False, status=ShariahStatus.RED, policy=policy)
    assert d.verdict is Verdict.REFUSED_BY_GATE


# ----------------------------------------------------------------------
# Tightening allowed, loosening refused
# ----------------------------------------------------------------------
def test_tighter_position_cap_is_accepted_and_binds(cfg: Thresholds) -> None:
    p = parse_policy(_raw(limits={"max_single_position": 0.20}), cfg)
    assert p.effective_max_position(cfg) == Decimal("0.20")


def test_looser_position_cap_is_rejected(cfg: Thresholds) -> None:
    engine_cap = cfg.constraint("max_single_position")
    with pytest.raises(PolicyRejectedError, match="may only tighten"):
        parse_policy(_raw(limits={"max_single_position": engine_cap + Decimal("0.01")}), cfg)


def test_looser_sector_cap_is_rejected(cfg: Thresholds) -> None:
    with pytest.raises(PolicyRejectedError, match="max_single_sector"):
        parse_policy(_raw(limits={"max_single_sector": 0.99}), cfg)


def test_higher_cash_floor_is_accepted_lower_is_rejected(cfg: Thresholds) -> None:
    floor = cfg.constraint("cash_reserve_min")
    p = parse_policy(_raw(limits={"cash_reserve_min": floor + Decimal("0.05")}), cfg)
    assert p.effective_cash_reserve_min(cfg) == floor + Decimal("0.05")
    with pytest.raises(PolicyRejectedError, match="below the engine floor"):
        parse_policy(_raw(limits={"cash_reserve_min": floor - Decimal("0.01")}), cfg)


def test_max_holdings_may_only_tighten(cfg: Thresholds) -> None:
    engine_cap = int(cfg.constraint("max_holdings"))
    p = parse_policy(_raw(limits={"max_holdings": engine_cap - 1}), cfg)
    assert p.effective_max_holdings(cfg) == engine_cap - 1
    with pytest.raises(PolicyRejectedError, match="exceeds the engine cap"):
        parse_policy(_raw(limits={"max_holdings": engine_cap + 1}), cfg)


def test_accept_amber_false_is_a_tightening(cfg: Thresholds) -> None:
    p = parse_policy(_raw(accept_amber=False), cfg)
    d = apply_policy("AAAA", "Materials", gate_passed=True, status=ShariahStatus.AMBER, policy=p)
    assert d.verdict is Verdict.EXCLUDED_BY_STATUS
    green = apply_policy("AAAA", "Materials", gate_passed=True, status=ShariahStatus.GREEN, policy=p)
    assert green.admitted is True


def test_amber_is_admitted_by_default() -> None:
    d = apply_policy("AAAA", "Materials", gate_passed=True, status=ShariahStatus.AMBER, policy=DEFAULT_POLICY)
    assert d.admitted is True


# ----------------------------------------------------------------------
# Exclusions
# ----------------------------------------------------------------------
def test_ticker_exclusion_applies_case_insensitively(cfg: Thresholds) -> None:
    p = parse_policy(
        _raw(exclusions={"tickers": [{"key": "aaaa", "reason": "family conflict of interest"}], "sectors": []}),
        cfg,
    )
    d = apply_policy("AAAA", "Materials", gate_passed=True, status=ShariahStatus.GREEN, policy=p)
    assert d.verdict is Verdict.EXCLUDED_BY_TICKER
    assert "family conflict" in d.reason


def test_sector_exclusion_applies_case_insensitively(cfg: Thresholds) -> None:
    p = parse_policy(
        _raw(exclusions={"tickers": [], "sectors": [{"key": "Tobacco", "reason": "user refuses"}]}),
        cfg,
    )
    d = apply_policy("AAAA", "tobacco", gate_passed=True, status=ShariahStatus.GREEN, policy=p)
    assert d.verdict is Verdict.EXCLUDED_BY_SECTOR


def test_blank_sector_exclusion_is_refused_at_parse(cfg: Thresholds) -> None:
    """A blank key would match every company whose sector we failed to capture."""
    with pytest.raises(PolicyConfigError, match="needs a ticker or sector"):
        parse_policy(_raw(exclusions={"tickers": [], "sectors": [{"key": "", "reason": "x"}]}), cfg)


def test_company_with_no_sector_is_not_caught_by_a_sector_exclusion(cfg: Thresholds) -> None:
    p = parse_policy(
        _raw(exclusions={"tickers": [], "sectors": [{"key": "Tobacco", "reason": "user refuses"}]}),
        cfg,
    )
    d = apply_policy("AAAA", "", gate_passed=True, status=ShariahStatus.GREEN, policy=p)
    assert d.admitted is True


def test_exclusion_without_reason_is_refused() -> None:
    with pytest.raises(PolicyConfigError, match="must state why"):
        Exclusion("AAAA", "", 1)


def test_exclusion_without_key_is_refused() -> None:
    with pytest.raises(PolicyConfigError, match="needs a ticker or sector"):
        Exclusion("  ", "reason", 1)


def test_same_key_as_ticker_and_sector_is_refused(cfg: Thresholds) -> None:
    with pytest.raises(PolicyConfigError, match="both ticker and sector"):
        parse_policy(
            _raw(
                exclusions={
                    "tickers": [{"key": "GOLD", "reason": "a"}],
                    "sectors": [{"key": "GOLD", "reason": "b"}],
                }
            ),
            cfg,
        )


# ----------------------------------------------------------------------
# Supersession
# ----------------------------------------------------------------------
def _with_exclusions(version: int, keys: tuple[str, ...], rescinds: tuple[str, ...] = ()) -> InvestmentPolicy:
    return InvestmentPolicy(
        version=version,
        effective_from="",
        objective="",
        horizon_years=5,
        excluded_tickers=tuple(Exclusion(k, "stated reason", version) for k in keys),
        rescinds=rescinds,
    )


def test_adding_an_exclusion_supersedes_cleanly() -> None:
    validate_supersession(_with_exclusions(1, ("AAAA",)), _with_exclusions(2, ("AAAA", "BBBB")))


def test_silently_dropping_an_exclusion_is_refused() -> None:
    with pytest.raises(PolicySupersessionError, match="without being rescinded"):
        validate_supersession(_with_exclusions(1, ("AAAA", "BBBB")), _with_exclusions(2, ("AAAA",)))


def test_explicitly_rescinding_an_exclusion_is_allowed() -> None:
    validate_supersession(
        _with_exclusions(1, ("AAAA", "BBBB")),
        _with_exclusions(2, ("AAAA",), rescinds=("BBBB",)),
    )


def test_dropping_a_sector_exclusion_is_also_caught() -> None:
    old = InvestmentPolicy(
        version=1, effective_from="", objective="", horizon_years=5,
        excluded_sectors=(Exclusion("Tobacco", "user refuses", 1),),
    )
    new = InvestmentPolicy(version=2, effective_from="", objective="", horizon_years=5)
    with pytest.raises(PolicySupersessionError, match="Tobacco"):
        validate_supersession(old, new)


def test_version_must_increase() -> None:
    with pytest.raises(PolicySupersessionError, match="does not supersede"):
        validate_supersession(_with_exclusions(2, ()), _with_exclusions(2, ()))
    with pytest.raises(PolicySupersessionError):
        validate_supersession(_with_exclusions(3, ()), _with_exclusions(2, ()))


# ----------------------------------------------------------------------
# Shape errors
# ----------------------------------------------------------------------
def test_missing_version_is_refused(cfg: Thresholds) -> None:
    raw = _raw()
    del raw["version"]
    with pytest.raises(PolicyConfigError, match="integer `version`"):
        parse_policy(raw, cfg)


@pytest.mark.parametrize("bad", [0, -1])
def test_version_below_one_is_refused(cfg: Thresholds, bad: int) -> None:
    with pytest.raises(PolicyConfigError, match="version must be >= 1"):
        parse_policy(_raw(version=bad), cfg)


def test_non_egp_base_currency_is_refused(cfg: Thresholds) -> None:
    with pytest.raises(PolicyConfigError, match="EGP only"):
        parse_policy(_raw(base_currency="USD"), cfg)


@pytest.mark.parametrize("bad", ["five", 0])
def test_bad_horizon_is_refused(cfg: Thresholds, bad: object) -> None:
    with pytest.raises(PolicyConfigError, match="horizon_years"):
        parse_policy(_raw(horizon_years=bad), cfg)


def test_negative_contribution_is_refused(cfg: Thresholds) -> None:
    with pytest.raises(PolicyConfigError, match="monthly_contribution"):
        parse_policy(_raw(monthly_contribution=-100), cfg)


def test_non_numeric_limit_is_refused(cfg: Thresholds) -> None:
    with pytest.raises(PolicyConfigError, match="not a number"):
        parse_policy(_raw(limits={"max_single_position": "a third"}), cfg)


def test_non_positive_limit_is_refused(cfg: Thresholds) -> None:
    with pytest.raises(PolicyConfigError, match="must be positive"):
        parse_policy(_raw(limits={"max_single_position": 0}), cfg)


@pytest.mark.parametrize("bad", [-0.1, 1])
def test_cash_reserve_outside_unit_interval_is_refused(cfg: Thresholds, bad: float) -> None:
    with pytest.raises(PolicyConfigError, match=r"\[0,1\)"):
        parse_policy(_raw(limits={"cash_reserve_min": bad}), cfg)


def test_zero_max_holdings_is_refused(cfg: Thresholds) -> None:
    with pytest.raises(PolicyConfigError, match="max_holdings must be >= 1"):
        parse_policy(_raw(limits={"max_holdings": 0}), cfg)


def test_non_mapping_limits_and_exclusions_are_refused(cfg: Thresholds) -> None:
    with pytest.raises(PolicyConfigError, match="`limits` must be a mapping"):
        parse_policy(_raw(limits=[1, 2]), cfg)
    with pytest.raises(PolicyConfigError, match="must be a list"):
        parse_policy(_raw(exclusions={"tickers": "AAAA"}), cfg)
    with pytest.raises(PolicyConfigError, match="each entry must be a mapping"):
        parse_policy(_raw(exclusions={"tickers": ["AAAA"]}), cfg)


def test_contribution_and_note_round_trip(cfg: Thresholds) -> None:
    p = parse_policy(_raw(monthly_contribution="2500.50", note="pay day is the 25th"), cfg)
    assert p.monthly_contribution == Decimal("2500.50")
    assert p.note == "pay day is the 25th"


# ----------------------------------------------------------------------
# Bulk filtering
# ----------------------------------------------------------------------
def test_filter_candidates_returns_admitted_and_every_refusal(cfg: Thresholds) -> None:
    p = parse_policy(
        _raw(
            accept_amber=False,
            exclusions={
                "tickers": [{"key": "BBBB", "reason": "already own it elsewhere"}],
                "sectors": [{"key": "Tobacco", "reason": "user refuses"}],
            },
        ),
        cfg,
    )
    admitted, decisions = filter_candidates(
        {
            "AAAA": ("Materials", True, ShariahStatus.GREEN),
            "BBBB": ("Materials", True, ShariahStatus.GREEN),
            "CCCC": ("Tobacco", True, ShariahStatus.GREEN),
            "DDDD": ("Banks", False, ShariahStatus.RED),
            "EEEE": ("Industrials", True, ShariahStatus.AMBER),
        },
        p,
    )
    assert admitted == ("AAAA",)
    verdicts = {d.ticker: d.verdict for d in decisions}
    assert verdicts == {
        "AAAA": Verdict.ADMITTED,
        "BBBB": Verdict.EXCLUDED_BY_TICKER,
        "CCCC": Verdict.EXCLUDED_BY_SECTOR,
        "DDDD": Verdict.REFUSED_BY_GATE,
        "EEEE": Verdict.EXCLUDED_BY_STATUS,
    }
    # Every decision carries the version it was made under (R5).
    assert {d.policy_version for d in decisions} == {p.version}


def test_filter_candidates_is_deterministic(cfg: Thresholds) -> None:
    candidates = {
        "CCCC": ("Materials", True, ShariahStatus.GREEN),
        "AAAA": ("Materials", True, ShariahStatus.GREEN),
        "BBBB": ("Materials", True, ShariahStatus.GREEN),
    }
    first = filter_candidates(candidates, DEFAULT_POLICY)
    second = filter_candidates(dict(reversed(list(candidates.items()))), DEFAULT_POLICY)
    assert first == second
    assert first[0] == ("AAAA", "BBBB", "CCCC")


def test_load_policy_matches_thresholds_version_expectations() -> None:
    """Loading must not depend on lru_cache ordering with thresholds."""
    assert load_policy() is load_policy()
    assert load_thresholds() is load_thresholds()
