"""Routines: idempotent, quiet on purpose, loud about what they could not check."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

import tools.analysis_tools
import tools.portfolio_tools  # noqa: F401  (tool registration)
from config_loader import load_policy, load_thresholds, load_universe
from engine.universe import Constituent, Provenance, Universe, UniverseStatus
from research.discovery import SeenFilingsLog
from research.protocols import FilingRef, PriceBar, SourceUnavailableError
from routines.base import already_ran, run_key
from routines.daily import (
    daily_filing_poll,
    daily_market_refresh,
    make_daily_filing_poll,
    make_daily_market_refresh,
)
from routines.periodic import make_quarterly_review, make_weekly_digest, weekly_digest
from store.jsonl_decisions import JsonlDecisionStore
from store.jsonl_ledger import JsonlLedgerStore
from store.jsonl_orders import JsonlOrderStore
from tools.audit import AuditLog
from tools.context import ToolContext
from tools.data import CompanyFacts, InMemoryDataProvider, NoDataProvider
from tools.registry import call_tool

NOW = "2026-07-27T09:00:00+00:00"


@pytest.fixture()
def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(
        now=NOW,
        ledger=JsonlLedgerStore(tmp_path / "ledger.jsonl"),
        orders=JsonlOrderStore(tmp_path / "orders.jsonl"),
        decisions=JsonlDecisionStore(tmp_path / "journal.jsonl"),
        audit=AuditLog(tmp_path / "audit.jsonl"),
        cfg=load_thresholds(),
        policy=load_policy(),
        universe=load_universe(),
        prices={},
        data=NoDataProvider(),
    )


class _FakeDiscovery:
    def __init__(self, refs: dict[str, list[FilingRef]], fail: set[str] | None = None) -> None:
        self.refs = refs
        self.fail = fail or set()

    def discover(self, ticker: str, since: str) -> list[FilingRef]:
        if ticker in self.fail:
            raise SourceUnavailableError(f"https://example.test/{ticker}", "403 from the proxy")
        return self.refs.get(ticker, [])


class _FakeMarket:
    def __init__(self, prices: dict[str, str], fail: set[str] | None = None) -> None:
        self.prices = prices
        self.fail = fail or set()

    def history(self, ticker: str, start: str, end: str) -> list[PriceBar]:
        if ticker in self.fail:
            raise SourceUnavailableError(ticker, "no data source configured")
        from datetime import date

        return [PriceBar(on=date(2026, 7, 27), close=Decimal(self.prices[ticker]))]


def _seed_holding(ctx: ToolContext, ticker: str = "AAAA") -> None:
    call_tool("record_contribution", {"amount": "25000", "request_id": "c1", "verbatim": "added"}, ctx)
    from reporting.order_sheet import Side, Validity

    ctx.orders.propose(
        order_id=f"{ticker}-1", at=NOW, ticker=ticker, side=Side.BUY,
        quantity=Decimal("100"), limit_price=Decimal("10.50"), validity=Validity.DAY,
    )
    call_tool(
        "confirm_order_fill",
        {"order_id": f"{ticker}-1", "shares": "100", "price": "10", "request_id": "f1",
         "verbatim": "bought 100 at 10"},
        ctx,
    )


# ----------------------------------------------------------------------
# Idempotency
# ----------------------------------------------------------------------
def test_a_routine_runs_once_a_day(ctx: ToolContext, tmp_path: Path) -> None:
    seen = SeenFilingsLog(tmp_path / "seen.jsonl")
    refs = {"AAAA": [FilingRef("AAAA", "https://x/1.pdf", "FY2025", "2026-07-20", "EGX_DISCLOSURE")]}
    poll = make_daily_filing_poll(_FakeDiscovery(refs), seen, since="2026-01-01")
    seeded = _with_universe(ctx)

    first = poll(seeded)
    assert first.details["new"]
    assert already_ran(seeded, "daily_filing_poll") is True

    second = poll(seeded)
    assert second.details == {"skipped": "already ran today"}
    assert len(seen.read_all()) == 1


def test_a_new_day_runs_again(ctx: ToolContext, tmp_path: Path) -> None:
    seen = SeenFilingsLog(tmp_path / "seen.jsonl")
    poll = make_daily_filing_poll(_FakeDiscovery({}), seen, since="2026-01-01")
    seeded = _with_universe(ctx)
    poll(seeded)
    tomorrow = ToolContext(**{**seeded.__dict__, "now": "2026-07-28T09:00:00+00:00"})
    assert already_ran(tomorrow, "daily_filing_poll") is False


def test_run_keys_are_per_routine_and_per_day() -> None:
    assert run_key("weekly_digest", NOW) == "routine:weekly_digest:2026-07-27"
    assert run_key("weekly_digest", NOW) != run_key("quarterly_review", NOW)


# ----------------------------------------------------------------------
# Filing poll
# ----------------------------------------------------------------------
def _with_universe(ctx: ToolContext) -> ToolContext:
    universe = Universe(
        version="test",
        status=UniverseStatus.POPULATED,
        index_name="Test index",
        expected_count=None,
        constituents=(Constituent("AAAA", "Alpha", sector="Materials"),),
        off_index_watch=(),
        retrieved=Provenance("2026-07-27", "fixture", "test"),
    )
    return ToolContext(**{**ctx.__dict__, "universe": universe})


def test_the_poll_stays_silent_when_there_is_no_news(ctx: ToolContext, tmp_path: Path) -> None:
    poll = make_daily_filing_poll(_FakeDiscovery({}), SeenFilingsLog(tmp_path / "s.jsonl"), since="2026-01-01")
    result = poll(_with_universe(ctx))
    assert result.message == ""
    assert result.quiet is True
    assert result.clean is True


def test_the_poll_speaks_when_it_could_not_reach_a_company(ctx: ToolContext, tmp_path: Path) -> None:
    """'No new filings' would be a claim about a company we never reached."""
    poll = make_daily_filing_poll(
        _FakeDiscovery({}, fail={"AAAA"}), SeenFilingsLog(tmp_path / "s.jsonl"), since="2026-01-01"
    )
    result = poll(_with_universe(ctx))
    assert "could not check AAAA" in result.message
    assert result.clean is False
    assert result.quiet is False


def test_the_poll_announces_a_new_filing_without_promising_action(ctx: ToolContext, tmp_path: Path) -> None:
    refs = {"AAAA": [FilingRef("AAAA", "https://x/1.pdf", "FY2025 annual", "2026-07-20", "EGX_DISCLOSURE")]}
    poll = make_daily_filing_poll(
        _FakeDiscovery(refs), SeenFilingsLog(tmp_path / "s.jsonl"), since="2026-01-01"
    )
    result = poll(_with_universe(ctx))
    assert "New filing(s) from AAAA" in result.message
    assert "No action needed from you yet" in result.message


def test_the_poll_covers_holdings_outside_the_universe(ctx: ToolContext, tmp_path: Path) -> None:
    _seed_holding(ctx, "ZZZZ")
    seen = SeenFilingsLog(tmp_path / "s.jsonl")
    refs = {"ZZZZ": [FilingRef("ZZZZ", "https://x/z.pdf", "H1", "2026-07-20", "EGX_DISCLOSURE")]}
    result = make_daily_filing_poll(_FakeDiscovery(refs), seen, since="2026-01-01")(ctx)
    assert "ZZZZ" in result.message


def test_the_poll_is_quiet_with_nothing_to_watch(ctx: ToolContext, tmp_path: Path) -> None:
    result = make_daily_filing_poll(
        _FakeDiscovery({}), SeenFilingsLog(tmp_path / "s.jsonl"), since="2026-01-01"
    )(ctx)
    assert result.quiet is True
    assert result.details["reason"].startswith("nothing to watch")  # type: ignore[union-attr]


def test_default_routines_report_that_no_adapter_is_configured(ctx: ToolContext) -> None:
    assert daily_filing_poll(ctx).quiet is True
    assert "no discovery adapter" in str(daily_filing_poll(ctx).details["reason"])
    assert "no market data source" in str(daily_market_refresh(ctx).details["reason"])


# ----------------------------------------------------------------------
# Market refresh
# ----------------------------------------------------------------------
def test_market_refresh_reports_prices_it_could_not_get(ctx: ToolContext) -> None:
    _seed_holding(ctx)
    refresh = make_daily_market_refresh(
        _FakeMarket({}, fail={"AAAA"}), start="2026-01-01", end="2026-07-27"
    )
    result = refresh(ctx)
    assert "not carried forward at their last known value" in result.message
    assert result.failures


def test_market_refresh_is_quiet_when_it_worked(ctx: ToolContext) -> None:
    _seed_holding(ctx)
    refresh = make_daily_market_refresh(_FakeMarket({"AAAA": "12.5"}), start="2026-01-01", end="2026-07-27")
    result = refresh(ctx)
    assert result.details["refreshed"] == {"AAAA": "12.5"}
    assert result.message == ""


def test_market_refresh_skips_an_empty_portfolio(ctx: ToolContext) -> None:
    result = make_daily_market_refresh(_FakeMarket({}), start="2026-01-01", end="2026-07-27")(ctx)
    assert result.quiet is True


# ----------------------------------------------------------------------
# Weekly digest — the quiet-week message is the feature
# ----------------------------------------------------------------------
def test_a_quiet_week_still_sends_a_message(ctx: ToolContext) -> None:
    result = make_weekly_digest("2026-07-20")(ctx)
    assert result.quiet is True
    assert result.message == "Quiet week. Nothing filed, nothing breached, nothing to do."


def test_a_busy_week_summarises_what_happened(ctx: ToolContext) -> None:
    _seed_holding(ctx)
    result = make_weekly_digest("2026-07-01")(ctx)
    assert result.quiet is False
    assert "portfolio event" in result.message


def test_the_digest_flags_outstanding_proposals(ctx: ToolContext) -> None:
    from reporting.order_sheet import Side, Validity

    ctx.orders.propose(
        order_id="AAAA-9", at=NOW, ticker="AAAA", side=Side.BUY,
        quantity=Decimal("10"), limit_price=Decimal("10"), validity=Validity.DAY,
    )
    result = make_weekly_digest("2026-07-20")(ctx)
    assert "awaiting your fills" in result.message


def test_the_default_digest_covers_the_last_seven_days(ctx: ToolContext) -> None:
    result = weekly_digest(ctx)
    assert result.routine == "weekly_digest"
    assert result.at == NOW


# ----------------------------------------------------------------------
# Quarterly review
# ----------------------------------------------------------------------
def test_quarterly_review_with_nothing_held(ctx: ToolContext) -> None:
    result = make_quarterly_review()(ctx)
    assert result.quiet is True
    assert "nothing to review" in result.message


def test_quarterly_review_names_holdings_it_could_not_check(ctx: ToolContext) -> None:
    _seed_holding(ctx)
    result = make_quarterly_review()(ctx)
    assert "could not review AAAA" in result.message
    assert "not assumed fine" in result.message
    assert result.failures


def test_quarterly_review_journals_a_decision_per_holding(
    ctx: ToolContext, clean_company: CompanyFacts
) -> None:
    _seed_holding(ctx, clean_company.ticker)
    seeded = ToolContext(**{
        **ctx.__dict__,
        "data": InMemoryDataProvider({clean_company.ticker: clean_company}),
        "prices": {clean_company.ticker: Decimal("12")},
    })
    result = make_quarterly_review()(seeded)
    assert result.details["reviewed"] == 1
    assert len(seeded.decisions.read_all()) == 1
    assert result.details["journalled"]


def test_re_running_the_quarterly_review_does_not_duplicate_decisions(
    ctx: ToolContext, clean_company: CompanyFacts
) -> None:
    _seed_holding(ctx, clean_company.ticker)
    seeded = ToolContext(**{
        **ctx.__dict__,
        "data": InMemoryDataProvider({clean_company.ticker: clean_company}),
        "prices": {clean_company.ticker: Decimal("12")},
    })
    make_quarterly_review()(seeded)
    # Same day: the guard stops it. Next day: the content-addressed decision id does.
    tomorrow = ToolContext(**{**seeded.__dict__, "now": "2026-07-28T09:00:00+00:00"})
    make_quarterly_review()(tomorrow)
    assert len(seeded.decisions.read_all()) == 1
