"""External data sources, as protocols the rest of the system can rely on.

``research/`` is the only layer allowed to touch the outside world. Everything
downstream depends on these interfaces, never on an implementation, which is
what lets the whole engine be exercised offline and lets a source be replaced
when EGX changes its pages — the single most likely long-term maintenance
burden in this project (``ROADMAP_V1`` M5).

Two rules shape every adapter:

* **Failure is loud.** A source that cannot answer raises
  :class:`SourceUnavailableError`. It never returns an empty list, a zero, or a
  stale value. A silent skip in a discovery run means a filing was missed and
  nothing anywhere records that it was missed.
* **Nothing is inferred.** An adapter returns what the source said, with the
  URL and retrieval time attached. Filling a gap is not an adapter's job, and
  under R3 it is nobody's job.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol


class ResearchError(RuntimeError):
    """Base class for research-layer failures."""


class SourceUnavailableError(ResearchError):
    """A source could not be reached or refused us.

    Carries the URL so a failure names the thing that failed, and the
    environment note so a 403 from a policy proxy is not mistaken for a dead
    site (see ``decisions/0004``).
    """

    def __init__(self, url: str, reason: str) -> None:
        super().__init__(f"{url}: {reason}")
        self.url = url
        self.reason = reason


class SourceChangedError(ResearchError):
    """The page was reached but no longer has the shape the adapter expects.

    Distinct from unavailable on purpose: unreachable is usually transient,
    a changed page needs a human to look at it.
    """


@dataclass(frozen=True)
class FilingRef:
    """A filing we know exists, before it is downloaded.

    ``content_hash`` is filled after acquisition; discovery only knows the URL.
    Idempotency across runs is by ``(ticker, url)`` first and by hash after
    download, because the same document is often published at two URLs.
    """

    ticker: str
    url: str
    title: str
    published_at: str            # ISO date as the source stated it
    source_type: str             # EGX_DISCLOSURE | COMPANY_IR | FRA
    fiscal_year: int | None = None
    period_type: str | None = None
    content_hash: str | None = None
    discovered_at: str = ""      # ISO timestamp supplied by the caller

    @property
    def identity(self) -> str:
        """Dedupe key before a download has happened."""
        return f"{self.ticker}:{self.url}"


@dataclass(frozen=True)
class PriceBar:
    """One trading day, as the market data source reported it."""

    on: date
    close: Decimal
    volume: Decimal | None = None
    market_cap: Decimal | None = None


@dataclass(frozen=True)
class MacroSnapshot:
    """Egyptian macro inputs the scoring layer needs (Pillars 4 and 5)."""

    as_of: str
    cpi_yoy: Decimal | None = None
    tbill_1y_yield: Decimal | None = None
    policy_rate: Decimal | None = None
    usd_egp: Decimal | None = None
    source: str = ""

    @property
    def complete(self) -> bool:
        """True when every scoring input is present.

        An incomplete snapshot is usable — the missing sub-criteria score
        MISSING_DATA, which understates the total. That is correct, and the
        caller is told so rather than handed a filled-in blank.
        """
        return None not in (self.cpi_yoy, self.tbill_1y_yield, self.policy_rate, self.usd_egp)


class FilingDiscovery(Protocol):
    """Finds filings that exist for a company."""

    def discover(self, ticker: str, since: str) -> list[FilingRef]:
        """Filings published on or after ``since`` (ISO date).

        Raises :class:`SourceUnavailableError` rather than returning ``[]`` when
        the source could not be read. An empty list means "the source answered
        and there is nothing new".
        """
        ...


class MarketDataSource(Protocol):
    """Daily prices and market capitalisation."""

    def history(self, ticker: str, start: str, end: str) -> list[PriceBar]:
        ...


class MacroSource(Protocol):
    """CBE / CAPMAS macro series."""

    def snapshot(self, as_of: str) -> MacroSnapshot:
        ...
