"""Intake: identify or refuse, dedupe by content, and say what is still missing."""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.types import PeriodType
from ingestion.intake import (
    IntakeError,
    coverage,
    missing_summary,
    parse_filename,
    scan_directory,
    sha256_file,
)


def _pdf(directory: Path, name: str, content: bytes = b"%PDF-1.4 filing") -> Path:
    path = directory / name
    path.write_bytes(content)
    return path


# ----------------------------------------------------------------------
# Filename identification
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("ABUK_FY2025_FY.pdf", ("ABUK", 2025, PeriodType.FY)),
        ("abuk_fy2025_h1.pdf", ("ABUK", 2025, PeriodType.H1)),
        ("SWDY_FY2026_9M.pdf", ("SWDY", 2026, PeriodType.NINE_M)),
        ("COMI_2024_Q1.pdf", ("COMI", 2024, PeriodType.Q1)),
        ("ISPH_FY2025_FY_final_v2.pdf", ("ISPH", 2025, PeriodType.FY)),
    ],
)
def test_recognised_filenames(name: str, expected: tuple[str, int, PeriodType]) -> None:
    assert parse_filename(name) == expected


@pytest.mark.parametrize(
    ("name", "why"),
    [
        ("annual_report.pdf", "expected TICKER"),
        ("ABUK_FY2025.pdf", "expected TICKER"),
        ("ABUK_2025x_FY.pdf", "not a fiscal year"),
        ("ABUK_FY25_FY.pdf", "not a fiscal year"),
        ("ABUK_FY2025_Q2.pdf", "not a period"),
        ("AB-UK_FY2025_FY.pdf", "not a plausible EGX code"),
    ],
)
def test_unrecognised_filenames_are_refused_with_a_reason(name: str, why: str) -> None:
    with pytest.raises(IntakeError, match=why):
        parse_filename(name)


def test_q2_is_refused_because_egx_reporting_is_cumulative() -> None:
    """Q2 standalone is derived as H1 - Q1; a file claiming to be Q2 is a category error."""
    with pytest.raises(IntakeError, match="not a period"):
        parse_filename("ABUK_FY2025_Q2.pdf")


# ----------------------------------------------------------------------
# Scanning
# ----------------------------------------------------------------------
def test_scan_identifies_and_hashes(tmp_path: Path) -> None:
    _pdf(tmp_path, "ABUK_FY2025_FY.pdf", b"one")
    _pdf(tmp_path, "SWDY_FY2025_H1.pdf", b"two")

    report = scan_directory(tmp_path)
    assert report.usable == 2
    assert report.issuers == {"ABUK", "SWDY"}
    assert [item.label for item in report.accepted] == ["ABUK 2025 FY", "SWDY 2025 H1"]
    assert report.accepted[0].sha256 == sha256_file(tmp_path / "ABUK_FY2025_FY.pdf")
    assert report.accepted[0].archive_path.startswith("filings/ABUK/2025/FY/")


def test_the_same_document_under_two_names_is_one_filing(tmp_path: Path) -> None:
    _pdf(tmp_path, "ABUK_FY2025_FY.pdf", b"identical bytes")
    _pdf(tmp_path, "ABUK_FY2025_FY_copy.pdf", b"identical bytes")

    report = scan_directory(tmp_path)
    assert report.usable == 1
    assert len(report.duplicates) == 1


def test_a_file_already_archived_is_a_duplicate(tmp_path: Path) -> None:
    path = _pdf(tmp_path, "ABUK_FY2025_FY.pdf", b"already have this")
    report = scan_directory(tmp_path, known_hashes={sha256_file(path)})
    assert report.usable == 0
    assert len(report.duplicates) == 1


def test_unidentified_files_are_named_never_guessed(tmp_path: Path) -> None:
    _pdf(tmp_path, "ABUK_FY2025_FY.pdf")
    _pdf(tmp_path, "annual report 2025 final.pdf")

    report = scan_directory(tmp_path)
    assert report.usable == 1
    assert [p.name for p, _ in report.unidentified] == ["annual report 2025 final.pdf"]
    assert "expected TICKER" in report.unidentified[0][1]
    assert "annual report 2025 final.pdf" in report.summary()


def test_non_pdfs_are_ignored(tmp_path: Path) -> None:
    _pdf(tmp_path, "ABUK_FY2025_FY.pdf")
    (tmp_path / "notes.txt").write_text("not a filing")
    (tmp_path / "prices.csv").write_text("date,close")

    report = scan_directory(tmp_path)
    assert report.usable == 1
    assert report.unidentified == ()


def test_an_empty_directory_is_an_empty_report_not_an_error(tmp_path: Path) -> None:
    report = scan_directory(tmp_path)
    assert report.usable == 0
    assert report.issuers == frozenset()


def test_a_missing_directory_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(IntakeError, match="does not exist"):
        scan_directory(tmp_path / "nope")


# ----------------------------------------------------------------------
# Coverage against the golden-set requirements
# ----------------------------------------------------------------------
def test_coverage_of_an_empty_set(tmp_path: Path) -> None:
    cov = coverage(scan_directory(tmp_path))
    assert cov["filings"] == (0, 15, False)
    assert cov["issuers"] == (0, 5, False)


def test_coverage_counts_issuers_not_files(tmp_path: Path) -> None:
    for year in range(2018, 2024):
        _pdf(tmp_path, f"ABUK_FY{year}_FY.pdf", f"year {year}".encode())
    cov = coverage(scan_directory(tmp_path))
    assert cov["filings"][0] == 6
    assert cov["issuers"][0] == 1          # six filings from one issuer is not five issuers


def test_missing_summary_admits_what_filenames_cannot_prove(tmp_path: Path) -> None:
    summary = missing_summary(scan_directory(tmp_path))
    assert "filings: 0 of 15" in summary
    # The hard requirements are the ones a filename can never establish, and the
    # report says so rather than implying full coverage.
    assert "scanned" in summary
    assert "Arabic-only" in summary
    assert "restatement" in summary
