"""The investable universe — EGX 33 Shariah Index constituents (``decisions/0004``).

Membership *selects* the universe. It never *substitutes* for Screens A-E: the
index publishes pass/fail on a periodic, lagging schedule and never reports
headroom, so every constituent is still screened independently before it can be
scored (``CLAUDE.md`` R7).

The load-bearing behaviour of this module is a refusal. An unpopulated universe
raises :class:`UniverseUnavailableError`; it must never be read as "no compliant
companies were found", because those two states produce the same empty list and
opposite correct actions — the first means *stop*, the second means *proceed
with nothing to buy*. Collapsing them is how a screening system silently reports
an all-clear it never computed.

Pure module (``CLAUDE.md`` §5, R2): definitions and total functions only, no I/O.
The YAML is read by :mod:`config_loader`, which hands the parsed mapping here.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from common.decimals import D

#: The only ``status`` value under which a universe may be used.
POPULATED = "POPULATED"

#: Set while the constituent list is deliberately empty, awaiting a primary source.
AWAITING_CONSTITUENTS = "AWAITING_CONSTITUENTS"

#: Fields every constituent row must carry. ``sector`` is intentionally absent:
#: the EGX constituents export does not publish one, and inventing it would be a
#: recalled fact in a file whose entire purpose is to contain only sourced ones.
_REQUIRED_FIELDS = ("ticker", "isin", "name_en", "name_ar", "reuters_code", "issuer_id")


class UniverseError(RuntimeError):
    """Base class for universe rejections."""


class UniverseUnavailableError(UniverseError):
    """The universe is not populated, so no screening run may proceed.

    Distinct from "the universe is populated and nothing passed screening".
    Callers must not catch this and continue with an empty list.
    """


class UniverseIntegrityError(UniverseError):
    """The universe file is internally inconsistent and cannot be trusted."""


@dataclass(frozen=True)
class Constituent:
    """One *listing* in the index.

    A listing is not an issuer. EGX lists some companies in more than one
    currency, so two listings can share an :attr:`issuer_id` — see
    :meth:`Universe.issuers` and ``decisions/0005``.
    """

    ticker: str
    isin: str
    name_en: str
    name_ar: str
    reuters_code: str
    issuer_id: str
    #: Index weight at the source's as-of date. Informational only — it is
    #: never an engine input. ``engine.portfolio`` derives its own target
    #: weights from scores and constraints (``ENGINE_SPEC`` §7).
    index_weight: Decimal | None = None
    #: ``None`` until read off a primary source. Never inferred (R3).
    sector: str | None = None


@dataclass(frozen=True)
class Provenance:
    """Where the constituent list came from. Mandatory when populated (R1)."""

    at: str
    source: str
    by: str
    sha256: str | None = None
    #: Id of the record in ``memory/evidence/``. The durable pointer — the file
    #: path can move, the registry entry cannot (``memory/evidence/README.md``).
    evidence_id: str | None = None


@dataclass(frozen=True)
class Universe:
    """A populated investable universe. Constructing one is proof of provenance."""

    version: str
    index_code: str
    retrieved: Provenance
    constituents: tuple[Constituent, ...]
    off_index_watch: tuple[str, ...] = ()

    @property
    def tickers(self) -> tuple[str, ...]:
        """Every listed ticker, in file order."""
        return tuple(c.ticker for c in self.constituents)

    @property
    def issuers(self) -> tuple[str, ...]:
        """Distinct issuers, in first-appearance order.

        This — not ``len(constituents)`` — is the count the index name refers
        to. Position limits and concentration caps apply per issuer, because
        two currency listings of one bank carry one company's risk.
        """
        seen: list[str] = []
        for c in self.constituents:
            if c.issuer_id not in seen:
                seen.append(c.issuer_id)
        return tuple(seen)

    def listings_of(self, issuer_id: str) -> tuple[Constituent, ...]:
        """Every listing belonging to ``issuer_id`` (usually one)."""
        return tuple(c for c in self.constituents if c.issuer_id == issuer_id)

    def is_constituent(self, ticker: str) -> bool:
        return any(c.ticker == ticker for c in self.constituents)

    def get(self, ticker: str) -> Constituent:
        """Look up a listing, raising :class:`KeyError` if it is not in the index."""
        for c in self.constituents:
            if c.ticker == ticker:
                return c
        raise KeyError(f"{ticker!r} is not a constituent of {self.index_code}")


def _require_provenance(raw: dict[str, Any]) -> Provenance:
    block = raw.get("retrieved") or {}
    if not isinstance(block, dict):
        raise UniverseIntegrityError("`retrieved` must be a mapping")
    missing = [k for k in ("at", "from", "by") if not block.get(k)]
    if missing:
        raise UniverseIntegrityError(f"populated universe is missing provenance: {', '.join(missing)} (CLAUDE.md R1)")
    sha = block.get("sha256")
    evidence_id = block.get("evidence_id")
    return Provenance(
        at=str(block["at"]),
        source=str(block["from"]),
        by=str(block["by"]),
        sha256=None if sha is None else str(sha),
        evidence_id=None if evidence_id is None else str(evidence_id),
    )


def _parse_weight(row: dict[str, Any], ticker: str) -> Decimal | None:
    raw_weight = row.get("index_weight")
    if raw_weight is None:
        return None
    weight = D(raw_weight)
    # Weights are a snapshot taken at the index's as-of date and drift with
    # price between rebalances, so the 15% cap is deliberately NOT enforced
    # here — a legitimate post-rebalance drift above the cap must not make the
    # whole universe unloadable. Only impossible values are rejected.
    if weight <= 0 or weight > 1:
        raise UniverseIntegrityError(f"{ticker}: index_weight {weight} is outside (0, 1]")
    return weight


def _parse_constituent(row: Any, position: int) -> Constituent:
    if not isinstance(row, dict):
        raise UniverseIntegrityError(f"constituent #{position} is not a mapping")
    missing = [f for f in _REQUIRED_FIELDS if not row.get(f)]
    if missing:
        raise UniverseIntegrityError(f"constituent #{position} is missing {', '.join(missing)}")
    ticker = str(row["ticker"])
    sector = row.get("sector")
    return Constituent(
        ticker=ticker,
        isin=str(row["isin"]),
        name_en=str(row["name_en"]),
        name_ar=str(row["name_ar"]),
        reuters_code=str(row["reuters_code"]),
        issuer_id=str(row["issuer_id"]),
        index_weight=_parse_weight(row, ticker),
        sector=None if sector is None else str(sector),
    )


def _reject_duplicates(constituents: tuple[Constituent, ...]) -> None:
    for field_name in ("ticker", "isin"):
        seen: set[str] = set()
        for c in constituents:
            value = getattr(c, field_name)
            if value in seen:
                raise UniverseIntegrityError(f"duplicate {field_name} {value!r} in constituents")
            seen.add(value)


def parse_universe(raw: dict[str, Any]) -> Universe:
    """Build a :class:`Universe` from the parsed ``config/universe.yaml`` mapping.

    Raises :class:`UniverseUnavailableError` when the file is not populated, and
    :class:`UniverseIntegrityError` when it is populated but does not hold
    together. Both are refusals: neither returns a usable empty universe.
    """
    status = str(raw.get("status") or "")
    rows = raw.get("constituents") or []

    if status != POPULATED:
        raise UniverseUnavailableError(
            f"universe status is {status or 'unset'!r}, not {POPULATED!r} — "
            "the constituent list has not been transcribed from a primary source. "
            "This is not the same as 'no company passed screening'; no run may proceed."
        )
    if not rows:
        raise UniverseUnavailableError(
            f"universe status is {POPULATED!r} but the constituent list is empty — "
            "refusing to treat an unpopulated universe as an empty result set."
        )

    constituents = tuple(_parse_constituent(row, i) for i, row in enumerate(rows, start=1))
    _reject_duplicates(constituents)

    universe = Universe(
        version=str(raw.get("version") or ""),
        index_code=str((raw.get("index") or {}).get("code") or ""),
        retrieved=_require_provenance(raw),
        constituents=constituents,
        off_index_watch=tuple(str(t) for t in (raw.get("off_index_watch") or [])),
    )

    # The index name is a promise about issuers, not listings. Checking it here
    # means a rebalance that adds or drops a company cannot slip through as a
    # silent transcription error — it fails loudly at load and gets a human look.
    declared = (raw.get("index") or {}).get("constituent_count")
    if declared is not None and len(universe.issuers) != int(declared):
        raise UniverseIntegrityError(
            f"{universe.index_code} declares {declared} constituents but the file holds "
            f"{len(universe.issuers)} distinct issuers across {len(constituents)} listings"
        )
    return universe
