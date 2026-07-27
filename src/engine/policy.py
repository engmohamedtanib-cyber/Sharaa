"""Investment Policy Statement — the user's mandate, as enforceable code (M2).

The IPS is what the user hired the CIO *to do*: horizon, contribution rhythm,
concentration limits, and any personal exclusions beyond the Shariah gate. It is
an **input to** every decision, and every decision records the policy version in
force at the time (R5) so a past decision stays explicable under a policy that
has since changed.

Three properties are load-bearing, and each exists to close a specific hole:

1. **A policy may only tighten, never loosen.** Every cap is checked against
   ``config/thresholds.yaml``; a policy asking for a 40% single position when the
   engine caps at 30% is rejected at load. Policy is a narrowing of the engine's
   permission, never an expansion of it. Without this rule the IPS becomes a
   back door around the very limits it is supposed to sit inside.

2. **The Shariah gate cannot be disabled by policy (R7).** ``require_shariah_gate``
   must be true, and no exclusion mechanism has an "include" counterpart. There
   is deliberately no ``allowed_tickers`` field: a whitelist is an admission
   mechanism, and admission belongs to the gate alone.

3. **Exclusions are additive across versions.** A new version may add an
   exclusion; removing one requires an explicit, recorded ``rescinds`` entry with
   a reason. Silently dropping an exclusion is how a company the user once
   refused reappears in a plan without anyone deciding that it should.

Pure module (``CLAUDE.md`` §5, R2): no I/O, no clock reads, no randomness. YAML
is read at the boundary in ``config_loader`` and passed here as a mapping.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

from common.decimals import D
from engine.config import Thresholds
from engine.types import ShariahStatus


class PolicyError(RuntimeError):
    """Base class for policy problems."""


class PolicyRejectedError(PolicyError):
    """A policy that would loosen a constitutional limit. Refused at load."""


class PolicyConfigError(PolicyError):
    """A malformed or internally inconsistent policy document."""


class PolicySupersessionError(PolicyError):
    """An illegal transition between two policy versions."""


class Verdict(StrEnum):
    """Why policy admitted or refused a company."""

    ADMITTED = "ADMITTED"
    EXCLUDED_BY_TICKER = "EXCLUDED_BY_TICKER"
    EXCLUDED_BY_SECTOR = "EXCLUDED_BY_SECTOR"
    EXCLUDED_BY_STATUS = "EXCLUDED_BY_STATUS"      # policy refuses AMBER, engine allowed it
    REFUSED_BY_GATE = "REFUSED_BY_GATE"            # the gate said no; policy never sees it


@dataclass(frozen=True)
class PolicyDecision:
    """The policy layer's answer for one company."""

    ticker: str
    verdict: Verdict
    reason: str
    policy_version: int

    @property
    def admitted(self) -> bool:
        return self.verdict is Verdict.ADMITTED


@dataclass(frozen=True)
class Exclusion:
    """One personal exclusion, with the reason it was made.

    A reason is mandatory. An exclusion without one cannot be reviewed later,
    and an unreviewable exclusion silently becomes permanent.
    """

    key: str                 # ticker or sector name
    reason: str
    added_in_version: int

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise PolicyConfigError("an exclusion needs a ticker or sector")
        if not self.reason.strip():
            raise PolicyConfigError(f"exclusion {self.key!r} has no reason; every exclusion must state why")


