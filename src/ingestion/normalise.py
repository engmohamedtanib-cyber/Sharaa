"""Normalisation of raw extracted strings (EXTRACTION_SPEC §5).

Deterministic and pure — no LLM, no I/O. This module converts a printed figure
into an exact :class:`~decimal.Decimal` in EGP units, or **refuses**.

Refusal is the point. Rule R3 forbids estimating a financial figure, so a
genuinely undecidable string raises :class:`AmbiguousNumberError` rather than
guessing. The caller turns that into ``DATA_INSUFFICIENT``.

Three known-frequent failure modes are handled explicitly:
  1. Arabic-Indic digits parsing as nothing (or partially) — §5.1
  2. Unit scale (thousands / millions) misread — §5.2, the highest-frequency
     error, and one V1 cannot catch because the balance sheet stays consistent
  3. Sign conventions: ``(1,234)``, ``1,234-``, Arabic brackets — §5.3
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation
from enum import StrEnum

# ----------------------------------------------------------------------
# §5.1 Digit systems
# ----------------------------------------------------------------------
# Arabic-Indic (U+0660..0669) and Eastern Arabic-Indic / Persian (U+06F0..06F9).
_ARABIC_INDIC = "٠١٢٣٤٥٦٧٨٩"
_EASTERN_INDIC = "۰۱۲۳۴۵۶۷۸۹"
_ASCII_DIGITS = "0123456789"

_DIGIT_MAP = str.maketrans(
    _ARABIC_INDIC + _EASTERN_INDIC,
    _ASCII_DIGITS + _ASCII_DIGITS,
)

# Arabic decimal separator (U+066B) and thousands separator (U+066C).
_ARABIC_DECIMAL_SEP = "٫"
_ARABIC_THOUSANDS_SEP = "٬"

# Characters that legitimately appear around a printed figure and carry no value.
_STRIP_CHARS = "‏‎‪‫‬    \t\n\r"

# Bracket styles used for negatives, including full-width / Arabic-typeset forms.
_OPEN_BRACKETS = "(（[【"
_CLOSE_BRACKETS = ")）]】"


class UnitScale(StrEnum):
    """Mirrors the ``unit_scale`` enum in ``db/schema.sql``."""

    UNITS = "UNITS"
    THOUSANDS = "THOUSANDS"
    MILLIONS = "MILLIONS"


_SCALE_MULTIPLIER: dict[UnitScale, Decimal] = {
    UnitScale.UNITS: Decimal(1),
    UnitScale.THOUSANDS: Decimal(1000),
    UnitScale.MILLIONS: Decimal(1000000),
}


class NormalisationError(ValueError):
    """Base class for refusals in this module."""


class AmbiguousNumberError(NormalisationError):
    """The string could mean more than one value. Never guessed (R3)."""


class UnparseableNumberError(NormalisationError):
    """The string is not a number at all."""


class ScaleUndeterminedError(NormalisationError):
    """The statement header does not state a unit scale unambiguously."""


# ----------------------------------------------------------------------
# §5.1 digit normalisation
# ----------------------------------------------------------------------
def normalise_digits(text: str) -> str:
    """Convert Arabic-Indic and Eastern Arabic-Indic digits to ASCII.

    Applied before ANY parsing. Missing this produces silent parse failures or,
    worse, partial numbers (§5.1).
    """
    return text.translate(_DIGIT_MAP)


# ----------------------------------------------------------------------
# §5.3 / §5.4 number parsing
# ----------------------------------------------------------------------
def parse_number(raw: str, *, decimal_places: int | None = None) -> Decimal:
    """Parse a printed figure into an exact :class:`Decimal`.

    Handles Arabic-Indic digits, bracketed / trailing negatives, and both
    ``,`` and ``.`` used as either thousands or decimal separators.

    Separator disambiguation (§5.4), applied in order:
      * both ``,`` and ``.`` present -> the **rightmost** is the decimal point
      * one separator kind, appearing more than once -> thousands grouping
      * one separator appearing once:
          - exactly 3 digits after it, and no ``decimal_places`` hint
            contradicting that -> thousands grouping (``1,234`` = 1234)
          - otherwise -> decimal point
      * a ``decimal_places`` hint (from the statement's stated precision)
        always wins over the 3-digit heuristic

    Raises :class:`AmbiguousNumberError` or :class:`UnparseableNumberError`
    rather than returning a guess.
    """
    if raw is None:
        raise UnparseableNumberError("cannot parse None")

    s = unicodedata.normalize("NFKC", raw)
    s = normalise_digits(s)
    s = s.replace(_ARABIC_THOUSANDS_SEP, ",").replace(_ARABIC_DECIMAL_SEP, ".")
    for ch in _STRIP_CHARS:
        s = s.replace(ch, "")

    if not s:
        raise UnparseableNumberError(f"empty value: {raw!r}")

    negative = False

    # Bracketed negative: (1,234) / （1,234） / [1,234]
    if s[0] in _OPEN_BRACKETS and s[-1] in _CLOSE_BRACKETS:
        negative = True
        s = s[1:-1]
    elif s[0] in _OPEN_BRACKETS or s[-1] in _CLOSE_BRACKETS:
        raise UnparseableNumberError(f"unbalanced brackets: {raw!r}")

    # Leading or trailing minus (both occur in Egyptian statements).
    s = s.replace("−", "-")  # UNICODE MINUS SIGN
    if s.endswith("-"):
        negative = not negative
        s = s[:-1]
    if s.startswith("-"):
        negative = not negative
        s = s[1:]
    if s.startswith("+"):
        s = s[1:]

    # Currency markers and stray letters are not part of the figure.
    s = s.replace("ج.م", "").replace("EGP", "").replace("egp", "").strip()

    if not s:
        raise UnparseableNumberError(f"no digits in value: {raw!r}")
    if not re.fullmatch(r"[0-9.,]+", s):
        raise UnparseableNumberError(f"unexpected characters in value: {raw!r}")

    digits = _resolve_separators(s, raw, decimal_places)

    try:
        value = Decimal(digits)
    except InvalidOperation as exc:  # pragma: no cover - guarded by the regex above
        raise UnparseableNumberError(f"cannot parse {raw!r}") from exc

    return -value if negative else value


def _resolve_separators(s: str, raw: str, decimal_places: int | None) -> str:
    """Return ``s`` with grouping removed and a single ``.`` decimal point."""
    has_comma = "," in s
    has_dot = "." in s

    if not has_comma and not has_dot:
        return s

    if has_comma and has_dot:
        # The rightmost separator is the decimal point; the other one groups.
        if s.rfind(",") > s.rfind("."):
            return _group_then_decimal(s, ",", ".")
        return _group_then_decimal(s, ".", ",")

    sep = "," if has_comma else "."
    count = s.count(sep)
    tail = s.rsplit(sep, 1)[1]

    if count > 1:
        # Repeated single separator kind is grouping: 1.234.567 or 1,234,567.
        if not _valid_grouping(s, sep):
            raise AmbiguousNumberError(f"irregular digit grouping: {raw!r}")
        return s.replace(sep, "")

    # Exactly one separator.
    if decimal_places is not None:
        return s.replace(sep, ".") if len(tail) == decimal_places else s.replace(sep, "")
    if len(tail) == 3 and _valid_grouping(s, sep):
        # 1,234 / 1.234 -> thousands grouping, the EGX convention.
        return s.replace(sep, "")
    return s.replace(sep, ".")


def _group_then_decimal(s: str, decimal_sep: str, group_sep: str) -> str:
    """Remove ``group_sep`` and normalise ``decimal_sep`` to ``.``."""
    integer_part, _, fraction = s.rpartition(decimal_sep)
    integer_part = integer_part.replace(group_sep, "").replace(decimal_sep, "")
    return f"{integer_part}.{fraction}"


def _valid_grouping(s: str, sep: str) -> bool:
    """True if ``sep`` groups digits in canonical 3s (leading group 1-3).

    A leading zero in the first group rules grouping out: ``0.005`` is five
    thousandths, never ``0 005``. Missing this reads every sub-unit ratio as a
    number 1000x too large.
    """
    parts = s.split(sep)
    if len(parts) < 2:
        return False
    if not parts[0] or len(parts[0]) > 3:
        return False
    if parts[0].startswith("0"):
        return False
    return all(len(p) == 3 for p in parts[1:])


# ----------------------------------------------------------------------
# §5.2 unit scale
# ----------------------------------------------------------------------
# Matched against the normalised statement header. Ordered most-specific first
# so that "ألف" inside a longer phrase cannot shadow "مليون".
_SCALE_PATTERNS: tuple[tuple[UnitScale, tuple[str, ...]], ...] = (
    (
        UnitScale.MILLIONS,
        ("بالمليون", "بالملايين", "مليون جنيه", "ملايين جنيه", "in millions", "egp millions", "millions of egyptian"),
    ),
    (
        UnitScale.THOUSANDS,
        ("بالألف", "بالالف", "بآلاف", "بالآلاف", "الف جنيه", "ألف جنيه",
         "in thousands", "egp thousands", "thousands of egyptian", "'000"),
    ),
    (
        UnitScale.UNITS,
        ("بالجنيه المصري", "بالجنيه", "in egyptian pounds", "in egp", "egp units"),
    ),
)


def detect_scale(header_text: str | None) -> UnitScale:
    """Detect the declared unit scale from a statement header (§5.2).

    Raises :class:`ScaleUndeterminedError` when the header states nothing
    recognisable. Defaulting to UNITS here would silently produce a 1000x
    error on every ratio, so the caller must treat this as DATA_INSUFFICIENT.
    """
    if not header_text or not header_text.strip():
        raise ScaleUndeterminedError("statement header is empty; unit scale not declared")

    text = unicodedata.normalize("NFKC", header_text).lower()
    text = normalise_digits(text)

    for scale, needles in _SCALE_PATTERNS:
        if any(needle.lower() in text for needle in needles):
            return scale

    raise ScaleUndeterminedError(f"no unit scale declared in header: {header_text!r}")


def apply_scale(value: Decimal, scale: UnitScale) -> Decimal:
    """Normalise a reported figure to EGP units (§5.2)."""
    return value * _SCALE_MULTIPLIER[scale]


def normalise_value(
    raw: str,
    scale: UnitScale,
    *,
    decimal_places: int | None = None,
) -> Decimal:
    """Parse ``raw`` and scale it to EGP units in one deterministic step."""
    return apply_scale(parse_number(raw, decimal_places=decimal_places), scale)


# ----------------------------------------------------------------------
# §3.2 financing classification
# ----------------------------------------------------------------------
class FinancingClass(StrEnum):
    CONVENTIONAL = "CONVENTIONAL"
    ISLAMIC = "ISLAMIC"
    AMBIGUOUS = "AMBIGUOUS"


def classify_financing(
    caption: str,
    conventional_captions: list[str],
    islamic_captions: list[str],
) -> FinancingClass:
    """Classify a borrowing caption as conventional or Islamic (§3.2).

    Islamic financing is EXCLUDED from Screen C's numerator, so this decides a
    compliance-relevant number. A caption matching both families, or neither,
    returns ``AMBIGUOUS`` — the caller must mark the filing DATA_INSUFFICIENT
    rather than defaulting either way (§3.2 ambiguity rule).
    """
    text = unicodedata.normalize("NFKC", caption).strip().lower()
    is_islamic = any(unicodedata.normalize("NFKC", c).lower() in text for c in islamic_captions)
    is_conventional = any(unicodedata.normalize("NFKC", c).lower() in text for c in conventional_captions)

    if is_islamic and not is_conventional:
        return FinancingClass.ISLAMIC
    if is_conventional and not is_islamic:
        return FinancingClass.CONVENTIONAL
    return FinancingClass.AMBIGUOUS
