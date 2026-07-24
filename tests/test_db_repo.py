"""Repository invariants: provenance, append-only, validated-only guard."""

from __future__ import annotations

from datetime import date

import pytest

from conftest import D
from db.models import (
    Company,
    Decision,
    Filing,
    LineItem,
    ScoreTotal,
    ShariahScreen,
)
from db.repo import InMemoryRepository, NotValidatedError, get_repository
from engine.types import (
    DataStatus,
    DecisionType,
    PeriodType,
    ShariahStatus,
)


def _filing(company_id: str, status: DataStatus = DataStatus.PENDING) -> Filing:
    return Filing(
        company_id=company_id, fiscal_year=2026, period=PeriodType.FY,
        period_end=date(2026, 12, 31), source_url="http://x", file_sha256="hash-1",
        storage_path="filings/x.pdf", page_count=40, is_scanned=False,
        language="ar", reported_scale="THOUSANDS", data_status=status,
    )


# ---- provenance NOT NULL (R1) ---------------------------------------
def test_line_item_requires_positive_page():
    with pytest.raises(ValueError):
        LineItem(
            filing_id="f1", statement="BALANCE_SHEET", item_key="total_assets",
            value_egp=D("1000"), raw_value="1,000", raw_caption="إجمالي الأصول",
            page_no=0, extraction_run_id="r1",
        )


def test_derived_line_item_requires_derivation():
    with pytest.raises(ValueError):
        LineItem(
            filing_id="f1", statement="INCOME_STATEMENT", item_key="q2_revenue",
            value_egp=D("100"), raw_value="100", raw_caption="x", page_no=5,
            extraction_run_id="r1", is_derived=True,
        )


def test_valid_line_item_constructs():
    li = LineItem(
        filing_id="f1", statement="INCOME_STATEMENT", item_key="q2_revenue",
        value_egp=D("100"), raw_value="100", raw_caption="x", page_no=5,
        extraction_run_id="r1", is_derived=True, derivation="H1 - Q1",
    )
    assert li.page_no == 5


# ---- decision CHECK constraints (§5.5) ------------------------------
def test_decision_rejects_short_reason():
    with pytest.raises(ValueError):
        Decision(
            review_id="rv", company_id="c1", decision=DecisionType.HOLD,
            shariah_status=ShariahStatus.GREEN, trigger="t", reason="too short",
            falsification_condition="a measurable falsification statement here",
        )


def test_score_total_bounds():
    with pytest.raises(ValueError):
        ScoreTotal(
            company_id="c1", filing_id="f1", p1_shariah=D("25"), p2_financial=D("20"),
            p3_earnings=D("15"), p4_valuation=D("15"), p5_growth=D("10"),
            p6_technical=D("10"), p7_governance=D("5"), total=D("120"), band="X",
        )


# ---- validated-only guard -------------------------------------------
def test_cannot_screen_unvalidated_filing():
    repo = InMemoryRepository()
    repo.upsert_company(Company(egx_code="AAA", name_en="A", sector="Industrials"))
    company = repo.list_companies()[0]
    f = _filing(company.id, DataStatus.INSUFFICIENT)
    repo.add_filing(f)
    screen = ShariahScreen(
        company_id=company.id, filing_id=f.id, screen_code="C", screen_name="Debt",
        numerator=D("100"), denominator=D("1000"), ratio=D("0.1"), threshold=D("0.30"),
        utilisation=D("0.333"), status=ShariahStatus.GREEN, threshold_version="1.0.0",
    )
    with pytest.raises(NotValidatedError):
        repo.append_screen(screen)


def test_can_screen_validated_filing():
    repo = InMemoryRepository()
    repo.upsert_company(Company(egx_code="AAA", name_en="A", sector="Industrials"))
    company = repo.list_companies()[0]
    f = _filing(company.id, DataStatus.VALIDATED)
    repo.add_filing(f)
    screen = ShariahScreen(
        company_id=company.id, filing_id=f.id, screen_code="C", screen_name="Debt",
        numerator=D("100"), denominator=D("1000"), ratio=D("0.1"), threshold=D("0.30"),
        utilisation=D("0.333"), status=ShariahStatus.GREEN, threshold_version="1.0.0",
    )
    repo.append_screen(screen)  # no raise
    assert len(repo.screens(f.id)) == 1


# ---- append-only: no update/delete surface --------------------------
def test_repository_has_no_mutation_methods():
    repo = InMemoryRepository()
    for forbidden in ("update_screen", "delete_screen", "update_decision", "delete_line_item"):
        assert not hasattr(repo, forbidden)


# ---- idempotency via file hash --------------------------------------
def test_add_filing_is_idempotent_on_hash():
    repo = InMemoryRepository()
    repo.upsert_company(Company(egx_code="AAA", name_en="A", sector="Industrials"))
    c = repo.list_companies()[0]
    repo.add_filing(_filing(c.id))
    repo.add_filing(_filing(c.id))  # same sha256 -> ignored
    assert repo.filing_exists("hash-1")


# ---- exception queue + seed round-trip ------------------------------
def test_exception_queue_lists_insufficient_and_conflict():
    repo = InMemoryRepository()
    repo.upsert_company(Company(egx_code="AAA", name_en="A", sector="Industrials"))
    c = repo.list_companies()[0]
    f = _filing(c.id, DataStatus.CONFLICT)
    repo.add_filing(f)
    repo.set_data_status(f.id, DataStatus.CONFLICT, "V9 disagreement")
    q = repo.exception_queue()
    assert len(q) == 1 and q[0].data_status is DataStatus.CONFLICT


def test_seed_ten_companies_round_trip():
    repo = get_repository("memory")
    names = ["COMI", "HRHO", "SWDY", "EAST", "TMGH", "ETEL", "ABUK", "ESRS", "MFPC", "PHDC"]
    for n in names:
        repo.upsert_company(Company(egx_code=n, name_en=n, sector="Test"))
    assert len(repo.list_companies()) == 10
    assert {c.egx_code for c in repo.list_companies()} == set(names)
