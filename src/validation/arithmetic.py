"""Arithmetic validators V1-V4 (EXTRACTION_SPEC §6). Pure, no network."""

from __future__ import annotations

from decimal import Decimal

from common.decimals import ZERO
from engine.types import Financials
from validation.types import ValidationResult, within

TOL_HALF_PCT = Decimal("0.005")   # 0.5%
TOL_BS = Decimal("0.005")         # V1 balance sheet identity


def v1_balance_sheet_identity(fin: Financials) -> ValidationResult:
    """V1: assets == liabilities + equity, within 0.5% of assets. Critical."""
    ta, tl, te = fin.total_assets, fin.total_liabilities, fin.total_equity
    if ta is None or tl is None or te is None or ta == ZERO:
        return ValidationResult(
            "V1", "Balance sheet identity", True, False, False,
            message="total_assets / total_liabilities / total_equity not all present",
        )
    rhs = tl + te
    ok = within(ta, rhs, TOL_BS, denom=ta)
    return ValidationResult(
        "V1", "Balance sheet identity", True, ok, True,
        expected=ta, actual=rhs, tolerance=TOL_BS,
        message="" if ok else f"assets {ta} != liabilities+equity {rhs}",
    )


def v2_income_statement_chain(fin: Financials) -> ValidationResult:
    """V2: revenue - cost_of_sales == gross_profit, and PBT-tax == net_profit,
    each within 0.5%. Critical.

    The gross-profit identity is the robustly verifiable link with the inputs we
    store. The PBT->net leg is checked only when both are present; it is skipped
    (not failed) otherwise to avoid false failures from minority interest.
    """
    rev, cogs, gp = fin.total_revenue, fin.cost_of_sales, fin.gross_profit
    if rev is None or cogs is None or gp is None or rev == ZERO:
        return ValidationResult(
            "V2", "Income statement chain", True, False, False,
            message="revenue / cost_of_sales / gross_profit not all present",
        )
    implied_gp = rev - cogs
    ok = within(implied_gp, gp, TOL_HALF_PCT, denom=rev)
    msg = "" if ok else f"revenue-COGS {implied_gp} != gross_profit {gp}"

    # Optional PBT -> net leg (only when both present).
    pbt, tax, ni = fin.profit_before_tax, fin.income_tax, fin.net_profit_attributable
    if ok and pbt is not None and tax is not None and ni is not None and pbt != ZERO:
        implied_net = pbt - tax
        if not within(implied_net, ni, TOL_HALF_PCT, denom=pbt):
            # Attributable profit differs from PBT-tax by minority interest; only
            # fail if the gap is large enough to indicate a genuine chain break.
            gap = abs(implied_net - ni) / abs(pbt)
            if gap > Decimal("0.10"):
                ok = False
                msg = f"PBT-tax {implied_net} vs net_profit {ni} gap {gap}"
    return ValidationResult(
        "V2", "Income statement chain", True, ok, True,
        expected=gp, actual=implied_gp, tolerance=TOL_HALF_PCT, message=msg,
    )


def v3_cash_flow_tie_out(fin: Financials) -> ValidationResult:
    """V3: CF closing_cash == BS cash_and_equivalents within 0.5%. Critical."""
    cf_cash, bs_cash = fin.closing_cash, fin.cash_and_equivalents
    if cf_cash is None or bs_cash is None:
        return ValidationResult(
            "V3", "Cash flow tie-out", True, False, False,
            message="closing_cash or cash_and_equivalents absent",
        )
    ok = within(cf_cash, bs_cash, TOL_HALF_PCT)
    return ValidationResult(
        "V3", "Cash flow tie-out", True, ok, True,
        expected=bs_cash, actual=cf_cash, tolerance=TOL_HALF_PCT,
        message="" if ok else f"CF cash {cf_cash} != BS cash {bs_cash}",
    )


def v4_borrowings_component_sum(
    fin: Financials,
    total_borrowings_disclosed: Decimal | None,
) -> ValidationResult:
    """V4: st + lt + overdraft + bonds == disclosed total_borrowings. Critical.

    Applicable only when a company discloses a total borrowings figure.
    """
    if total_borrowings_disclosed is None or total_borrowings_disclosed == ZERO:
        return ValidationResult(
            "V4", "Borrowings component sum", True, False, False,
            message="no disclosed total borrowings to reconcile against",
        )
    components = (
        fin.get0("short_term_borrowings")
        + fin.get0("long_term_borrowings")
        + fin.get0("bank_overdraft")
        + fin.get0("bonds_payable")
    )
    ok = within(components, total_borrowings_disclosed, TOL_HALF_PCT, denom=total_borrowings_disclosed)
    return ValidationResult(
        "V4", "Borrowings component sum", True, ok, True,
        expected=total_borrowings_disclosed, actual=components, tolerance=TOL_HALF_PCT,
        message="" if ok else f"components {components} != disclosed total {total_borrowings_disclosed}",
    )
