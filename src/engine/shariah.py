"""Shariah gate — Screens A-E, status classification, breach typing.

The gate is evaluated FIRST and is a hard filter (``CLAUDE.md`` R7): a company
failing it is never scored, never ranked, never in a portfolio. ``worst``,
never ``average``, across screens (``ENGINE_SPEC`` §2.7). Every function here
is pure.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from decimal import Decimal

from common.decimals import ZERO, pct_delta, safe_div
from engine.config import Thresholds
from engine.types import (
    BreachType,
    DataStatus,
    Financials,
    ShariahStatus,
    status_severity,
    worst_status,
)

_INTEREST_BEARING_DEBT_PARTS = (
    "short_term_borrowings",
    "long_term_borrowings",
    "bank_overdraft",
    "bonds_payable",
    "lease_liabilities_finance",
)
_INTEREST_BEARING_ASSET_PARTS = (
    "cash_and_equivalents",
    "time_deposits",
    "treasury_bills_and_bonds",
    "conventional_money_market",
)


@dataclass(frozen=True)
class ScreenResult:
    code: str
    name: str
    numerator: Decimal
    denominator: Decimal | None
    ratio: Decimal | None
    threshold: Decimal
    utilisation: Decimal | None
    status: ShariahStatus
    ratio_total_assets: Decimal | None = None  # secondary basis (§2.6), C & D


@dataclass(frozen=True)
class GateResult:
    screens: tuple[ScreenResult, ...]
    overall_status: ShariahStatus
    worst_screen_code: str | None
    worst_utilisation: Decimal | None
    passed: bool                 # overall_status in {GREEN, AMBER}
    activity_hard_fail: bool

    def screen(self, code: str) -> ScreenResult:
        for s in self.screens:
            if s.code == code:
                return s
        raise KeyError(code)


@dataclass(frozen=True)
class BreachResult:
    breach: BreachType
    worst_breaching_code: str | None
    numerator_delta_pct: Decimal | None
    denominator_delta_pct: Decimal | None


# ======================================================================
# Core-activity override (§2.1)
# ======================================================================
def _normalise(text: str) -> str:
    return unicodedata.normalize("NFKC", text).strip().lower()


def is_core_activity_prohibited(
    sector: str,
    sub_sector: str | None,
    prohibited_sectors: list[str],
) -> bool:
    """True if the company's primary classification is itself prohibited.

    Checked BEFORE any ratio (the 5% tolerance is only for incidental revenue
    in an otherwise permissible business). Case-insensitive substring match.
    """
    haystacks = [_normalise(sector)]
    if sub_sector:
        haystacks.append(_normalise(sub_sector))
    needles = [_normalise(p) for p in prohibited_sectors]
    return any(needle in hay for hay in haystacks for needle in needles)


# ======================================================================
# Status classification (§2.7)
# ======================================================================
def classify_status(utilisation: Decimal | None, cfg: Thresholds) -> ShariahStatus:
    if utilisation is None:
        return ShariahStatus.DATA_INSUFFICIENT
    bands = cfg.status_bands()
    if utilisation <= bands["GREEN"]:
        return ShariahStatus.GREEN
    if utilisation <= bands["AMBER"]:
        return ShariahStatus.AMBER
    if utilisation <= bands["ORANGE"]:
        return ShariahStatus.ORANGE
    return ShariahStatus.RED


def _screen(
    code: str,
    name: str,
    numerator: Decimal,
    denominator: Decimal | None,
    threshold: Decimal,
    cfg: Thresholds,
    *,
    ratio_total_assets: Decimal | None = None,
    force_red: bool = False,
) -> ScreenResult:
    ratio = safe_div(numerator, denominator) if denominator is not None else None
    utilisation = safe_div(ratio, threshold) if ratio is not None else None
    status = ShariahStatus.RED if force_red else classify_status(utilisation, cfg)
    return ScreenResult(
        code=code,
        name=name,
        numerator=numerator,
        denominator=denominator,
        ratio=ratio,
        threshold=threshold,
        utilisation=utilisation,
        status=status,
        ratio_total_assets=ratio_total_assets,
    )


# ======================================================================
# Screens A-E (§2.1 - §2.5)
# ======================================================================
def screen_a(fin: Financials, cfg: Thresholds, *, core_prohibited: bool) -> ScreenResult:
    """Business activity: non_permissible_revenue / total_revenue."""
    threshold = cfg.screen_threshold("A")
    name = cfg.screen_name("A")
    numerator = fin.get0("non_permissible_revenue")
    denominator = fin.total_revenue if fin.total_revenue not in (None, ZERO) else None
    return _screen("A", name, numerator, denominator, threshold, cfg, force_red=core_prohibited)


def screen_b(fin: Financials, cfg: Thresholds) -> ScreenResult:
    """Impure income / total_revenue (denominator includes other income)."""
    threshold = cfg.screen_threshold("B")
    name = cfg.screen_name("B")
    impure = (
        fin.get0("interest_income")
        + fin.get0("income_from_conventional_investments")
        + fin.get0("other_non_permissible_income")
    )
    denominator = fin.total_revenue if fin.total_revenue not in (None, ZERO) else None
    return _screen("B", name, impure, denominator, threshold, cfg)


def interest_bearing_debt(fin: Financials) -> Decimal:
    """Screen C numerator: conventional debt only; Islamic financing excluded."""
    total = sum((fin.get0(p) for p in _INTEREST_BEARING_DEBT_PARTS), ZERO)
    return total - fin.get0("islamic_financing")


def interest_bearing_assets(fin: Financials) -> Decimal:
    """Screen D numerator: sukuk / Islamic accounts / gold excluded upstream."""
    return sum((fin.get0(p) for p in _INTEREST_BEARING_ASSET_PARTS), ZERO)


def screen_c(fin: Financials, denominator: Decimal | None, cfg: Thresholds) -> ScreenResult:
    threshold = cfg.screen_threshold("C")
    name = cfg.screen_name("C")
    num = interest_bearing_debt(fin)
    denom = denominator if denominator not in (None, ZERO) else None
    ta = fin.total_assets if fin.total_assets not in (None, ZERO) else None
    ratio_ta = safe_div(num, ta) if ta is not None else None
    return _screen("C", name, num, denom, threshold, cfg, ratio_total_assets=ratio_ta)


def screen_d(fin: Financials, denominator: Decimal | None, cfg: Thresholds) -> ScreenResult:
    threshold = cfg.screen_threshold("D")
    name = cfg.screen_name("D")
    num = interest_bearing_assets(fin)
    denom = denominator if denominator not in (None, ZERO) else None
    ta = fin.total_assets if fin.total_assets not in (None, ZERO) else None
    ratio_ta = safe_div(num, ta) if ta is not None else None
    return _screen("D", name, num, denom, threshold, cfg, ratio_total_assets=ratio_ta)


def screen_e(fin: Financials, denominator: Decimal | None, cfg: Thresholds) -> ScreenResult:
    threshold = cfg.screen_threshold("E")
    name = cfg.screen_name("E")
    num = fin.get0("accounts_receivable")
    denom = denominator if denominator not in (None, ZERO) else None
    return _screen("E", name, num, denom, threshold, cfg)


# ======================================================================
# The gate (§2.7)
# ======================================================================
def run_gate(
    fin: Financials,
    mcap_avg_12m: Decimal | None,
    cfg: Thresholds,
    *,
    core_prohibited: bool = False,
) -> GateResult:
    """Run all five screens and combine to an overall status (worst-of)."""
    screens = (
        screen_a(fin, cfg, core_prohibited=core_prohibited),
        screen_b(fin, cfg),
        screen_c(fin, mcap_avg_12m, cfg),
        screen_d(fin, mcap_avg_12m, cfg),
        screen_e(fin, mcap_avg_12m, cfg),
    )
    overall = worst_status([s.status for s in screens])

    # Worst screen = highest severity, then highest utilisation as tiebreak.
    def sort_key(s: ScreenResult) -> tuple[int, Decimal]:
        return (status_severity(s.status), s.utilisation if s.utilisation is not None else ZERO)

    worst = max(screens, key=sort_key)
    passed = overall in (ShariahStatus.GREEN, ShariahStatus.AMBER)
    return GateResult(
        screens=screens,
        overall_status=overall,
        worst_screen_code=worst.code,
        worst_utilisation=worst.utilisation,
        passed=passed,
        activity_hard_fail=core_prohibited,
    )


# ======================================================================
# Breach typing (§3)
# ======================================================================
def diagnose_breach(
    gate: GateResult,
    data_status: DataStatus,
    filing_age_days: int,
    cfg: Thresholds,
    *,
    prior_screens: dict[str, ScreenResult] | None = None,
) -> BreachResult:
    """Classify the breach type driving the current status (§3.1, §3.2)."""
    # TYPE_1 — activity. Hard fail or Screen A red.
    if gate.activity_hard_fail or gate.screen("A").status is ShariahStatus.RED:
        return BreachResult(BreachType.TYPE_1_ACTIVITY, "A", None, None)

    # TYPE_4 — stale or unvalidated data.
    if data_status is not DataStatus.VALIDATED or filing_age_days > cfg.staleness_days:
        return BreachResult(BreachType.TYPE_4_DATA, None, None, None)

    # TYPE_2/3 — a structural screen (B/C/D/E) is RED.
    breaching = [
        s for s in gate.screens if s.code != "A" and s.status is ShariahStatus.RED
    ]
    if not breaching:
        return BreachResult(BreachType.NONE, None, None, None)

    worst = max(breaching, key=lambda s: s.utilisation or ZERO)
    prior = (prior_screens or {}).get(worst.code)
    if prior is None or prior.denominator is None or worst.denominator is None:
        # Cannot diagnose the driver -> treat as structural (exit is the
        # conservative Shariah stance when the cause is unknown, §3.2).
        return BreachResult(BreachType.TYPE_2_STRUCTURAL, worst.code, None, None)

    num_delta = pct_delta(worst.numerator, prior.numerator)
    den_delta = pct_delta(worst.denominator, prior.denominator)
    if num_delta is None or den_delta is None:
        return BreachResult(BreachType.TYPE_2_STRUCTURAL, worst.code, num_delta, den_delta)

    breach = (
        BreachType.TYPE_2_STRUCTURAL
        if abs(num_delta) > abs(den_delta)
        else BreachType.TYPE_3_DENOMINATOR
    )
    return BreachResult(breach, worst.code, num_delta, den_delta)


# ======================================================================
# Cure window (§3.3)
# ======================================================================
def cure_window(
    breach: BreachType,
    review_date: str,
    next_review_date: str,
) -> tuple[str | None, str | None]:
    """(opened_at, expires_at) for a Type 3 breach; (None, None) otherwise.

    Opens at the review detecting the breach; expires at the next scheduled
    review. Finite by construction — the engine cannot extend it.
    """
    if breach is BreachType.TYPE_3_DENOMINATOR:
        return (review_date, next_review_date)
    return (None, None)


# ======================================================================
# Newly-compliant admission test (§3.4)
# ======================================================================
def admission_test(
    prev_status: ShariahStatus,
    curr_status: ShariahStatus,
    numerator_delta_pct: Decimal | None,
    denominator_delta_pct: Decimal | None,
    total_assets_delta_pct: Decimal | None,
) -> bool:
    """Admit a RED -> GREEN/AMBER company only on a *real* improvement.

    Numerator-driven (the offending figure actually fell) or denominator-driven
    via genuine total-asset growth. Compliance restored purely by a share-price
    rally is rejected (§3.4).
    """
    is_transition = prev_status is ShariahStatus.RED and curr_status in (
        ShariahStatus.GREEN,
        ShariahStatus.AMBER,
    )
    if not is_transition:
        return True  # not a re-admission event; nothing to gate here

    if numerator_delta_pct is None or denominator_delta_pct is None:
        return False  # cannot prove a real improvement -> stay out

    numerator_driven = abs(numerator_delta_pct) >= abs(denominator_delta_pct)
    if numerator_driven:
        return numerator_delta_pct < 0  # the offending numerator actually fell
    # denominator-driven: genuine only if total assets grew (not price alone)
    return total_assets_delta_pct is not None and total_assets_delta_pct > 0


# ======================================================================
# Compliance streak (§3.5)
# ======================================================================
def compliance_streak(prior_statuses_newest_first: list[ShariahStatus]) -> int:
    """Count consecutive most-recent filings with status in {GREEN, AMBER}."""
    streak = 0
    for s in prior_statuses_newest_first:
        if s in (ShariahStatus.GREEN, ShariahStatus.AMBER):
            streak += 1
        else:
            break
    return streak
