"""Normalisation: digits, separators, signs, scale (EXTRACTION_SPEC §5)."""

from __future__ import annotations

import pytest

from conftest import D
from ingestion.normalise import (
    AmbiguousNumberError,
    FinancingClass,
    ScaleUndeterminedError,
    UnitScale,
    UnparseableNumberError,
    apply_scale,
    classify_financing,
    detect_scale,
    normalise_digits,
    normalise_value,
    parse_number,
)


# ---- §5.1 Arabic-Indic digits ---------------------------------------
def test_arabic_indic_digits_converted():
    assert normalise_digits("١٢٣٤٥٦٧٨٩٠") == "1234567890"


def test_eastern_arabic_indic_digits_converted():
    assert normalise_digits("۰۱۲۳۴۵۶۷۸۹") == "0123456789"


def test_mixed_script_digits():
    assert normalise_digits("١٢٣abc456") == "123abc456"


def test_parse_arabic_number():
    assert parse_number("١٢٣٤٥") == D("12345")


def test_parse_arabic_with_arabic_thousands_sep():
    assert parse_number("١٬٢٣٤") == D("1234")


def test_parse_arabic_decimal_sep():
    assert parse_number("١٢٫٥") == D("12.5")


# ---- §5.4 separators -------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1,234,567", "1234567"),
        ("1.234.567", "1234567"),
        ("1,234.56", "1234.56"),
        ("1.234,56", "1234.56"),
        ("1,234", "1234"),      # 3-digit tail -> thousands (EGX convention)
        ("12.5", "12.5"),       # non-3 tail -> decimal
        ("1234", "1234"),
        ("0.005", "0.005"),
    ],
)
def test_separator_disambiguation(raw, expected):
    assert parse_number(raw) == D(expected)


def test_decimal_places_hint_overrides_heuristic():
    # With a stated precision of 3, "1,234" is 1.234 not 1234.
    assert parse_number("1,234", decimal_places=3) == D("1.234")


def test_decimal_places_hint_confirms_thousands():
    assert parse_number("1,234", decimal_places=2) == D("1234")


def test_irregular_grouping_is_ambiguous():
    with pytest.raises(AmbiguousNumberError):
        parse_number("1,23,45")


# ---- §5.3 sign conventions ------------------------------------------
@pytest.mark.parametrize("raw", ["(1,234)", "-1,234", "1,234-", "（1,234）", "[1,234]"])
def test_negative_forms(raw):
    assert parse_number(raw) == D("-1234")


def test_arabic_bracketed_negative():
    assert parse_number("(١٢٣٤)") == D("-1234")


def test_unicode_minus():
    assert parse_number("−1234") == D("-1234")


def test_unbalanced_bracket_rejected():
    with pytest.raises(UnparseableNumberError):
        parse_number("(1,234")


def test_double_negative_is_positive():
    # "(-1234)" -> bracket negates, leading minus negates again
    assert parse_number("(-1234)") == D("1234")


# ---- refusals (R3: never guess) -------------------------------------
@pytest.mark.parametrize("raw", ["", "   ", "abc", "12abc34", "-", "()"])
def test_unparseable_refused(raw):
    with pytest.raises(UnparseableNumberError):
        parse_number(raw)


def test_none_refused():
    with pytest.raises(UnparseableNumberError):
        parse_number(None)  # type: ignore[arg-type]


def test_currency_marker_stripped():
    assert parse_number("1,234 ج.م") == D("1234")


# ---- §5.2 unit scale -------------------------------------------------
@pytest.mark.parametrize(
    "header,expected",
    [
        ("بالألف جنيه مصري", UnitScale.THOUSANDS),
        ("بالالف", UnitScale.THOUSANDS),
        ("In thousands of Egyptian Pounds", UnitScale.THOUSANDS),
        ("EGP '000", UnitScale.THOUSANDS),
        ("بالمليون", UnitScale.MILLIONS),
        ("بالملايين", UnitScale.MILLIONS),
        ("In millions", UnitScale.MILLIONS),
        ("بالجنيه المصري", UnitScale.UNITS),
        ("In Egyptian Pounds", UnitScale.UNITS),
    ],
)
def test_detect_scale(header, expected):
    assert detect_scale(header) is expected


def test_millions_not_shadowed_by_thousands_substring():
    assert detect_scale("القوائم المالية بالمليون جنيه مصري") is UnitScale.MILLIONS


def test_undeclared_scale_refused_not_defaulted():
    # Defaulting to UNITS would silently produce a 1000x error on every ratio.
    with pytest.raises(ScaleUndeterminedError):
        detect_scale("Consolidated Statement of Financial Position")


def test_empty_header_refused():
    with pytest.raises(ScaleUndeterminedError):
        detect_scale("")
    with pytest.raises(ScaleUndeterminedError):
        detect_scale(None)


@pytest.mark.parametrize(
    "scale,expected",
    [(UnitScale.UNITS, "1234"), (UnitScale.THOUSANDS, "1234000"), (UnitScale.MILLIONS, "1234000000")],
)
def test_apply_scale(scale, expected):
    assert apply_scale(D("1234"), scale) == D(expected)


def test_normalise_value_end_to_end():
    assert normalise_value("(١٬٢٣٤)", UnitScale.THOUSANDS) == D("-1234000")


# ---- §3.2 financing classification ----------------------------------
CONV = ["bank loans", "overdraft", "قروض بنكية"]
ISLAMIC = ["murabaha", "sukuk", "مرابحة"]


def test_classify_islamic():
    assert classify_financing("مرابحة طويلة الأجل", CONV, ISLAMIC) is FinancingClass.ISLAMIC
    assert classify_financing("Sukuk issued", CONV, ISLAMIC) is FinancingClass.ISLAMIC


def test_classify_conventional():
    assert classify_financing("قروض بنكية طويلة الأجل", CONV, ISLAMIC) is FinancingClass.CONVENTIONAL
    assert classify_financing("Bank loans", CONV, ISLAMIC) is FinancingClass.CONVENTIONAL


def test_classify_ambiguous_when_neither():
    assert classify_financing("Other financing", CONV, ISLAMIC) is FinancingClass.AMBIGUOUS


def test_classify_ambiguous_when_both():
    # Never default either way (§3.2 ambiguity rule).
    assert classify_financing("Murabaha bank loans", CONV, ISLAMIC) is FinancingClass.AMBIGUOUS