@dataclass(frozen=True)
class InvestmentPolicy:
    """A versioned, immutable mandate."""

    version: int
    effective_from: str                 # ISO date, supplied at the boundary
    objective: str
    horizon_years: int
    base_currency: str = "EGP"

    monthly_contribution: Decimal | None = None
    max_single_position: Decimal | None = None
    max_single_sector: Decimal | None = None
    cash_reserve_min: Decimal | None = None
    max_holdings: int | None = None

    excluded_tickers: tuple[Exclusion, ...] = ()
    excluded_sectors: tuple[Exclusion, ...] = ()

    #: Constitutional, and checked at load. R7 is not negotiable by mandate.
    require_shariah_gate: bool = True
    #: A policy may refuse AMBER (tightening). It can never accept RED.
    accept_amber: bool = True

    note: str = ""
    rescinds: tuple[str, ...] = field(default=())   # exclusions deliberately lifted

    # -- lookups -------------------------------------------------------
    @property
    def excluded_ticker_keys(self) -> frozenset[str]:
        return frozenset(e.key for e in self.excluded_tickers)

    @property
    def excluded_sector_keys(self) -> frozenset[str]:
        return frozenset(e.key for e in self.excluded_sectors)

    def exclusion_for(self, ticker: str) -> Exclusion | None:
        key = ticker.strip().upper()
        for e in self.excluded_tickers:
            if e.key == key:
                return e
        return None

    # -- effective limits ---------------------------------------------
    def effective_max_position(self, cfg: Thresholds) -> Decimal:
        """The binding single-position cap: the tighter of policy and engine."""
        engine_cap = cfg.constraint("max_single_position")
        return min(engine_cap, self.max_single_position) if self.max_single_position is not None else engine_cap

    def effective_max_sector(self, cfg: Thresholds) -> Decimal:
        engine_cap = cfg.constraint("max_single_sector")
        return min(engine_cap, self.max_single_sector) if self.max_single_sector is not None else engine_cap

    def effective_cash_reserve_min(self, cfg: Thresholds) -> Decimal:
        """Cash floor: the *higher* of the two, because more cash is the safe side."""
        engine_floor = cfg.constraint("cash_reserve_min")
        return max(engine_floor, self.cash_reserve_min) if self.cash_reserve_min is not None else engine_floor

    def effective_max_holdings(self, cfg: Thresholds) -> int:
        engine_cap = int(cfg.constraint("max_holdings"))
        return min(engine_cap, self.max_holdings) if self.max_holdings is not None else engine_cap


#: The default mandate, used until the user states one. Deliberately the most
#: conservative reading of the engine's own limits: it adds nothing and removes
#: nothing, so behaviour with no policy equals behaviour with the engine alone.
DEFAULT_POLICY = InvestmentPolicy(
    version=1,
    effective_from="",
    objective="Preserve and grow capital in Shariah-compliant EGX equities.",
    horizon_years=5,
    note="Default mandate in force until the user states their own.",
)


# ======================================================================
# Loading and validation
# ======================================================================
def _dec(raw: Any, field_name: str) -> Decimal | None:
    if raw is None:
        return None
    try:
        return D(raw)
    except ValueError as exc:
        raise PolicyConfigError(f"{field_name}: {raw!r} is not a number") from exc


