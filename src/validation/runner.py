"""Validation orchestration: run V1-V9, derive ``data_status`` (§6).

Pure (no network). Also provides a *type-level* gate — :class:`ValidatedFiling`
— so a filing that did not validate cannot be handed to scoring by construction,
not merely by an ``if`` (BUILD_SPEC Phase 3 acceptance).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from engine.config import Thresholds
from engine.types import DataStatus, Financials
from validation.agreement import v9_dual_extraction_agreement
from validation.arithmetic import (
    v1_balance_sheet_identity,
    v2_income_statement_chain,
    v3_cash_flow_tie_out,
    v4_borrowings_component_sum,
)
from validation.continuity import v5_comparative_continuity, v6_cumulative_monotonicity
from validation.plausibility import v7_magnitude_plausibility, v8_segment_reconciliation
from validation.types import ValidationResult

# The 12 critical line items (EXTRACTION_SPEC §3.1) checked by V9.
CRITICAL_KEYS = [
    "total_assets",
    "cash_and_equivalents",
    "time_deposits",
    "treasury_bills_and_bonds",
    "accounts_receivable",
    "short_term_borrowings",
    "long_term_borrowings",
    "bank_overdraft",
    "bonds_payable",
    "total_revenue",
    "interest_income",
    "net_profit_attributable",
]


@dataclass(frozen=True)
class ValidationInputs:
    financials: Financials
    market_cap: Decimal | None = None
    close_price: Decimal | None = None
    shares_out: Decimal | None = None
    total_borrowings_disclosed: Decimal | None = None
    current_comparatives: dict[str, Decimal] = field(default_factory=dict)
    stored_prior: dict[str, Decimal] = field(default_factory=dict)
    cumulative_revenue_series: list[Decimal] = field(default_factory=list)
    cumulative_ocf_series: list[Decimal] = field(default_factory=list)
    segment_revenues: list[Decimal] = field(default_factory=list)
    extraction_pass1: dict[str, Decimal] = field(default_factory=dict)
    extraction_pass2: dict[str, Decimal] = field(default_factory=dict)
    filing_age_days: int | None = None


@dataclass(frozen=True)
class ValidationReport:
    results: tuple[ValidationResult, ...]
    data_status: DataStatus
    status_reason: str


def run_validators(inp: ValidationInputs) -> list[ValidationResult]:
    fin = inp.financials
    results = [
        v1_balance_sheet_identity(fin),
        v2_income_statement_chain(fin),
        v3_cash_flow_tie_out(fin),
        v4_borrowings_component_sum(fin, inp.total_borrowings_disclosed),
        v5_comparative_continuity(inp.current_comparatives, inp.stored_prior),
        v6_cumulative_monotonicity(inp.cumulative_revenue_series, "cumulative revenue"),
        v7_magnitude_plausibility(fin, inp.market_cap, inp.close_price, inp.shares_out),
        v8_segment_reconciliation(inp.segment_revenues, fin.total_revenue),
        v9_dual_extraction_agreement(inp.extraction_pass1, inp.extraction_pass2, CRITICAL_KEYS),
    ]
    if inp.cumulative_ocf_series:
        results.append(v6_cumulative_monotonicity(inp.cumulative_ocf_series, "cumulative OCF"))
    return results


def staleness_insufficient(filing_age_days: int | None, cfg: Thresholds) -> bool:
    """V10 (operational): a filing older than the staleness window is stale.

    Pure — the caller computes ``filing_age_days`` at the boundary (no clock
    read here).
    """
    if filing_age_days is None:
        return False
    return filing_age_days > cfg.staleness_days


def derive_status(results: list[ValidationResult], *, stale: bool) -> tuple[DataStatus, str]:
    """Combine validator outcomes into ``data_status`` (§6 outcome rules)."""
    if stale:
        return DataStatus.INSUFFICIENT, "filing older than staleness window (V10)"

    critical_failures = [
        r for r in results
        if r.is_critical and r.applicable and not r.passed and r.code != "V9"
    ]
    if critical_failures:
        codes = ", ".join(r.code for r in critical_failures)
        return DataStatus.INSUFFICIENT, f"critical validation failed: {codes}"

    v9 = next((r for r in results if r.code == "V9"), None)
    if v9 is not None and v9.applicable and not v9.passed:
        return DataStatus.CONFLICT, "dual-extraction disagreement on a critical item (V9)"

    warnings = [r for r in results if r.warning]
    if warnings:
        codes = ", ".join(r.code for r in warnings)
        return DataStatus.VALIDATED, f"validated with warnings: {codes}"
    return DataStatus.VALIDATED, "all validators passed"


def validate(inp: ValidationInputs, cfg: Thresholds) -> ValidationReport:
    results = run_validators(inp)
    stale = staleness_insufficient(inp.filing_age_days, cfg)
    status, reason = derive_status(results, stale=stale)
    return ValidationReport(tuple(results), status, reason)


# ----------------------------------------------------------------------
# Type-level gate: scoring only accepts a ValidatedFiling (BUILD_SPEC P3)
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class ValidatedFiling:
    """A filing certified VALIDATED. Construct ONLY via :func:`certify`.

    The scoring orchestration accepts this type, so an INSUFFICIENT/CONFLICT
    filing cannot reach scoring without first passing through ``certify`` —
    which returns ``None`` for any non-VALIDATED status.
    """

    financials: Financials
    _certified: bool = field(default=False, repr=False)


def certify(report: ValidationReport, financials: Financials) -> ValidatedFiling | None:
    """Return a :class:`ValidatedFiling` iff the report is VALIDATED, else None."""
    if report.data_status is DataStatus.VALIDATED:
        return ValidatedFiling(financials=financials, _certified=True)
    return None
