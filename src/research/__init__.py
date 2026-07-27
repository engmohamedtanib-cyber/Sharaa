"""Live data adapters (``ROADMAP_V1`` M5).

The only layer permitted to touch the outside world. Everything it returns is
what a source actually said, with the URL and retrieval time attached; nothing
here fills a gap, and a source that cannot answer raises rather than returning
an empty result.
"""

from __future__ import annotations

from research.discovery import DiscoveryOutcome, SeenFilingsLog, discover_filings
from research.market import (
    InsufficientHistoryError,
    MarketSnapshot,
    build_snapshot,
    trailing_average_market_cap,
)
from research.offline import (
    BLOCKED_NOTE,
    BlockedFilingDiscovery,
    BlockedMacroSource,
    BlockedMarketData,
    UploadedFilingDiscovery,
)
from research.protocols import (
    FilingDiscovery,
    FilingRef,
    MacroSnapshot,
    MacroSource,
    MarketDataSource,
    PriceBar,
    ResearchError,
    SourceChangedError,
    SourceUnavailableError,
)
from research.registry import SourceRegistry, SourceSpec, backoff_delays, parse_registry, with_retry

__all__ = [
    "BLOCKED_NOTE",
    "BlockedFilingDiscovery",
    "BlockedMacroSource",
    "BlockedMarketData",
    "DiscoveryOutcome",
    "FilingDiscovery",
    "FilingRef",
    "InsufficientHistoryError",
    "MacroSnapshot",
    "MacroSource",
    "MarketDataSource",
    "MarketSnapshot",
    "PriceBar",
    "ResearchError",
    "SeenFilingsLog",
    "SourceChangedError",
    "SourceRegistry",
    "SourceSpec",
    "SourceUnavailableError",
    "UploadedFilingDiscovery",
    "backoff_delays",
    "build_snapshot",
    "discover_filings",
    "parse_registry",
    "trailing_average_market_cap",
    "with_retry",
]
