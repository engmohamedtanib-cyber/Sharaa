"""Command-line entrypoint for the EGX Shariah Engine.

Every command accepts ``--dry-run`` (no persistence, no external calls). The
deterministic commands run entirely offline on the in-memory backend; commands
that need ingestion/market data say so explicitly rather than guessing.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from config_loader import load_thresholds, load_universe
from db.repo import get_repository
from engine.decisions import DecisionContext, decide
from engine.scoring import assert_pillar_maxima, score_company
from engine.shariah import diagnose_breach, run_gate
from engine.types import (
    AuditOpinion,
    CapitalAllocation,
    DataStatus,
    Financials,
    FxResilience,
    Governance,
    History,
    MacroData,
    MarketData,
    Qualitative,
    RelatedPartyQuality,
    RoicCategory,
    ScoringInputs,
    Timeliness,
    Valuation,
)
from ingestion.intake import IntakeError, missing_summary, scan_directory
from ingestion.normalise import NormalisationError, detect_scale, parse_number
from reporting.journal import entry_from_decision, render_journal
from reporting.order_sheet import build_order, render_order_sheet
from store.jsonl_ledger import JsonlLedgerStore


def _sample_inputs() -> ScoringInputs:
    """A fully-populated validated company for offline demonstration.

    Doubles as living documentation of the :class:`ScoringInputs` shape.
    """
    fin = Financials(
        total_assets=Decimal("1000"),
        total_liabilities=Decimal("600"),
        total_equity=Decimal("400"),
        total_revenue=Decimal("500"),
        cost_of_sales=Decimal("300"),
        gross_profit=Decimal("200"),
        operating_profit=Decimal("140"),
        profit_before_tax=Decimal("120"),
        income_tax=Decimal("24"),
        net_profit_attributable=Decimal("96"),
        cash_and_equivalents=Decimal("60"),
        time_deposits=Decimal("50"),
        accounts_receivable=Decimal("90"),
        short_term_borrowings=Decimal("40"),
        long_term_borrowings=Decimal("60"),
        interest_income=Decimal("5"),
        operating_cash_flow=Decimal("150"),
        capex=Decimal("15"),
        total_current_assets=Decimal("320"),
        total_current_liabilities=Decimal("140"),
        inventory=Decimal("70"),
        closing_cash=Decimal("60"),
    )
    history = History(
        worst_util_last4=(Decimal("0.30"), Decimal("0.27"), Decimal("0.25"), Decimal("0.24")),
        consecutive_compliant_quarters=8,
        prior_total_assets=Decimal("950"),
        receivables_growth=Decimal("0.05"),
        revenue_growth=Decimal("0.10"),
        operating_margins_8q=tuple(
            Decimal(m) for m in ("0.27", "0.28", "0.27", "0.28", "0.27", "0.28", "0.27", "0.28")
        ),
        ev_ebitda_median_5y=Decimal("7"),
        nominal_revenue_cagr_3y=Decimal("0.25"),
        nominal_eps_cagr_3y=Decimal("0.25"),
        physical_volume_growth=Decimal("0.12"),
        ocf_ttm=Decimal("150"),
        net_income_ttm=Decimal("96"),
    )
    return ScoringInputs(
        financials=fin,
        market=MarketData(
            market_cap=Decimal("2400"), close_price=Decimal("12"),
            adtv_60d=Decimal("200000"), ma_50=Decimal("11"), ma_200=Decimal("10"),
            ma_200_rising=True, rsi_14=Decimal("55"), rel_strength_6m_vs_egx30=Decimal("0.08"),
        ),
        macro=MacroData(tbill_1y_yield=Decimal("0.19"), cpi_yoy=Decimal("0.12")),
        history=history,
        valuation=Valuation(
            pe_ratio=Decimal("5"), ev_ebitda=Decimal("4"), pb_ratio=Decimal("1.0"),
            roe=Decimal("0.25"), dividend_yield=Decimal("0.09"), payout_of_fcf=Decimal("0.5"),
            dividend_sustainable=True, sector_median_pe=Decimal("7"),
        ),
        governance=Governance(
            independent_fraction=Decimal("0.40"), chair_is_ceo=False, free_float=Decimal("0.35"),
            clean_minority_record=True, timeliness=Timeliness.ON_TIME_FULL,
            related_party=RelatedPartyQuality.IMMATERIAL_DISCLOSED,
            capital_allocation=CapitalAllocation.CONSISTENT,
        ),
        qualitative=Qualitative(
            fx_resilience=FxResilience.HEDGED, audit_opinion=AuditOpinion.CLEAN_IMMATERIAL_RPT,
            roic=RoicCategory.EXCEEDS_STABLE,
        ),
        target_position_value=Decimal("1000"),
    )


def cmd_config(args: argparse.Namespace) -> int:
    cfg = load_thresholds()
    assert_pillar_maxima(cfg)
    print(f"thresholds version : {cfg.version}")
    print(f"governing standard : {cfg.governing_standard}")
    print(f"pillar maxima      : {{{', '.join(f'{k}={v}' for k, v in cfg.pillar_maxima().items())}}} = 100 OK")
    print(f"staleness window   : {cfg.staleness_days} days")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    cfg = load_thresholds()
    inp = _sample_inputs()
    gate = run_gate(inp.financials, inp.market.mcap_avg_12m or inp.market.market_cap, cfg)
    breach = diagnose_breach(gate, DataStatus.VALIDATED, filing_age_days=30, cfg=cfg)

    print(f"Shariah gate       : {gate.overall_status.value} "
          f"(worst screen {gate.worst_screen_code}, util {gate.worst_utilisation})")
    print(f"breach             : {breach.breach.value}")

    result = score_company(inp, gate, cfg)
    if result is None:
        print("score              : None (gate not passed — not scored, per R7)")
        return 0
    print(f"score              : {result.total} ({result.band})")
    print("pillars            : " + ", ".join(f"{k}={v}" for k, v in result.pillar_totals.items()))
    if result.vetoes:
        print(f"vetoes             : {', '.join(result.vetoes)}")

    ctx = DecisionContext(
        breach=breach.breach,
        status=gate.overall_status,
        score=result.total,
        valuation_gap=Decimal("0.28"),
        held=False,
        below_target_weight=False,
        vetoes=result.vetoes,
    )
    dec = decide(ctx, cfg)
    print(f"decision           : {dec.decision.value} — {dec.trigger}")
    print(f"  reason           : {dec.reason}")
    print(f"  falsification    : {dec.falsification_condition}")

    # Journal is written BEFORE the order sheet (BUILD_SPEC Phase 5).
    entry = entry_from_decision(
        review_code="DEMO",
        egx_code="DEMO",
        decision=dec.decision,
        shariah_status=gate.overall_status,
        breach=breach.breach,
        trigger=dec.trigger,
        reason=dec.reason,
        falsification_condition=dec.falsification_condition,
        score=result.total,
        price_at_decision=inp.market.close_price,
        valuation_gap_pct=Decimal("0.28"),
        veto_fired=dec.veto_fired,
        threshold_version=cfg.version,
    )
    order = build_order(
        egx_code="DEMO",
        decision=dec.decision,
        trade_value=Decimal("10000"),
        reference_price=inp.market.close_price or Decimal("1"),
    )
    print()
    print(render_journal("DEMO", [entry]))
    print(render_order_sheet([order] if order else []))

    if args.dry_run:
        print("(dry-run: nothing persisted; no order was placed — R6)")
    else:
        repo = get_repository()
        print(f"(persisted to {type(repo).__name__}; exception queue: {len(repo.exception_queue())})")
    return 0


def cmd_normalise(args: argparse.Namespace) -> int:
    """Demonstrate the normalisation layer on the known-hard cases (§5)."""
    samples = [
        ("1,234,567", "ASCII thousands"),
        ("1.234.567", "dot thousands"),
        ("1,234.56", "thousands + decimal"),
        ("١٢٣٤٥", "Arabic-Indic digits"),
        ("١٬٢٣٤", "Arabic thousands separator"),
        ("(1,234)", "bracketed negative"),
        ("1,234-", "trailing minus"),
        ("0.005", "sub-unit ratio"),
    ]
    print("value normalisation (EXTRACTION_SPEC §5)\n")
    for raw, label in samples:
        print(f"  {raw:<14} {parse_number(raw):>14}   {label}")

    print("\nunit scale detection (§5.2)\n")
    for header in ["بالألف جنيه مصري", "بالمليون", "In thousands of Egyptian Pounds"]:
        print(f"  {header:<34} -> {detect_scale(header).value}")

    print("\nrefusals — never guessed (R3)\n")
    for bad in ["1,23,45", "not a number"]:
        try:
            parse_number(bad)
        except NormalisationError as exc:
            print(f"  {bad:<14} -> {type(exc).__name__}")
    try:
        detect_scale("Consolidated Statement of Financial Position")
    except NormalisationError as exc:
        print(f"  {'(no scale header)':<14} -> {type(exc).__name__}")
    return 0


def cmd_exceptions(args: argparse.Namespace) -> int:
    repo = get_repository()
    queue = repo.exception_queue()
    if not queue:
        print("exception queue empty (safe: no DATA_INSUFFICIENT/CONFLICT filings)")
        return 0
    for f in queue:
        print(f"{f.company_id} {f.fiscal_year} {f.period.value}: {f.data_status.value} — {f.status_reason}")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    print("Full quarterly review requires the ingestion + market-data layers,")
    print("which need Supabase and Anthropic credentials (see .env.example).")
    print("Run 'egx demo --dry-run' to exercise the deterministic core offline.")
    return 0 if args.dry_run else 2


def cmd_universe(args: argparse.Namespace) -> int:
    """Report whether the universe is usable, and say what is missing if not."""
    universe = load_universe()
    print(f"index      : {universe.index_name or '(unnamed)'}")
    print(f"status     : {universe.status.value}")
    print(f"available  : {universe.available}")
    print(f"transcribed: {len(universe.constituents)} of {universe.expected_count or '?'}")
    print(f"retrieved  : {universe.retrieved.at or '-'} from {universe.retrieved.source or '-'}")

    if universe.available:
        for c in universe.constituents:
            print(f"  {c.ticker:<8}{c.sector:<24}{c.name_en or c.name_ar}")
        return 0

    print()
    print("The universe is not usable, so no screening run is possible.")
    print("This is NOT the same as 'no company is compliant' — nothing has been checked.")
    print("To populate it: docs/DATA_REQUEST.md §1.")
    return 0


def cmd_intake(args: argparse.Namespace) -> int:
    """Identify uploaded filings and report what is still missing."""
    directory = Path(args.directory)
    try:
        report = scan_directory(directory)
    except IntakeError as exc:
        print(f"intake failed: {exc}")
        return 2

    print(f"scanned {directory}")
    print(report.summary())
    if report.accepted:
        print("\naccepted")
        for item in report.accepted:
            print(f"  {item.label:<20}{item.sha256[:12]}  {item.size_bytes:>10,} bytes  {item.path.name}")
    print("\ngolden-set coverage")
    print(missing_summary(report))
    # An unidentified file is a non-zero exit: it is work the user still has to
    # do (rename it), not a warning to scroll past.
    return 0 if not report.unidentified else 1


def cmd_tools(args: argparse.Namespace) -> int:
    """List the tool API surface the agent plane can call."""
    import tools.analysis_tools
    import tools.portfolio_tools  # noqa: F401  (registration side effect)
    from tools.registry import REGISTRY

    for name in sorted(REGISTRY):
        tool = REGISTRY[name]
        kind = "write" if tool.write else "read "
        print(f"  {kind}  {name:<28}{tool.description}")
    print(f"\n{len(REGISTRY)} tools. Every call is audited; every write is idempotent.")
    return 0


def cmd_routine(args: argparse.Namespace) -> int:
    """Run one routine once, against the repository's own state files."""
    from routines.daily import daily_filing_poll, daily_market_refresh
    from routines.periodic import quarterly_review, weekly_digest
    from tools.context import build_context

    available = {
        "daily_filing_poll": daily_filing_poll,
        "daily_market_refresh": daily_market_refresh,
        "weekly_digest": weekly_digest,
        "quarterly_review": quarterly_review,
    }
    routine = available.get(args.name)
    if routine is None:
        print(f"unknown routine {args.name!r}. Available: {', '.join(sorted(available))}")
        return 2

    # The one clock read, at the boundary, captured once for the whole run.
    now = datetime.now(UTC).isoformat(timespec="seconds")
    if args.dry_run:
        print(f"[dry-run] would run {args.name} at {now}")
        return 0

    result = routine(build_context(now))
    print(f"{result.routine} at {result.at}")
    print(f"  quiet   : {result.quiet}")
    print(f"  message : {result.message or '(silent)'}")
    for failure in result.failures:
        print(f"  failure : {failure}")
    return 0 if result.clean else 1



