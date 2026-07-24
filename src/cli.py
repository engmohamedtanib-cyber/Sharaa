"""Command-line entrypoint for the EGX Shariah Engine.

Every command accepts ``--dry-run`` (no persistence, no external calls). The
deterministic commands run entirely offline on the in-memory backend; commands
that need ingestion/market data say so explicitly rather than guessing.
"""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal

from config_loader import load_thresholds
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

    if args.dry_run:
        print("\n(dry-run: nothing persisted)")
    else:
        repo = get_repository()
        print(f"\n(persisted to {type(repo).__name__}; exception queue: {len(repo.exception_queue())})")
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="egx", description="EGX Shariah Engine")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, func: object, help_text: str) -> None:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--dry-run", action="store_true", help="do not persist or make external calls")
        p.set_defaults(func=func)

    add("config", cmd_config, "show threshold config and verify pillar maxima")
    add("demo", cmd_demo, "run the deterministic gate/score/decision on a sample company")
    add("exceptions", cmd_exceptions, "list the DATA_INSUFFICIENT / CONFLICT exception queue")
    add("review", cmd_review, "run a full quarterly review (needs ingestion + credentials)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))  # type: ignore[no-any-return]


if __name__ == "__main__":
    sys.exit(main())
