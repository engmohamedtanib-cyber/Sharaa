"""Acquisition: hashing, dedupe, scanned/language classification (BUILD_SPEC 2b)."""

from __future__ import annotations

from engine.types import PeriodType
from ingestion.acquire import (
    SCANNED_CHARS_PER_PAGE,
    acquire,
    detect_language,
    is_scanned,
    sha256_hex,
    storage_path,
)


class FakeDownloader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.calls = 0

    def get(self, url: str) -> bytes:
        self.calls += 1
        return self.data


class FakeReader:
    def __init__(self, pages: list[str]) -> None:
        self.pages = pages

    def page_texts(self, data: bytes) -> list[str]:
        return self.pages


class FakeStore:
    def __init__(self) -> None:
        self.written: dict[str, bytes] = {}

    def put(self, path: str, data: bytes) -> str:
        self.written[path] = data
        return path


def test_sha256_is_stable_and_content_addressed():
    assert sha256_hex(b"abc") == sha256_hex(b"abc")
    assert sha256_hex(b"abc") != sha256_hex(b"abd")


# ---- scanned classification -----------------------------------------
def test_text_layer_document_not_scanned():
    pages = ["x" * (SCANNED_CHARS_PER_PAGE + 50)] * 5
    assert is_scanned(pages) is False


def test_thin_text_layer_is_scanned():
    pages = ["x" * 10] * 5
    assert is_scanned(pages) is True


def test_empty_document_treated_as_scanned():
    assert is_scanned([]) is True


def test_few_blank_pages_do_not_force_ocr():
    pages = ["x" * 2000] * 4 + ["", ""]
    assert is_scanned(pages) is False


# ---- language detection ---------------------------------------------
def test_detect_arabic():
    assert detect_language(["إجمالي الأصول والخصوم وحقوق الملكية"]) == "ar"


def test_detect_english():
    assert detect_language(["Total assets and liabilities and equity"]) == "en"


def test_detect_bilingual():
    assert detect_language(["إجمالي الأصول Total Assets إجمالي الخصوم Total Liabilities"]) == "ar+en"


def test_empty_defaults_to_arabic_pre_ocr():
    assert detect_language([""]) == "ar"


# ---- storage path ----------------------------------------------------
def test_storage_path_is_deterministic():
    p1 = storage_path("COMI", 2026, PeriodType.FY, "a" * 64)
    p2 = storage_path("COMI", 2026, PeriodType.FY, "a" * 64)
    assert p1 == p2
    assert p1.startswith("filings/COMI/2026/FY/")


def test_storage_path_distinguishes_restatements():
    a = storage_path("COMI", 2026, PeriodType.FY, "a" * 64)
    b = storage_path("COMI", 2026, PeriodType.FY, "b" * 64)
    assert a != b


# ---- acquire ---------------------------------------------------------
def test_acquire_archives_and_classifies():
    data = b"%PDF-1.7 ..."
    store = FakeStore()
    result = acquire(
        "http://example/f.pdf",
        egx_code="COMI",
        fiscal_year=2026,
        period=PeriodType.FY,
        downloader=FakeDownloader(data),
        reader=FakeReader(["Total assets " * 40] * 3),
        store=store,
    )
    assert result is not None
    assert result.page_count == 3
    assert result.is_scanned is False
    assert result.file_sha256 == sha256_hex(data)
    assert result.storage_path in store.written


def test_acquire_is_idempotent_on_known_hash():
    data = b"%PDF-1.7 ..."
    digest = sha256_hex(data)
    store = FakeStore()
    result = acquire(
        "http://example/f.pdf",
        egx_code="COMI",
        fiscal_year=2026,
        period=PeriodType.FY,
        downloader=FakeDownloader(data),
        reader=FakeReader(["text"]),
        store=store,
        known_hashes={digest},
    )
    assert result is None
    assert store.written == {}  # nothing re-archived
