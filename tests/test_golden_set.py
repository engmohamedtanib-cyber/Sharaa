"""The golden-set gate (``ROADMAP_V1`` M6).

Right now this set is **empty**, and that is the single most important fact about
extraction in this project: accuracy is *unknown*, not good.

So this file does two things:

1. **Reports the gap, without pretending.** While the set is empty the coverage
   test *skips with the reason spelled out* — a skip is visible in every run and
   says "unknown", which is the true state. It does not pass silently. The
   moment the first case lands, the same test becomes a hard gate: a set that
   has cases but falls short of the composition fails. Set ``STRICT_GOLDEN_SET``
   to make the empty case fail too, once collection has actually started.

2. **Proves the harness works before there is data**, on a synthetic case. The
   day the first real filing arrives, the loader, the comparison and the
   accuracy calculation have already been tested — the only new variable is the
   filing itself.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from ingestion.intake import GOLDEN_REQUIREMENTS

GOLDEN_DIR = Path(__file__).parent / "golden"

#: The 12 items whose extraction must be >=99% accurate (EXTRACTION_SPEC §3.1).
CRITICAL_ITEMS = (
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
)

ACCURACY_BAR = Decimal("0.99")


class GoldenCaseError(ValueError):
    """A golden file is malformed. Malformed golden data is worse than none."""


@dataclass(frozen=True)
class GoldenCase:
    """One hand-verified filing."""

    path: Path
    ticker: str
    fiscal_year: int
    period: str
    expected: dict[str, Decimal]
    provenance: dict[str, dict[str, Any]]
    language: str = ""
    scanned: bool = False
    unit_scale: str = ""

    @property
    def label(self) -> str:
        return f"{self.ticker}_{self.fiscal_year}_{self.period}"


def load_case(path: Path) -> GoldenCase:
    """Parse one golden YAML, refusing anything incomplete.

    Every expected value must carry a page number. A hand-verified figure with
    no page reference cannot be re-checked by the next person, which makes it an
    assertion rather than evidence (R1).
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise GoldenCaseError(f"{path.name}: not a mapping")

    for key in ("ticker", "fiscal_year", "period", "expected"):
        if key not in raw:
            raise GoldenCaseError(f"{path.name}: missing {key!r}")

    expected_raw = raw["expected"]
    if not isinstance(expected_raw, dict):
        raise GoldenCaseError(f"{path.name}: `expected` must be a mapping")

    missing = [item for item in CRITICAL_ITEMS if item not in expected_raw]
    if missing:
        raise GoldenCaseError(
            f"{path.name}: missing critical item(s) {missing}. A golden case that skips an "
            "item silently exempts it from the accuracy bar."
        )

    expected: dict[str, Decimal] = {}
    provenance: dict[str, dict[str, Any]] = {}
    for item, spec in expected_raw.items():
        if not isinstance(spec, dict) or "value" not in spec:
            raise GoldenCaseError(f"{path.name}: {item} must be a mapping with `value` and `page`")
        if spec.get("page") is None:
            raise GoldenCaseError(
                f"{path.name}: {item} has no page reference. Provenance is mandatory (R1) — "
                "a figure nobody can re-check is not verification."
            )
        expected[item] = Decimal(str(spec["value"]))
        provenance[item] = {"page": spec["page"], "note": spec.get("note", "")}

    source = raw.get("source") or {}
    return GoldenCase(
        path=path,
        ticker=str(raw["ticker"]).upper(),
        fiscal_year=int(raw["fiscal_year"]),
        period=str(raw["period"]).upper(),
        expected=expected,
        provenance=provenance,
        language=str(source.get("language", "")),
        scanned=bool(source.get("scanned", False)),
        unit_scale=str(source.get("unit_scale", "")),
    )


def discover_cases() -> list[GoldenCase]:
    """Every golden case currently in the repository."""
    if not GOLDEN_DIR.exists():
        return []
    return [load_case(p) for p in sorted(GOLDEN_DIR.glob("*.yaml")) if not p.name.startswith("_")]


def accuracy(expected: dict[str, Decimal], extracted: dict[str, Decimal | None]) -> Decimal:
    """Share of critical items extracted exactly.

    Exactly — not "within a rounding tolerance". These are figures printed on a
    statement; a mismatch is a misread, not noise.
    """
    if not expected:
        return Decimal(0)
    correct = sum(1 for k, v in expected.items() if extracted.get(k) == v)
    return Decimal(correct) / Decimal(len(expected))


