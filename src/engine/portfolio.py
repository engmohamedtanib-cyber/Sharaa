"""Portfolio construction, rebalancing and the trade-cost gate (ENGINE_SPEC §7).

Pure module. The trade-cost gate FAILS LOUDLY when execution fees are unset —
the engine must not ship with guessed Thndr fees (§7.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from common.decimals import ONE, ZERO, D
from engine.bands import match
from engine.config import Thresholds


@dataclass(frozen=True)
class Candidate:
    ident: str
    score: Decimal
    sector: str


@dataclass(frozen=True)
class TradeCost:
    round_trip_cost: Decimal
    cost_pct: Decimal
    suppressed: bool


class ExecutionFeesError(RuntimeError):
    """Raised when the trade-cost gate is asked to run without verified fees."""


def target_weight(score: Decimal, cfg: Thresholds) -> Decimal:
    """Target weight of invested capital for a score (§7.1)."""
    band = match(score, cfg.target_weight_bands())
    if band is None:
        return ZERO
    return D(band.payload["weight"])


def holdings_bounds(capital_egp: Decimal, cfg: Thresholds) -> tuple[int | None, int | None]:
    """(min_holdings, max_holdings) applicable at this capital (§7.2)."""
    c = cfg.portfolio()["constraints"]
    min_h = int(c["min_holdings"]) if capital_egp >= Decimal(str(c["min_holdings_capital"])) else None
    max_h = int(c["max_holdings"]) if capital_egp < Decimal(str(c["max_holdings_capital"])) else None
    return (min_h, max_h)


# ----------------------------------------------------------------------
# Trade-cost gate (§7.3)
# ----------------------------------------------------------------------
_FEE_KEYS = ("fixed_fee_egp", "commission_pct", "min_fee_egp", "levies_pct", "tax_pct")


def _fee_params(cfg: Thresholds) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal]:
    ex = cfg.execution()
    missing = [k for k in _FEE_KEYS if ex.get(k) is None]
    if missing:
        raise ExecutionFeesError(
            "execution fees are unset in config/thresholds.yaml "
            f"({', '.join(missing)}); verify against Thndr's schedule before any live run (§7.3)"
        )
    return (
        Decimal(str(ex["fixed_fee_egp"])),
        Decimal(str(ex["commission_pct"])),
        Decimal(str(ex["min_fee_egp"])),
        Decimal(str(ex["levies_pct"])),
        Decimal(str(ex["tax_pct"])),
    )


def trade_cost(trade_value: Decimal, cfg: Thresholds) -> TradeCost:
    """Estimate round-trip cost and decide the §7.3 economic-suppression gate.

    The cost model is **fixed plus percentage**, because that is the shape of
    the real schedule (``thndr/fee_schedule/2026-07-26``): a flat 2 EGP per
    order, a 0.1% brokerage commission, per-order market levies, and a
    regulator levy carrying a 1 EGP floor::

        per_side = fixed + commission_pct*V + levies_pct*V + max(tax_pct*V, min_fee)

    The flat term is the whole point. A purely proportional model makes cost a
    constant percentage at every size, so a 300 EGP trade looks exactly as
    economic as a 300 000 EGP one. It is not, and the error runs in the
    dangerous direction — understating the cost of small trades is what lets a
    system recommend a trade that loses money on execution alone
    (``decisions/0007``).

    Doubled for the round trip: the schedule states that brokerage and
    third-party fees apply to buy *and* sell orders.
    """
    if trade_value <= ZERO:
        raise ValueError("trade_value must be positive")
    fixed, commission_pct, min_fee, levies_pct, tax_pct = _fee_params(cfg)
    per_side = (
        fixed
        + commission_pct * trade_value
        + levies_pct * trade_value
        + max(tax_pct * trade_value, min_fee)
    )
    round_trip = Decimal(2) * per_side
    cost_pct = round_trip / trade_value
    ceiling = cfg.constraint("max_trade_cost_pct")
    return TradeCost(round_trip_cost=round_trip, cost_pct=cost_pct, suppressed=cost_pct > ceiling)


def min_economic_trade_value(cfg: Thresholds) -> Decimal:
    """Smallest trade value whose round-trip cost still clears the gate.

    Exists so the system can answer "how much do I actually need?" with a
    number instead of a refusal. Solving ``2*(fixed + max_component)/V <=
    ceiling`` for V, taking the regulator floor as binding — which it is for
    any order this portfolio will place::

        V >= 2*(fixed + min_fee) / (ceiling - 2*(commission_pct + levies_pct))

    Raises :class:`ExecutionFeesError` if the percentage components alone
    already exceed the ceiling, in which case no trade of any size is economic
    and the honest answer is that this broker cannot serve this strategy.
    """
    fixed, commission_pct, min_fee, levies_pct, _tax_pct = _fee_params(cfg)
    ceiling = cfg.constraint("max_trade_cost_pct")
    proportional = Decimal(2) * (commission_pct + levies_pct)
    if proportional >= ceiling:
        raise ExecutionFeesError(
            f"percentage fees alone ({proportional}) meet or exceed the "
            f"{ceiling} round-trip ceiling; no trade size is economic"
        )
    return (Decimal(2) * (fixed + min_fee)) / (ceiling - proportional)


# ----------------------------------------------------------------------
# Rebalancing (§7.4)
# ----------------------------------------------------------------------
def rebalance_needed(actual_weight: Decimal, target: Decimal, cfg: Thresholds) -> bool:
    """Band-based rebalance test: relative drift beyond ``rebalance_band``."""
    if target == ZERO:
        return actual_weight != ZERO  # any weight in a zero-target name must go
    band = cfg.constraint("rebalance_band")
    return abs(actual_weight - target) / target > band


def contribution_rebalance(
    underweights: dict[str, Decimal],
    contribution: Decimal,
) -> dict[str, Decimal]:
    """Direct a pending contribution to underweight positions (§7.4).

    ``underweights`` maps ident -> shortfall (target_value - actual_value), only
    positive shortfalls. Allocates the contribution pro-rata to shortfalls,
    never selling. Prefer this to selling at low capital.
    """
    positive = {k: v for k, v in underweights.items() if v > ZERO}
    total_short = sum(positive.values(), ZERO)
    if total_short == ZERO or contribution <= ZERO:
        return {k: ZERO for k in underweights}
    if contribution >= total_short:
        # Enough to top every underweight to target; leftover stays as cash.
        return dict(positive)
    return {k: contribution * (v / total_short) for k, v in positive.items()}


# ----------------------------------------------------------------------
# Weight construction (§7.1, §7.2)
# ----------------------------------------------------------------------
def build_weights(candidates: list[Candidate], cfg: Thresholds) -> dict[str, Decimal]:
    """Construct constrained target weights (of total capital) by water-filling.

    Deterministic: candidates are processed in a stable (score desc, ident asc)
    order. Score-band target weights seed a proportional allocation of the
    investable fraction (``1 - cash_reserve_min``); positions are then capped at
    ``max_single_position`` and sectors at ``max_single_sector``, with the
    freed capital redistributed only into genuine headroom. Any capital that
    cannot be deployed within the caps stays as **cash** — weights are never
    re-inflated past a cap. Sub-minimum positions are dropped to cash.

    A returned weight-sum below ``investable`` (cash above ``cash_reserve_max``)
    is a real signal that the candidate set is too concentrated to deploy the
    capital within the risk caps — surfaced, not hidden.
    """
    c = cfg.portfolio()["constraints"]
    max_pos = Decimal(str(c["max_single_position"]))
    max_sector = Decimal(str(c["max_single_sector"]))
    min_pos = Decimal(str(c["min_position_size"]))
    investable = ONE - Decimal(str(c["cash_reserve_min"]))
    tiny = Decimal("0.0000001")

    selected = sorted(
        (c2 for c2 in candidates if target_weight(c2.score, cfg) > ZERO),
        key=lambda x: (-x.score, x.ident),
    )
    if not selected:
        return {}

    raw = {c2.ident: target_weight(c2.score, cfg) for c2 in selected}
    sectors = {c2.ident: c2.sector for c2 in selected}
    total_raw = sum(raw.values(), ZERO)
    weights = {k: v / total_raw * investable for k, v in raw.items()}

    for _ in range(64):  # bounded water-fill; converges well before the cap
        weights = {k: min(v, max_pos) for k, v in weights.items()}
        weights = _apply_sector_cap(weights, sectors, max_sector)
        leftover = investable - sum(weights.values(), ZERO)
        if leftover <= tiny:
            break
        headroom = _headroom(weights, sectors, max_pos, max_sector)
        total_headroom = sum(headroom.values(), ZERO)
        if total_headroom <= tiny:
            break  # nothing can absorb more -> remainder stays as cash
        add = min(leftover, total_headroom)
        for k, room in headroom.items():
            weights[k] += add * (room / total_headroom)

    # Drop sub-minimum positions to cash (never held below min_position_size).
    return {k: v for k, v in weights.items() if v >= min_pos}


def _headroom(
    weights: dict[str, Decimal],
    sectors: dict[str, str],
    max_pos: Decimal,
    max_sector: Decimal,
) -> dict[str, Decimal]:
    """Per-name remaining capacity, bounded by both the name and sector caps."""
    by_sector: dict[str, Decimal] = {}
    for k, w in weights.items():
        by_sector[sectors[k]] = by_sector.get(sectors[k], ZERO) + w
    room: dict[str, Decimal] = {}
    for k, w in weights.items():
        name_room = max_pos - w
        sector_room = max_sector - by_sector[sectors[k]]
        r = min(name_room, sector_room)
        if r > ZERO:
            room[k] = r
    return room


def _apply_sector_cap(
    weights: dict[str, Decimal],
    sectors: dict[str, str],
    max_sector: Decimal,
) -> dict[str, Decimal]:
    by_sector: dict[str, Decimal] = {}
    for k, w in weights.items():
        by_sector[sectors[k]] = by_sector.get(sectors[k], ZERO) + w
    out = dict(weights)
    for sector, total in by_sector.items():
        if total > max_sector and total > ZERO:
            scale = max_sector / total
            for k in weights:
                if sectors[k] == sector:
                    out[k] = out[k] * scale
    return out
