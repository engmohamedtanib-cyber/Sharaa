"""Daily routines: poll for filings, refresh market data.

Both are written to be boring on most days. The filing poll's usual output is
nothing at all — it stays silent rather than sending a daily "no news" message,
because a notification that fires every day stops being read. The weekly digest
is where quiet gets reported (``routines.periodic``).
"""

from __future__ import annotations

from research.discovery import SeenFilingsLog, discover_filings
from research.protocols import FilingDiscovery, MarketDataSource, SourceUnavailableError
from routines.base import Routine, RoutineResult, guard
from tools.context import ToolContext

DAILY_FILING_POLL = "daily_filing_poll"
DAILY_MARKET_REFRESH = "daily_market_refresh"


def make_daily_filing_poll(
    adapter: FilingDiscovery,
    seen: SeenFilingsLog,
    *,
    since: str,
) -> Routine:
    """Build the poll around a discovery adapter.

    The adapter is injected rather than constructed here so the routine can be
    driven by a real source, by an upload directory, or by a fixture, without
    the routine knowing which.
    """

    @guard(DAILY_FILING_POLL)
    def routine(ctx: ToolContext) -> RoutineResult:
        """Check every company we care about for new filings."""
        tickers = sorted({
            *(c.ticker for c in ctx.universe.constituents),
            *(c.ticker for c in ctx.universe.off_index_watch),
            *ctx.ledger.state().open_holdings,
        })
        if not tickers:
            return RoutineResult(
                routine=DAILY_FILING_POLL,
                at=ctx.now,
                quiet=True,
                message="",
                details={"reason": "nothing to watch: no universe and no holdings"},
            )

        outcome = discover_filings(tickers, adapter, seen, since=since, now=ctx.now)
        failures = tuple(f"{ticker}: {reason}" for ticker, reason in outcome.failed)

        if not outcome.new:
            # Silence on a normal day; a failure still speaks, because "no new
            # filings" would be a claim we cannot make about a company we could
            # not reach.
            return RoutineResult(
                routine=DAILY_FILING_POLL,
                at=ctx.now,
                quiet=not failures,
                message=(
                    "I could not check " + ", ".join(t for t, _ in outcome.failed) + " today. "
                    "No new filings from the companies I could reach."
                ) if failures else "",
                details={"checked": len(tickers), "new": 0},
                failures=failures,
            )

        names = ", ".join(sorted({ref.ticker for ref in outcome.new}))
        return RoutineResult(
            routine=DAILY_FILING_POLL,
            at=ctx.now,
            message=(
                f"New filing(s) from {names}. I will extract and screen them, then tell you whether "
                "anything changes. No action needed from you yet."
            ),
            details={
                "checked": len(tickers),
                "new": [{"ticker": r.ticker, "title": r.title, "url": r.url} for r in outcome.new],
            },
            failures=failures,
        )

    return routine


def make_daily_market_refresh(source: MarketDataSource, *, start: str, end: str) -> Routine:
    """Build the market refresh around a price source."""

    @guard(DAILY_MARKET_REFRESH)
    def routine(ctx: ToolContext) -> RoutineResult:
        """Refresh prices for held companies, so weights and alerts stay real."""
        held = sorted(ctx.ledger.state().open_holdings)
        if not held:
            return RoutineResult(
                routine=DAILY_MARKET_REFRESH, at=ctx.now, quiet=True,
                details={"reason": "no holdings to price"},
            )

        refreshed: dict[str, str] = {}
        failures: list[str] = []
        for ticker in held:
            try:
                bars = source.history(ticker, start, end)
            except SourceUnavailableError as exc:
                failures.append(f"{ticker}: {exc.reason}")
                continue
            if bars:
                refreshed[ticker] = str(bars[-1].close)

        return RoutineResult(
            routine=DAILY_MARKET_REFRESH,
            at=ctx.now,
            quiet=not failures,
            message=(
                "I have no current prices for " + ", ".join(f.split(":")[0] for f in failures)
                + ". Weights and drawdown checks exclude them until a price arrives — "
                  "they are not carried forward at their last known value."
            ) if failures else "",
            details={"refreshed": refreshed},
            failures=tuple(failures),
        )

    return routine


def daily_filing_poll(ctx: ToolContext) -> RoutineResult:
    """Default filing poll: no adapter configured, so it says so once per day."""
    return RoutineResult(
        routine=DAILY_FILING_POLL,
        at=ctx.now,
        quiet=True,
        details={"reason": "no discovery adapter configured; build one with make_daily_filing_poll"},
    )


def daily_market_refresh(ctx: ToolContext) -> RoutineResult:
    """Default market refresh: no source configured."""
    return RoutineResult(
        routine=DAILY_MARKET_REFRESH,
        at=ctx.now,
        quiet=True,
        details={"reason": "no market data source configured; build one with make_daily_market_refresh"},
    )
