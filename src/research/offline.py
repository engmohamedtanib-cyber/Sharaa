"""The adapters that exist today: ones that tell the truth about being blocked.

This environment cannot reach EGX. ``curl`` and ``WebFetch`` both return 403
from the egress proxy for every host, verified rather than assumed
(``memory/CURRENT_STATE.md``). So the honest live adapter, right now, is one
that raises with that explanation attached.

This is not a stub to be replaced by "the real one later" — it is what the
system should do in any environment without network access, and it stays useful
after live adapters exist: pass it explicitly to prove a code path never needed
the network.

Filings reach the system by upload instead (``decisions/0004``), which is why
:class:`UploadedFilingDiscovery` is here: a discovery adapter over a local
directory the user has dropped PDFs into.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from research.protocols import (
    FilingRef,
    MacroSnapshot,
    PriceBar,
    SourceUnavailableError,
)

BLOCKED_NOTE = (
    "outbound HTTP is blocked in this environment (403 from the egress proxy for every host, "
    "verified 2026-07-25). Filings arrive by upload; market and macro data must be supplied. "
    "See decisions/0004."
)


@dataclass(frozen=True)
class BlockedFilingDiscovery:
    """Filing discovery in an environment with no outbound network."""

    note: str = BLOCKED_NOTE

    def discover(self, ticker: str, since: str) -> list[FilingRef]:
        raise SourceUnavailableError(f"filing discovery for {ticker}", self.note)


@dataclass(frozen=True)
class BlockedMarketData:
    """Market data in an environment with no outbound network."""

    note: str = BLOCKED_NOTE

    def history(self, ticker: str, start: str, end: str) -> list[PriceBar]:
        raise SourceUnavailableError(f"price history for {ticker}", self.note)


@dataclass(frozen=True)
class BlockedMacroSource:
    """CBE macro in an environment with no outbound network."""

    note: str = BLOCKED_NOTE

    def snapshot(self, as_of: str) -> MacroSnapshot:
        raise SourceUnavailableError("CBE macro snapshot", self.note)


@dataclass(frozen=True)
class UploadedFilingDiscovery:
    """Discovery over a local directory of uploaded filings.

    Naming convention: ``TICKER_FY2025_FY.pdf`` — ticker, fiscal year, period
    type. A file that does not parse is **skipped and named**, never guessed at:
    an unparseable filename is a filing whose period we do not know, and a
    filing filed under the wrong period is worse than one not filed at all.
    """

    directory: Path
    unparsed: list[str] | None = None

    def discover(self, ticker: str, since: str) -> list[FilingRef]:
        if not self.directory.exists():
            raise SourceUnavailableError(
                str(self.directory), "the upload directory does not exist"
            )
        key = ticker.strip().upper()
        found: list[FilingRef] = []
        for path in sorted(self.directory.glob("*.pdf")):
            parts = path.stem.split("_")
            if len(parts) < 3 or parts[0].upper() != key:
                if self.unparsed is not None and parts[0].upper() == key:
                    self.unparsed.append(path.name)
                continue
            year_part = parts[1].upper().removeprefix("FY")
            if not year_part.isdigit():
                if self.unparsed is not None:
                    self.unparsed.append(path.name)
                continue
            found.append(
                FilingRef(
                    ticker=key,
                    url=path.resolve().as_uri(),
                    title=path.name,
                    published_at="",
                    source_type="UPLOAD",
                    fiscal_year=int(year_part),
                    period_type=parts[2].upper(),
                )
            )
        return found
