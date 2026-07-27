"""Research layer: loud failures, idempotent discovery, honest averages."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from config_loader import load_sources
from research.discovery import SeenFilingsLog, discover_filings
from research.market import (
    InsufficientHistoryError,
    average_daily_traded_value,
    build_snapshot,
    moving_average,
    trailing_average_market_cap,
)
from research.offline import (
    BlockedFilingDiscovery,
    BlockedMacroSource,
    BlockedMarketData,
    UploadedFilingDiscovery,
)
from research.protocols import (
    FilingRef,
    MacroSnapshot,
    PriceBar,
    ResearchError,
    SourceUnavailableError,
)
from research.registry import backoff_delays, parse_registry, with_retry

NOW = "2026-07-27T09:00:00+00:00"


def _bars(n: int, *, close: str = "10", mcap: str | None = "1000", volume: str | None = "500") -> list[PriceBar]:
    start = date(2026, 7, 27) - timedelta(days=n - 1)
    return [
        PriceBar(
            on=start + timedelta(days=i),
            close=Decimal(close),
            volume=Decimal(volume) if volume is not None else None,
            market_cap=Decimal(mcap) if mcap is not None else None,
        )
        for i in range(n)
    ]


# ----------------------------------------------------------------------
# Blocked adapters tell the truth
# ----------------------------------------------------------------------
def test_blocked_adapters_raise_with_the_reason_not_empty_results() -> None:
    with pytest.raises(SourceUnavailableError, match="403"):
        BlockedFilingDiscovery().discover("COMI", "2026-01-01")
    with pytest.raises(SourceUnavailableError, match="403"):
        BlockedMarketData().history("COMI", "2026-01-01", "2026-07-27")
    with pytest.raises(SourceUnavailableError, match="403"):
        BlockedMacroSource().snapshot("2026-07-27")


def test_a_blocked_source_carries_the_url_it_failed_on() -> None:
    with pytest.raises(SourceUnavailableError) as exc:
        BlockedMarketData().history("SWDY", "2026-01-01", "2026-07-27")
    assert "SWDY" in exc.value.url


# ----------------------------------------------------------------------
# Uploaded filings
# ----------------------------------------------------------------------
def test_uploaded_discovery_reads_the_naming_convention(tmp_path: Path) -> None:
    (tmp_path / "COMI_FY2025_FY.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "COMI_FY2026_H1.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "SWDY_FY2025_FY.pdf").write_bytes(b"%PDF-1.4")

    refs = UploadedFilingDiscovery(tmp_path).discover("comi", "2020-01-01")
    assert [(r.fiscal_year, r.period_type) for r in refs] == [(2025, "FY"), (2026, "H1")]
    assert all(r.source_type == "UPLOAD" for r in refs)


def test_an_unparseable_filename_is_named_not_guessed(tmp_path: Path) -> None:
    (tmp_path / "COMI_annual_report_final_v2.pdf").write_bytes(b"%PDF-1.4")
    unparsed: list[str] = []
    refs = UploadedFilingDiscovery(tmp_path, unparsed).discover("COMI", "2020-01-01")
    assert refs == []
    assert unparsed == ["COMI_annual_report_final_v2.pdf"]


def test_a_missing_upload_directory_is_a_source_failure(tmp_path: Path) -> None:
    with pytest.raises(SourceUnavailableError):
        UploadedFilingDiscovery(tmp_path / "nope").discover("COMI", "2020-01-01")


# ----------------------------------------------------------------------
# Discovery orchestration
# ----------------------------------------------------------------------
class _FakeDiscovery:
    def __init__(self, refs: dict[str, list[FilingRef]], fail: set[str] | None = None) -> None:
        self.refs = refs
        self.fail = fail or set()
        self.calls: list[str] = []

    def discover(self, ticker: str, since: str) -> list[FilingRef]:
        self.calls.append(ticker)
        if ticker in self.fail:
            raise SourceUnavailableError(f"https://example.test/{ticker}", "403 from the proxy")
        return self.refs.get(ticker, [])


def _ref(ticker: str, n: int = 1) -> FilingRef:
    return FilingRef(
        ticker=ticker,
        url=f"https://example.test/{ticker}/{n}.pdf",
        title=f"{ticker} report {n}",
        published_at="2026-07-20",
        source_type="EGX_DISCLOSURE",
    )


def test_discovery_is_idempotent_across_runs(tmp_path: Path) -> None:
    seen = SeenFilingsLog(tmp_path / "seen.jsonl")
    adapter = _FakeDiscovery({"AAAA": [_ref("AAAA")]})

    first = discover_filings(["AAAA"], adapter, seen, since="2026-01-01", now=NOW)
    assert len(first.new) == 1

    second = discover_filings(["AAAA"], adapter, seen, since="2026-01-01", now=NOW)
    assert second.new == ()
    assert len(second.already_seen) == 1
    assert len(seen.read_all()) == 1


def test_one_failing_company_does_not_abort_the_run_and_is_reported(tmp_path: Path) -> None:
    seen = SeenFilingsLog(tmp_path / "seen.jsonl")
    adapter = _FakeDiscovery({"AAAA": [_ref("AAAA")]}, fail={"BBBB"})

    outcome = discover_filings(["AAAA", "BBBB"], adapter, seen, since="2026-01-01", now=NOW)
    assert len(outcome.new) == 1
    assert outcome.failed == (("BBBB", "403 from the proxy"),)
    assert outcome.complete is False
    assert adapter.calls == ["AAAA", "BBBB"]


def test_discovered_filings_are_stamped_with_the_run_time(tmp_path: Path) -> None:
    seen = SeenFilingsLog(tmp_path / "seen.jsonl")
    outcome = discover_filings(
        ["AAAA"], _FakeDiscovery({"AAAA": [_ref("AAAA")]}), seen, since="2026-01-01", now=NOW
    )
    assert outcome.new[0].discovered_at == NOW
    assert seen.read_all()[0]["discovered_at"] == NOW


def test_seen_log_tracks_content_hashes(tmp_path: Path) -> None:
    seen = SeenFilingsLog(tmp_path / "seen.jsonl")
    seen.record(FilingRef("AAAA", "u", "t", "2026-01-01", "UPLOAD", content_hash="abc123"))
    assert seen.hashes() == {"abc123"}


# ----------------------------------------------------------------------
# Retry
# ----------------------------------------------------------------------
def test_backoff_is_exponential_and_deterministic() -> None:
    assert backoff_delays(4) == (2.0, 4.0, 8.0)
    assert backoff_delays(1) == ()
    with pytest.raises(ValueError, match="attempts must be"):
        backoff_delays(0)


def test_retry_succeeds_after_transient_failures() -> None:
    calls = {"n": 0}
    slept: list[float] = []

    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise SourceUnavailableError("https://example.test", "timeout")
        return "ok"

    assert with_retry(flaky, attempts=4, sleep=slept.append) == "ok"
    assert slept == [2.0, 4.0]


def test_retry_gives_up_and_re_raises() -> None:
    slept: list[float] = []

    def always_fails() -> str:
        raise SourceUnavailableError("https://example.test", "403")

    with pytest.raises(SourceUnavailableError):
        with_retry(always_fails, attempts=3, sleep=slept.append)
    assert slept == [2.0, 4.0]


def test_a_non_transient_failure_is_not_retried() -> None:
    slept: list[float] = []

    def parse_error() -> str:
        raise ResearchError("the page no longer has a table")

    with pytest.raises(ResearchError):
        with_retry(parse_error, attempts=4, sleep=slept.append)
    assert slept == []


# ----------------------------------------------------------------------
# Source registry
# ----------------------------------------------------------------------
def test_shipped_sources_file_parses() -> None:
    registry = parse_registry(load_sources())
    assert registry.exchange
    assert registry.configured_companies == ()


def test_company_sources_come_before_the_exchange_feed_when_higher_priority() -> None:
    registry = parse_registry({
        "exchange": {"egx": {
            "source_type": "EGX_DISCLOSURE", "url_pattern": "https://egx/x",
            "discovery_method": "HTML_SCRAPE", "priority": 2,
        }},
        "companies": {"comi": [{
            "source_type": "COMPANY_IR", "url_pattern": "https://ir/{ticker}/reports",
            "discovery_method": "DIRECTORY_LISTING", "priority": 1,
        }]},
    })
    specs = registry.for_ticker("COMI")
    assert [s.source_type for s in specs] == ["COMPANY_IR", "EGX_DISCLOSURE"]
    assert specs[0].url_for("comi") == "https://ir/COMI/reports"
    # The exchange feed is always included, even for a company with its own page.
    assert len(registry.for_ticker("ZZZZ")) == 1


def test_a_malformed_source_spec_is_refused() -> None:
    with pytest.raises(ResearchError, match="source spec missing"):
        parse_registry({"exchange": {"x": {"source_type": "EGX_DISCLOSURE"}}, "companies": {}})
    with pytest.raises(ResearchError, match="must be a mapping"):
        parse_registry({"exchange": [], "companies": {}})


# ----------------------------------------------------------------------
# Market derivations
# ----------------------------------------------------------------------
def test_trailing_average_market_cap_is_an_average_not_spot() -> None:
    bars = _bars(250, mcap="1000")
    bars[-1] = PriceBar(on=bars[-1].on, close=Decimal("10"), volume=Decimal("500"), market_cap=Decimal("9999"))
    avg = trailing_average_market_cap(bars, as_of=date(2026, 7, 27))
    assert Decimal("1000") < avg < Decimal("1100")   # one spike barely moves a 250-day mean


def test_a_short_history_raises_rather_than_averaging_what_exists() -> None:
    with pytest.raises(InsufficientHistoryError, match="different statistic"):
        trailing_average_market_cap(_bars(30), as_of=date(2026, 7, 27))


def test_bars_outside_the_window_are_excluded() -> None:
    old = [
        PriceBar(on=date(2020, 1, 1) + timedelta(days=i), close=Decimal("1"), market_cap=Decimal("1"))
        for i in range(300)
    ]
    with pytest.raises(InsufficientHistoryError):
        trailing_average_market_cap(old, as_of=date(2026, 7, 27))


def test_moving_averages_need_a_full_window() -> None:
    assert moving_average(_bars(49), 50) is None
    assert moving_average(_bars(50), 50) == Decimal("10")
    with pytest.raises(ValueError, match="window must be positive"):
        moving_average(_bars(10), 0)


def test_adtv_refuses_a_window_with_missing_volume() -> None:
    bars = _bars(60)
    assert average_daily_traded_value(bars) == Decimal("5000")
    bars[-1] = PriceBar(on=bars[-1].on, close=Decimal("10"), volume=None, market_cap=Decimal("1000"))
    assert average_daily_traded_value(bars) is None
    assert average_daily_traded_value(_bars(59)) is None


def test_snapshot_leaves_unavailable_statistics_as_none() -> None:
    snap = build_snapshot("aaaa", _bars(30), as_of=date(2026, 7, 27))
    assert snap.ticker == "AAAA"
    assert snap.mcap_avg_12m is None      # too little history — not approximated
    assert snap.ma_200 is None
    assert snap.close_price == Decimal("10")
    assert snap.observations == 30


def test_snapshot_computes_what_it_can_from_a_full_history() -> None:
    snap = build_snapshot("AAAA", _bars(250), as_of=date(2026, 7, 27))
    assert snap.mcap_avg_12m == Decimal("1000")
    assert snap.ma_50 == Decimal("10")
    assert snap.ma_200 == Decimal("10")
    assert snap.ma_200_rising is False     # flat prices are not a rising average
    assert snap.adtv_60d == Decimal("5000")


def test_snapshot_of_an_empty_history_is_all_none() -> None:
    snap = build_snapshot("AAAA", [], as_of=date(2026, 7, 27))
    assert snap.close_price is None
    assert snap.observations == 0


# ----------------------------------------------------------------------
# Macro
# ----------------------------------------------------------------------
def test_macro_snapshot_reports_incompleteness() -> None:
    partial = MacroSnapshot(as_of="2026-07-27", cpi_yoy=Decimal("0.12"))
    assert partial.complete is False
    full = MacroSnapshot(
        as_of="2026-07-27", cpi_yoy=Decimal("0.12"), tbill_1y_yield=Decimal("0.19"),
        policy_rate=Decimal("0.21"), usd_egp=Decimal("48.5"),
    )
    assert full.complete is True
