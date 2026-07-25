"""Reconcile two independent extraction passes (EXTRACTION_SPEC §2, §3).

Turns two :class:`~ingestion.extract.NormalisedPass` objects into the single set
of reconciled values the validation layer checks and the engine consumes.

Agreement is required, not assumed: a critical item on which the passes disagree
becomes a ``CONFLICT`` rather than a value. This module also encodes the two
document-shape rules that decide whether a filing is usable at all:

  * §3.3 — interest income must be traced into the notes; a material
    ``other_income`` whose note cannot be parsed makes the filing insufficient.
    At ~19% policy rates, treasury income is the most common cause of a Shariah
    breach and the item most likely to be hidden in aggregation.
  * §3.2 — a borrowing that cannot be classified as conventional or Islamic
    makes the filing insufficient; neither default is safe.

Pure module: no LLM, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from engine.types import Financials
from ingestion.extract import ExtractedItem, NormalisedPass
from ingestion.normalise import FinancingClass

#: V9 agreement tolerance for critical items (EXTRACTION_SPEC §6).
AGREEMENT_TOLERANCE = Decimal("0.001")

#: §3.3 — other income above this share of revenue is material and its note
#: must be located and parsed.
OTHER_INCOME_MATERIALITY = Decimal("0.02")


@dataclass(frozen=True)
class ReconciledItem:
    key: str
    value: Decimal
    agreement: bool
    pass_1_value: Decimal | None
    pass_2_value: Decimal | None
    page_no: int
    note_ref: str | None


@dataclass(frozen=True)
class Reconciliation:
    """Reconciled values plus every reason the filing might be unusable."""

    items: dict[str, ReconciledItem] = field(default_factory=dict)
    conflicts: list[str] = field(default_factory=list)
    one_sided: list[str] = field(default_factory=list)
    unparseable: dict[str, str] = field(default_factory=dict)

    @property
    def has_critical_conflict(self) -> bool:
        return bool(self.conflicts)

    def values(self) -> dict[str, Decimal]:
        return {k: v.value for k, v in self.items.items()}


def _rel_diff(a: Decimal, b: Decimal) -> Decimal | None:
    scale = max(abs(a), abs(b))
    if scale == 0:
        return None
    return abs(a - b) / scale


def reconcile(
    pass1: NormalisedPass,
    pass2: NormalisedPass,
    critical_keys: list[str],
    *,
    tolerance: Decimal = AGREEMENT_TOLERANCE,
) -> Reconciliation:
    """Merge two passes into reconciled values.

    A value is stored only when both passes agree within ``tolerance``, or when
    the item is non-critical and only one pass found it. Critical disagreement
    is recorded as a conflict and the value is withheld — the filing goes to the
    exception queue instead of the engine.
    """
    critical = set(critical_keys)
    items: dict[str, ReconciledItem] = {}
    conflicts: list[str] = []
    one_sided: list[str] = []

    for key in sorted(set(pass1.values) | set(pass2.values)):
        v1 = pass1.values.get(key)
        v2 = pass2.values.get(key)
        prov = pass1.provenance.get(key) or pass2.provenance.get(key)
        assert prov is not None  # a value always arrives with its item

        if v1 is None or v2 is None:
            if key in critical:
                # A critical item seen by only one pass is a disagreement (§6 V9).
                one_sided.append(key)
                conflicts.append(key)
                continue
            one_sided.append(key)
            items[key] = _make(key, v1 if v1 is not None else v2, False, v1, v2, prov)
            continue

        diff = _rel_diff(v1, v2)
        agreed = diff is None or diff < tolerance
        if not agreed and key in critical:
            conflicts.append(key)
            continue
        items[key] = _make(key, v1, agreed, v1, v2, prov)

    unparseable = {**pass2.unparseable, **pass1.unparseable}
    return Reconciliation(
        items=items,
        conflicts=conflicts,
        one_sided=one_sided,
        unparseable=unparseable,
    )


def _make(
    key: str,
    value: Decimal | None,
    agreement: bool,
    v1: Decimal | None,
    v2: Decimal | None,
    prov: ExtractedItem,
) -> ReconciledItem:
    assert value is not None
    return ReconciledItem(
        key=key,
        value=value,
        agreement=agreement,
        pass_1_value=v1,
        pass_2_value=v2,
        page_no=prov.page_no,
        note_ref=prov.note_ref,
    )


# ----------------------------------------------------------------------
# §3.3 interest income — mandatory note-level extraction
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class InterestIncomeCheck:
    material: bool
    note_located: bool
    sufficient: bool
    reason: str


def check_interest_income_traceability(
    values: dict[str, Decimal],
    provenance: dict[str, ReconciledItem],
) -> InterestIncomeCheck:
    """Enforce §3.3: material other income must be traced to its note.

    ``interest_income`` sourced only from the statement face while a material
    ``other_income`` sits unexplained is exactly the failure mode that produces
    a false *compliant* verdict, so it is refused.
    """
    revenue = values.get("total_revenue")
    other_income = values.get("other_income")

    if revenue is None or revenue == 0:
        return InterestIncomeCheck(False, False, False, "total_revenue unavailable; cannot assess materiality")

    if other_income is None:
        return InterestIncomeCheck(
            False, True, True, "no other_income reported; interest income taken from the statement face"
        )

    material = (abs(other_income) / revenue) > OTHER_INCOME_MATERIALITY
    if not material:
        return InterestIncomeCheck(
            False, True, True, "other_income immaterial (<=2% of revenue); note-level tracing not required"
        )

    note = provenance.get("other_income")
    if note is None or note.note_ref is None:
        return InterestIncomeCheck(
            True, False, False,
            "other_income is material (>2% of revenue) and its note could not be located or parsed (§3.3)",
        )
    return InterestIncomeCheck(
        True, True, True, f"other_income traced to note {note.note_ref} on page {note.page_no}"
    )


# ----------------------------------------------------------------------
# §3.2 financing classification gate
# ----------------------------------------------------------------------
def unclassified_borrowings(classifications: dict[str, FinancingClass]) -> list[str]:
    """Return borrowing captions that could not be classified (§3.2).

    A non-empty result makes the filing DATA_INSUFFICIENT: defaulting to
    conventional invents a breach, defaulting to Islamic hides one.
    """
    return sorted(c for c, k in classifications.items() if k is FinancingClass.AMBIGUOUS)


# ----------------------------------------------------------------------
# Mapping to the engine's input type
# ----------------------------------------------------------------------
#: Reconciled keys that map straight onto :class:`~engine.types.Financials`.
_FINANCIALS_FIELDS = frozenset(Financials.__dataclass_fields__)


def to_financials(values: dict[str, Decimal]) -> Financials:
    """Build the engine's :class:`Financials` from reconciled values.

    Unknown keys are ignored rather than silently coerced; absent keys stay
    ``None``, which each screen interprets explicitly (never as zero).
    """
    kwargs = {k: v for k, v in values.items() if k in _FINANCIALS_FIELDS}
    return Financials(**kwargs)


def impure_income_components(values: dict[str, Decimal]) -> dict[str, Decimal]:
    """Break out the Screen B numerator for the audit snapshot (§2.2)."""
    keys = (
        "interest_income",
        "income_from_conventional_investments",
        "other_non_permissible_income",
    )
    return {k: values[k] for k in keys if k in values}