def _exclusions(raw: Any, version: int, what: str, *, upper: bool) -> tuple[Exclusion, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise PolicyConfigError(f"{what} must be a list")
    out: list[Exclusion] = []
    for item in raw:
        if not isinstance(item, dict):
            raise PolicyConfigError(f"{what}: each entry must be a mapping with `key` and `reason`")
        key = str(item.get("key", "")).strip()
        out.append(
            Exclusion(
                key=key.upper() if upper else key,
                reason=str(item.get("reason", "")),
                added_in_version=int(item.get("added_in_version", version)),
            )
        )
    return tuple(out)


def parse_policy(raw: dict[str, Any], cfg: Thresholds) -> InvestmentPolicy:
    """Build and validate a policy from a parsed mapping.

    Validation runs against ``cfg`` because "may only tighten" is meaningless
    without the engine's own limits to compare to.
    """
    try:
        version = int(raw["version"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PolicyConfigError("policy must carry an integer `version`") from exc
    if version < 1:
        raise PolicyConfigError(f"policy version must be >= 1 (got {version})")

    horizon_raw = raw.get("horizon_years", 5)
    try:
        horizon = int(horizon_raw)
    except (TypeError, ValueError) as exc:
        raise PolicyConfigError(f"horizon_years: {horizon_raw!r} is not an integer") from exc
    if horizon < 1:
        raise PolicyConfigError(f"horizon_years must be >= 1 (got {horizon})")

    limits = raw.get("limits") or {}
    if not isinstance(limits, dict):
        raise PolicyConfigError("`limits` must be a mapping")

    max_holdings_raw = limits.get("max_holdings")
    policy = InvestmentPolicy(
        version=version,
        effective_from=str(raw.get("effective_from", "") or ""),
        objective=str(raw.get("objective", "") or ""),
        horizon_years=horizon,
        base_currency=str(raw.get("base_currency", "EGP")),
        monthly_contribution=_dec(raw.get("monthly_contribution"), "monthly_contribution"),
        max_single_position=_dec(limits.get("max_single_position"), "limits.max_single_position"),
        max_single_sector=_dec(limits.get("max_single_sector"), "limits.max_single_sector"),
        cash_reserve_min=_dec(limits.get("cash_reserve_min"), "limits.cash_reserve_min"),
        max_holdings=int(max_holdings_raw) if max_holdings_raw is not None else None,
        excluded_tickers=_exclusions(
            (raw.get("exclusions") or {}).get("tickers"), version, "exclusions.tickers", upper=True
        ),
        excluded_sectors=_exclusions(
            (raw.get("exclusions") or {}).get("sectors"), version, "exclusions.sectors", upper=False
        ),
        require_shariah_gate=bool(raw.get("require_shariah_gate", True)),
        accept_amber=bool(raw.get("accept_amber", True)),
        note=str(raw.get("note", "") or ""),
        rescinds=tuple(str(r) for r in (raw.get("rescinds") or [])),
    )
    validate_policy(policy, cfg)
    return policy


def validate_policy(policy: InvestmentPolicy, cfg: Thresholds) -> None:
    """Refuse any policy that would widen the engine's permission.

    Raises :class:`PolicyRejectedError` on a loosening, :class:`PolicyConfigError`
    on a shape problem. Returns ``None`` when the policy is admissible.
    """
    if not policy.require_shariah_gate:
        raise PolicyRejectedError(
            "require_shariah_gate is false. The Shariah gate is constitutional (CLAUDE.md R7): "
            "no mandate, however explicit, may switch it off. Change CLAUDE.md or accept the gate."
        )

    if policy.base_currency != "EGP":
        raise PolicyConfigError(
            f"base_currency {policy.base_currency!r}: this system prices, screens and reports in EGP only"
        )

    checks: tuple[tuple[str, Decimal | None, Decimal, str], ...] = (
        ("max_single_position", policy.max_single_position, cfg.constraint("max_single_position"), "above"),
        ("max_single_sector", policy.max_single_sector, cfg.constraint("max_single_sector"), "above"),
    )
    for name, value, engine_limit, _ in checks:
        if value is None:
            continue
        if value <= 0:
            raise PolicyConfigError(f"{name} must be positive (got {value})")
        if value > engine_limit:
            raise PolicyRejectedError(
                f"{name}={value} exceeds the engine limit of {engine_limit}. A policy may only "
                "tighten a limit. Raising it requires a threshold change in config/thresholds.yaml "
                "with a citation and a changelog entry (R4), not a personal mandate."
            )

    if policy.cash_reserve_min is not None:
        floor = cfg.constraint("cash_reserve_min")
        if policy.cash_reserve_min < 0 or policy.cash_reserve_min >= 1:
            raise PolicyConfigError(f"cash_reserve_min must be in [0,1) (got {policy.cash_reserve_min})")
        if policy.cash_reserve_min < floor:
            raise PolicyRejectedError(
                f"cash_reserve_min={policy.cash_reserve_min} is below the engine floor of {floor}. "
                "Holding less cash than the engine requires is a loosening, not a preference."
            )

    if policy.max_holdings is not None:
        engine_cap = int(cfg.constraint("max_holdings"))
        if policy.max_holdings < 1:
            raise PolicyConfigError(f"max_holdings must be >= 1 (got {policy.max_holdings})")
        if policy.max_holdings > engine_cap:
            raise PolicyRejectedError(
                f"max_holdings={policy.max_holdings} exceeds the engine cap of {engine_cap}."
            )

    if policy.monthly_contribution is not None and policy.monthly_contribution < 0:
        raise PolicyConfigError("monthly_contribution cannot be negative")

    dupes = policy.excluded_ticker_keys & {e.key for e in policy.excluded_sectors}
    if dupes:
        raise PolicyConfigError(f"the same key is excluded as both ticker and sector: {sorted(dupes)}")


def validate_supersession(old: InvestmentPolicy, new: InvestmentPolicy) -> None:
    """Check a policy replacement. History is never edited (R5).

    Exclusions are additive: a new version may add, and may only drop one it
    names in ``rescinds``. A silent drop is refused, because a company the user
    once refused to own would otherwise walk back into a plan with no decision
    behind it.
    """
    if new.version <= old.version:
        raise PolicySupersessionError(
            f"new policy version {new.version} does not supersede {old.version}; versions increase, "
            "and an existing version is never edited in place"
        )

    dropped = (old.excluded_ticker_keys - new.excluded_ticker_keys) | (
        old.excluded_sector_keys - new.excluded_sector_keys
    )
    unexplained = sorted(dropped - set(new.rescinds))
    if unexplained:
        raise PolicySupersessionError(
            f"exclusions {unexplained} disappeared without being rescinded. To lift an exclusion, "
            "list it in `rescinds` so the reversal is itself a recorded decision."
        )


# ======================================================================
# Application — always after the gate, never instead of it
# ======================================================================
def apply_policy(
    ticker: str,
    sector: str,
    gate_passed: bool,
    status: ShariahStatus,
    policy: InvestmentPolicy,
) -> PolicyDecision:
    """Decide whether policy admits a company the gate has already judged.

    ``gate_passed`` is the engine's verdict and is checked *first*: policy is
    only ever consulted about companies the gate already allows (R7). A policy
    can subtract from that set. Nothing can add to it.
    """
    key = ticker.strip().upper()

    if not gate_passed:
        return PolicyDecision(
            ticker=key,
            verdict=Verdict.REFUSED_BY_GATE,
            reason=f"Shariah gate refused {key} ({status.value}); policy is not consulted.",
            policy_version=policy.version,
        )

    excl = policy.exclusion_for(key)
    if excl is not None:
        return PolicyDecision(
            ticker=key,
            verdict=Verdict.EXCLUDED_BY_TICKER,
            reason=f"personal exclusion: {excl.reason}",
            policy_version=policy.version,
        )

    sector_key = sector.strip()
    for e in policy.excluded_sectors:
        if e.key.casefold() == sector_key.casefold() and sector_key:
            return PolicyDecision(
                ticker=key,
                verdict=Verdict.EXCLUDED_BY_SECTOR,
                reason=f"sector {sector_key!r} excluded by policy: {e.reason}",
                policy_version=policy.version,
            )

    if status is ShariahStatus.AMBER and not policy.accept_amber:
        return PolicyDecision(
            ticker=key,
            verdict=Verdict.EXCLUDED_BY_STATUS,
            reason="policy does not accept AMBER (within limits but close to one); tightening only.",
            policy_version=policy.version,
        )

    return PolicyDecision(
        ticker=key,
        verdict=Verdict.ADMITTED,
        reason=f"admitted under policy v{policy.version}",
        policy_version=policy.version,
    )


def filter_candidates(
    candidates: dict[str, tuple[str, bool, ShariahStatus]],
    policy: InvestmentPolicy,
) -> tuple[tuple[str, ...], tuple[PolicyDecision, ...]]:
    """Apply policy to many companies at once.

    ``candidates`` maps ticker -> (sector, gate_passed, status). Returns the
    admitted tickers in sorted order and every decision made, including the
    refusals — the refusals are the part the user needs to see.
    """
    decisions = tuple(
        apply_policy(t, sector, passed, status, policy)
        for t, (sector, passed, status) in sorted(candidates.items())
    )
    admitted = tuple(d.ticker for d in decisions if d.admitted)
    return admitted, decisions
