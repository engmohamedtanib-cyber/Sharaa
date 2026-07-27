"""Per-company source registry and retry policy.

Two small things that keep the live layer maintainable when EGX inevitably
changes a page:

* :class:`SourceRegistry` — where each company's filings actually live, read
  from ``config/sources.yaml``. Sources are configuration, not code, so
  redirecting a company to a new IR page is a config edit with a diff, not a
  patch (the same reasoning as R4 for thresholds).

* :func:`backoff_delays` and :func:`with_retry` — a network failure is retried
  a bounded number of times and then **surfaces**. The retry loop takes its
  sleep function as an argument, so tests exercise the real control flow
  without waiting and nothing in this module reads a clock.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from research.protocols import ResearchError, SourceUnavailableError

T = TypeVar("T")


@dataclass(frozen=True)
class SourceSpec:
    """One place a company's filings can be found."""

    source_type: str
    url_pattern: str
    discovery_method: str
    priority: int = 1
    selector: str | None = None

    def url_for(self, ticker: str) -> str:
        """Fill the ticker into the pattern. A pattern without a slot is constant."""
        return self.url_pattern.replace("{ticker}", ticker.strip().upper())


@dataclass(frozen=True)
class SourceRegistry:
    """Everything ``config/sources.yaml`` says about where to look."""

    exchange: tuple[SourceSpec, ...]
    companies: dict[str, tuple[SourceSpec, ...]]

    def for_ticker(self, ticker: str) -> tuple[SourceSpec, ...]:
        """Sources to try for one company, most preferred first.

        The exchange-wide disclosure feed is always included: an issuer's own IR
        page can lag or omit, and the exchange filing is the record.
        """
        key = ticker.strip().upper()
        specs = list(self.companies.get(key, ())) + list(self.exchange)
        return tuple(sorted(specs, key=lambda s: (s.priority, s.source_type)))

    @property
    def configured_companies(self) -> tuple[str, ...]:
        return tuple(sorted(self.companies))


def _spec(raw: dict[str, Any]) -> SourceSpec:
    try:
        return SourceSpec(
            source_type=str(raw["source_type"]),
            url_pattern=str(raw["url_pattern"]),
            discovery_method=str(raw["discovery_method"]),
            priority=int(raw.get("priority", 1)),
            selector=raw.get("selector"),
        )
    except KeyError as exc:
        raise ResearchError(f"source spec missing {exc}") from exc


def parse_registry(raw: dict[str, Any]) -> SourceRegistry:
    """Build the registry from the parsed ``sources.yaml`` mapping."""
    exchange_raw = raw.get("exchange")
    exchange_raw = {} if exchange_raw is None else exchange_raw
    if not isinstance(exchange_raw, dict):
        raise ResearchError("`exchange` must be a mapping")
    exchange = tuple(_spec(v) for v in exchange_raw.values())

    companies_raw = raw.get("companies")
    companies_raw = {} if companies_raw is None else companies_raw
    if not isinstance(companies_raw, dict):
        raise ResearchError("`companies` must be a mapping")
    companies = {
        str(ticker).upper(): tuple(_spec(s) for s in (specs or []))
        for ticker, specs in companies_raw.items()
    }
    return SourceRegistry(exchange=exchange, companies=companies)


# ======================================================================
# Retry
# ======================================================================
def backoff_delays(attempts: int, base_seconds: float = 2.0) -> tuple[float, ...]:
    """Exponential delays between attempts: 2s, 4s, 8s, ...

    Deterministic — no jitter. One personal tool polling a handful of pages does
    not need to de-synchronise from a fleet, and a reproducible delay sequence
    is one less thing that differs between a test and a live run.
    """
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    return tuple(base_seconds * (2 ** i) for i in range(attempts - 1))


def with_retry(
    operation: Callable[[], T],
    *,
    attempts: int = 4,
    sleep: Callable[[float], None],
    on_failure: Callable[[int, Exception], None] | None = None,
    base_seconds: float = 2.0,
) -> T:
    """Run ``operation``, retrying transient source failures, then re-raise.

    Only :class:`SourceUnavailableError` is retried: a changed page or a parse
    error will fail identically every time, and retrying it just delays the
    moment a human finds out.
    """
    delays = backoff_delays(attempts, base_seconds)
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except SourceUnavailableError as exc:
            last = exc
            if on_failure is not None:
                on_failure(attempt, exc)
            if attempt <= len(delays):
                sleep(delays[attempt - 1])
    assert last is not None
    raise last