# ----------------------------------------------------------------------
# The gate
# ----------------------------------------------------------------------
def test_golden_set_coverage() -> None:
    """The set must reach the composition M6 requires.

    Empty set -> skip, with the reason printed on every run: extraction accuracy
    is UNKNOWN. Partially collected set -> hard failure, because that is a set
    someone is relying on that is not yet trustworthy.
    """
    cases = discover_cases()
    counts = {"filings": len(cases), "issuers": len({c.ticker for c in cases})}
    shortfalls = [
        f"{req.key}: {counts[req.key]} of {req.minimum}"
        for req in GOLDEN_REQUIREMENTS
        if not req.met_by(counts[req.key])
    ]

    if shortfalls and not cases and not os.environ.get("STRICT_GOLDEN_SET"):
        pytest.skip(
            "GOLDEN SET EMPTY — extraction accuracy is UNKNOWN, not good. No position may be "
            "opened on freshly extracted data. Needs " + "; ".join(shortfalls)
            + ". See tests/golden/README.md and docs/DATA_REQUEST.md §2."
        )

    assert not shortfalls, (
        "The golden set does not meet its composition requirements: "
        + "; ".join(shortfalls)
        + ". Until it does, extraction accuracy is UNKNOWN and no position may be opened on "
        "freshly extracted data. See tests/golden/README.md and docs/DATA_REQUEST.md §2."
    )


@pytest.mark.parametrize("case", discover_cases(), ids=lambda c: c.label)
def test_every_golden_case_is_well_formed(case: GoldenCase) -> None:
    """A golden file must be complete before it can judge anything."""
    assert set(case.expected) >= set(CRITICAL_ITEMS)
    assert all(case.provenance[item]["page"] is not None for item in CRITICAL_ITEMS)


# ----------------------------------------------------------------------
# The harness, proven before the data arrives
# ----------------------------------------------------------------------
def _synthetic_yaml(**overrides: Any) -> str:
    expected = {item: {"value": 100 + i, "page": 12} for i, item in enumerate(CRITICAL_ITEMS)}
    doc: dict[str, Any] = {
        "ticker": "TEST",
        "fiscal_year": 2025,
        "period": "FY",
        "source": {"language": "AR", "scanned": True, "unit_scale": "thousands"},
        "expected": expected,
    }
    doc.update(overrides)
    return yaml.safe_dump(doc, allow_unicode=True)


def test_harness_loads_a_well_formed_case(tmp_path: Path) -> None:
    path = tmp_path / "TEST_FY2025_FY.yaml"
    path.write_text(_synthetic_yaml(), encoding="utf-8")
    case = load_case(path)
    assert case.label == "TEST_2025_FY"
    assert case.scanned is True
    assert case.unit_scale == "thousands"
    assert len(case.expected) == len(CRITICAL_ITEMS)


def test_harness_refuses_a_case_missing_a_critical_item(tmp_path: Path) -> None:
    expected = {item: {"value": 1, "page": 1} for item in CRITICAL_ITEMS if item != "interest_income"}
    path = tmp_path / "TEST_FY2025_FY.yaml"
    path.write_text(_synthetic_yaml(expected=expected), encoding="utf-8")
    with pytest.raises(GoldenCaseError, match="interest_income"):
        load_case(path)


def test_harness_refuses_a_figure_without_a_page_reference(tmp_path: Path) -> None:
    expected = {item: {"value": 1, "page": 1} for item in CRITICAL_ITEMS}
    expected["total_assets"] = {"value": 1}
    path = tmp_path / "TEST_FY2025_FY.yaml"
    path.write_text(_synthetic_yaml(expected=expected), encoding="utf-8")
    with pytest.raises(GoldenCaseError, match="no page reference"):
        load_case(path)


def test_harness_refuses_malformed_files(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(GoldenCaseError, match="not a mapping"):
        load_case(bad)

    incomplete = tmp_path / "incomplete.yaml"
    incomplete.write_text(yaml.safe_dump({"ticker": "TEST"}), encoding="utf-8")
    with pytest.raises(GoldenCaseError, match="missing"):
        load_case(incomplete)


def test_accuracy_is_exact_not_approximate() -> None:
    expected = {"total_assets": Decimal("1000"), "total_revenue": Decimal("500")}
    assert accuracy(expected, {"total_assets": Decimal("1000"), "total_revenue": Decimal("500")}) == 1
    # One EGP out is a misread, not noise.
    assert accuracy(expected, {"total_assets": Decimal("1001"), "total_revenue": Decimal("500")}) == Decimal("0.5")
    # A missing extraction counts as wrong, never as excused.
    assert accuracy(expected, {"total_assets": Decimal("1000")}) == Decimal("0.5")
    assert accuracy({}, {}) == 0


def test_the_accuracy_bar_is_where_the_spec_says() -> None:
    assert Decimal("0.99") == ACCURACY_BAR
    expected = {item: Decimal(1) for item in CRITICAL_ITEMS}
    one_wrong = dict.fromkeys(CRITICAL_ITEMS, Decimal(1))
    one_wrong["interest_income"] = Decimal(2)
    # 11/12 = 0.9166 — one misread in twelve is already below the bar.
    assert accuracy(expected, one_wrong) < ACCURACY_BAR