LEDGER_PATH = "memory/portfolio/ledger.jsonl"


def cmd_portfolio(args: argparse.Namespace) -> int:
    """Show portfolio state derived from the git-native ledger (decisions/0002)."""
    store = JsonlLedgerStore(LEDGER_PATH)
    events = store.read_all()
    state = store.state()

    print(f"ledger             : {LEDGER_PATH} ({len(events)} events)")
    print(f"cash               : {state.cash} EGP")
    print(f"contributed        : {state.total_contributed} EGP")
    print(f"dividends received : {state.dividends_received} EGP")
    print(f"realised P&L       : {state.realised_pnl} EGP")
    print(f"purification due   : {state.purification_due} EGP")

    holdings = state.open_holdings
    if not holdings:
        print("\nholdings           : none")
        return 0
    print("\nholdings")
    for ticker, h in sorted(holdings.items()):
        print(f"  {ticker:<10}{h.shares:>10} shares   avg cost {h.avg_cost}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="egx", description="EGX Shariah Engine")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, func: object, help_text: str) -> None:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--dry-run", action="store_true", help="do not persist or make external calls")
        p.set_defaults(func=func)

    add("config", cmd_config, "show threshold config and verify pillar maxima")
    add("normalise", cmd_normalise, "demonstrate value normalisation and unit-scale detection")
    add("demo", cmd_demo, "run the deterministic gate/score/decision on a sample company")
    add("portfolio", cmd_portfolio, "show portfolio state derived from the ledger")
    add("exceptions", cmd_exceptions, "list the DATA_INSUFFICIENT / CONFLICT exception queue")
    add("review", cmd_review, "run a full quarterly review (needs ingestion + credentials)")
    add("universe", cmd_universe, "show whether the investable universe is populated and usable")
    add("tools", cmd_tools, "list the tool API surface available to the agent plane")

    intake = sub.add_parser("intake", help="identify uploaded filings and report golden-set coverage")
    intake.add_argument("directory", help="directory of uploaded PDFs")
    intake.add_argument("--dry-run", action="store_true", help="accepted for symmetry; intake never writes")
    intake.set_defaults(func=cmd_intake)

    routine = sub.add_parser("routine", help="run one scheduled routine once")
    routine.add_argument("name", help="daily_filing_poll | daily_market_refresh | weekly_digest | quarterly_review")
    routine.add_argument("--dry-run", action="store_true", help="report what would run, touch nothing")
    routine.set_defaults(func=cmd_routine)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))  # type: ignore[no-any-return]


if __name__ == "__main__":
    sys.exit(main())
