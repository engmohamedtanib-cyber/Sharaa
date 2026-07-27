"""The investable universe: parsing, validation, and refusal (``decisions/0004``).

``config/universe.yaml`` names the companies the engine is allowed to consider.
It is currently, and deliberately, **empty**: the EGX 33 Shariah constituent list
must be transcribed from a primary source and has never been opened in this
environment (``memory/NEXT_TASK.md``).

The single load-bearing behaviour in this module:

    **An unpopulated universe is UNAVAILABLE, never "no compliant companies".**

Those two states are indistinguishable downstream unless something refuses. A
screening run over zero companies produces zero passes, which reads exactly like
"nothing on the EGX is halal" — a confidently wrong statement about something
that was never checked. That is the Prime Directive's failure mode with the sign
flipped, so it is raised here as an error rather than returned as a result.

Membership in the index selects the universe; it never substitutes for our own
Screens A-E. A constituent still has to pass the gate on its own filings, and a
company can breach between index rebalances (``config/universe.yaml`` header).

Pure module (``CLAUDE.md`` §5, R2): no I/O, no clock reads, no randomness. The
YAML is read at the boundary in ``config_loader`` and handed here as a mapping.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class UniverseError(RuntimeError):
    """Base class for universe problems."""


class UniverseUnavailableError(UniverseError):
    """The universe has not been populated from a primary source.

    Raised instead of returning an empty list, so that a caller cannot mistake
    "we never looked" for "we looked and found nothing".
    """


class UniverseConfigError(UniverseError):
    """``universe.yaml`` is internally inconsistent or claims more than it shows."""


class UniverseStatus(StrEnum):
    """Lifecycle of the constituent list."""

    AWAITING_CONSTITUENTS = "AWAITING_CONSTITUENTS"  # never transcribed — unusable
    POPULATED = "POPULATED"                          # transcribed, with provenance
    STALE = "STALE"                                  # transcribed, but past its review date


#: Statuses a screening run may proceed on.
_USABLE = frozenset({UniverseStatus.POPULATED, UniverseStatus.STALE})


@dataclass(frozen=True)
class Constituent:
    """One company in the universe, as transcribed from a source.

    ``ticker`` is the EGX code (e.g. ``COMI``). Names are kept bilingual where
    the source provides both, and Arabic is stored verbatim (``CLAUDE.md`` §7).
    """

    ticker: str
    name_en: str
    name_ar: str = ""
    sector: str = ""
    isin: str = ""

    def __post_init__(self) -> None:
        if not self.ticker.strip():
            raise UniverseConfigError("a constituent must have a ticker")
        if not self.name_en.strip() and not self.name_ar.strip():
            raise UniverseConfigError(f"{self.ticker}: a constituent must have a name in at least one language")


@dataclass(frozen=True)
class Provenance:
    """Where the list came from and when. Required before the list may be used (R1)."""

    at: str | None = None
    source: str | None = None
    by: str | None = None

    @property
    def complete(self) -> bool:
        return bool((self.at or "").strip()) and bool((self.source or "").strip())


@dataclass(frozen=True)
class Universe:
    """The parsed universe file."""

    version: str
    status: UniverseStatus
    index_name: str
    expected_count: int | None
    constituents: tuple[Constituent, ...]
    off_index_watch: tuple[Constituent, ...]
    retrieved: Provenance

    # -- availability --------------------------------------------------
    @property
    def available(self) -> bool:
        """True when the engine may screen against this universe."""
        return self.status in _USABLE and bool(self.constituents) and self.retrieved.complete

    def require_available(self) -> None:
        """Raise unless the universe may be used. The refusal point.

        Callers must call this *before* iterating, not after, so that an empty
        iteration can never be reported as a screening result.
        """
        if self.status not in _USABLE:
            raise UniverseUnavailableError(
                f"universe status is {self.status.value}: the constituent list has never been "
                "transcribed from a primary source. Screening cannot run. This is not the same "
                "as 'no company is compliant' and must never be reported as such "
                "(see memory/NEXT_TASK.md for how to populate it)."
            )
        if not self.constituents:
            raise UniverseUnavailableError(
                f"universe status is {self.status.value} but the constituent list is empty; "
                "the file contradicts itself and cannot be used."
            )
        if not self.retrieved.complete:
            raise UniverseUnavailableError(
                "the universe has constituents but no retrieval provenance (retrieved.at / "
                "retrieved.from). A halal universe with no source is exactly what R1 forbids."
            )

    # -- lookups (all require availability first) ----------------------
    @property
    def tickers(self) -> tuple[str, ...]:
        return tuple(c.ticker for c in self.constituents)

    def contains(self, ticker: str) -> bool:
        return any(c.ticker == ticker.strip().upper() for c in self.constituents)

    def get(self, ticker: str) -> Constituent | None:
        key = ticker.strip().upper()
        for c in self.constituents:
            if c.ticker == key:
                return c
        for c in self.off_index_watch:
            if c.ticker == key:
                return c
        return None

    def is_off_index(self, ticker: str) -> bool:
        """True for a watched company that the index does not include.

        Such a company carries no index pre-screening at all: it must clear the
        full A-E gate on its own filings before it can be considered.
        """
        key = ticker.strip().upper()
        return any(c.ticker == key for c in self.off_index_watch)

    def screenable(self) -> tuple[Constituent, ...]:
        """Everything the engine may screen this cycle. Refuses when unavailable."""
        self.require_available()
        return self.constituents + self.off_index_watch


# ======================================================================
# Parsing
# ======================================================================
def _constituent(raw: Any, where: str) -> Constituent:
    if not isinstance(raw, dict):
        raise UniverseConfigError(f"{where}: each entry must be a mapping, got {type(raw).__name__}")
    ticker = str(raw.get("ticker", "")).strip().upper()
    return Constituent(
        ticker=ticker,
        name_en=str(raw.get("name_en", "") or "").strip(),
        name_ar=str(raw.get("name_ar", "") or "").strip(),
        sector=str(raw.get("sector", "") or "").strip(),
        isin=str(raw.get("isin", "") or "").strip(),
    )


def parse_universe(raw: dict[str, Any]) -> Universe:
    """Build a :class:`Universe` from the parsed YAML mapping.

    Validation here is deliberately suspicious of *claims*: a file that says
    ``POPULATED`` while showing no rows, or that shows a different number of rows
    than the index it names, is a transcription that went wrong halfway. Both are
    refused at parse time rather than discovered as a short screening run.
    """
    try:
        status = UniverseStatus(str(raw.get("status", UniverseStatus.AWAITING_CONSTITUENTS.value)))
    except ValueError as exc:
        raise UniverseConfigError(f"unknown universe status {raw.get('status')!r}") from exc

    index = raw.get("index") or {}
    if not isinstance(index, dict):
        raise UniverseConfigError("`index` must be a mapping")

    expected_raw = index.get("constituent_count")
    expected = int(expected_raw) if expected_raw is not None else None

    constituents = tuple(_constituent(r, "constituents") for r in (raw.get("constituents") or []))
    off_index = tuple(_constituent(r, "off_index_watch") for r in (raw.get("off_index_watch") or []))

    seen: set[str] = set()
    for c in constituents + off_index:
        if c.ticker in seen:
            raise UniverseConfigError(f"{c.ticker} appears twice in the universe")
        seen.add(c.ticker)

    retrieved_raw = raw.get("retrieved") or {}
    if not isinstance(retrieved_raw, dict):
        raise UniverseConfigError("`retrieved` must be a mapping")
    retrieved = Provenance(
        at=retrieved_raw.get("at"),
        source=retrieved_raw.get("from"),
        by=retrieved_raw.get("by"),
    )

    universe = Universe(
        version=str(raw.get("version", "0.0.0")),
        status=status,
        index_name=str(index.get("name", "") or ""),
        expected_count=expected,
        constituents=constituents,
        off_index_watch=off_index,
        retrieved=retrieved,
    )

    if status in _USABLE:
        if not constituents:
            raise UniverseConfigError(
                f"status is {status.value} but no constituents are listed; populate the list or "
                "set status back to AWAITING_CONSTITUENTS"
            )
        if not retrieved.complete:
            raise UniverseConfigError(
                f"status is {status.value} but retrieval provenance is incomplete "
                "(retrieved.at and retrieved.from are both required by R1)"
            )
        if expected is not None and len(constituents) != expected:
            raise UniverseConfigError(
                f"index {universe.index_name!r} declares {expected} constituents but "
                f"{len(constituents)} were transcribed. A partial transcription must not be "
                "used: the missing rows are indistinguishable from companies that were checked "
                "and rejected."
            )
    elif constituents:
        raise UniverseConfigError(
            "constituents are listed but status is AWAITING_CONSTITUENTS; set the status and the "
            "retrieval provenance together, in one edit, so the list is never usable without a source"
        )

    return universe
