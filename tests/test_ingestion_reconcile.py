"""Dual-pass reconciliation and the §3.2/§3.3 document-shape gates."""

from __future__ import annotations

from conftest import D
from ingestion.extract import ExtractedItem, NormalisedPass
from ingestion.normalise import FinancingClass
from ingestion.reconcile import (
    check_interest_income_traceability,
    impure_income_components,
    reconcile,
    to_financials,
    unclassified_borrowings,
)

CRITICAL = ["total_assets", "total_revenue", "interest_income"]


def _item(key: str, page: int = 4, note: str | None = None) -> ExtractedItem:
    return ExtractedItem(
        key=key, raw_caption="caption", raw_value="1", page_no=page,
        statement="BALANCE_SHEET", note_ref=note,
    )


def _pass(values: dict, notes: dict | None = None, unparseable: dict | None = None) -> NormalisedPass:
    notes = notes or {}
    return NormalisedPass(
        values=values,
        provenance={k: _item(k, note=notes.get(k)) for k in values},
        unparseable=unparseable or {},
    )


# ---- agreement -------------------------------------------------------
def test_agreeing_passes_reconcile():
    p1 = _pass({"total_assets": D("1000")})
    p2 = _pass({"total_assets": D("1000")})
    r = reconcile(p1, p2, CRITICAL)
    assert r.items["total_assets"].value == D("1000")
    assert r.items["total_assets"].agreement is True
    assert r.has_critical_conflict is False


def test_within_tolerance_agrees():
    p1 = _pass({"total_assets": D("1000")})
    p2 = _pass({"total_assets": D("1000.5")})  # 0.05% < 0.1%
    assert reconcile(p1, p2, CRITICAL).has_critical_conflict is False


def test_critical_disagreement_withholds_value():
    p1 = _pass({"total_assets": D("1000")})
    p2 = _pass({"total_assets": D("1100")})
    r = reconcile(p1, p2, CRITICAL)
    assert "total_assets" in r.conflicts
    assert "total_assets" not in r.items  # value withheld, not averaged
    assert r.has_critical_conflict is True


def test_critical_one_sided_is_conflict():
    r = reconcile(_pass({"total_assets": D("1000")}), _pass({}), CRITICAL)
    assert "total_assets" in r.conflicts
    assert "total_assets" not in r.items


def test_non_critical_one_sided_is_kept_flagged():
    r = reconcile(_pass({"inventory": D("50")}), _pass({}), CRITICAL)
    assert r.items["inventory"].value == D("50")
    assert r.items["inventory"].agreement is False
    assert r.has_critical_conflict is False


def test_non_critical_disagreement_keeps_pass1():
    r = reconcile(_pass({"inventory": D("50")}), _pass({"inventory": D("60")}), CRITICAL)
    assert r.items["inventory"].value == D("50")
    assert r.items["inventory"].agreement is False


def test_provenance_carried_through():
    r = reconcile(_pass({"total_assets": D("1000")}), _pass({"total_assets": D("1000")}), CRITICAL)
    assert r.items["total_assets"].page_no == 4


def test_unparseable_surfaced():
    p1 = _pass({}, unparseable={"total_revenue": "'n/a': unparseable"})
    r = reconcile(p1, _pass({}), CRITICAL)
    assert "total_revenue" in r.unparseable


def test_zero_values_agree():
    r = reconcile(_pass({"bonds_payable": D("0")}), _pass({"bonds_payable": D("0")}), CRITICAL)
    assert r.items["bonds_payable"].value == D("0")


# ---- §3.3 interest income traceability ------------------------------
def test_immaterial_other_income_needs_no_note():
    values = {"total_revenue": D("1000"), "other_income": D("10")}  # 1%
    r = reconcile(_pass(values), _pass(values), CRITICAL)
    check = check_interest_income_traceability(values, r.items)
    assert check.material is False
    assert check.sufficient is True


def test_material_other_income_without_note_is_insufficient():
    values = {"total_revenue": D("1000"), "other_income": D("50")}  # 5% > 2%
    r = reconcile(_pass(values), _pass(values), CRITICAL)
    check = check_interest_income_traceability(values, r.items)
    assert check.material is True
    assert check.note_located is False
    assert check.sufficient is False


def test_material_other_income_with_note_is_sufficient():
    values = {"total_revenue": D("1000"), "other_income": D("50")}
    notes = {"other_income": "18"}
    r = reconcile(_pass(values, notes), _pass(values, notes), CRITICAL)
    check = check_interest_income_traceability(values, r.items)
    assert check.material is True
    assert check.note_located is True
    assert check.sufficient is True


def test_no_other_income_is_sufficient():
    values = {"total_revenue": D("1000")}
    r = reconcile(_pass(values), _pass(values), CRITICAL)
    assert check_interest_income_traceability(values, r.items).sufficient is True


def test_missing_revenue_cannot_assess():
    assert check_interest_income_traceability({}, {}).sufficient is False


def test_negative_other_income_uses_absolute_materiality():
    values = {"total_revenue": D("1000"), "other_income": D("-50")}
    r = reconcile(_pass(values), _pass(values), CRITICAL)
    assert check_interest_income_traceability(values, r.items).material is True


# ---- §3.2 financing classification gate -----------------------------
def test_unclassified_borrowings_listed():
    classes = {
        "قروض بنكية": FinancingClass.CONVENTIONAL,
        "مرابحة": FinancingClass.ISLAMIC,
        "Other financing": FinancingClass.AMBIGUOUS,
    }
    assert unclassified_borrowings(classes) == ["Other financing"]


def test_no_unclassified_borrowings():
    assert unclassified_borrowings({"مرابحة": FinancingClass.ISLAMIC}) == []


# ---- mapping to engine input ----------------------------------------
def test_to_financials_maps_known_keys():
    fin = to_financials({"total_assets": D("1000"), "total_revenue": D("500")})
    assert fin.total_assets == D("1000")
    assert fin.total_revenue == D("500")


def test_to_financials_ignores_unknown_keys():
    fin = to_financials({"total_assets": D("1000"), "not_a_field": D("1")})
    assert fin.total_assets == D("1000")


def test_to_financials_leaves_absent_as_none_not_zero():
    fin = to_financials({"total_assets": D("1000")})
    assert fin.total_revenue is None  # explicitly absent, never a silent zero


def test_impure_income_components():
    values = {"interest_income": D("10"), "other_non_permissible_income": D("5"), "total_revenue": D("100")}
    assert impure_income_components(values) == {
        "interest_income": D("10"),
        "other_non_permissible_income": D("5"),
    }
