"""Acquisition: download, hash, dedupe, archive, classify (BUILD_SPEC 2b).

The SHA-256 of the file is the idempotency key (``filings.file_sha256`` is
UNIQUE), so re-running the scheduler never double-processes a filing.

Network and PDF libraries sit behind protocols, keeping the decision logic
— hashing, dedupe, scanned-vs-text classification, storage paths — pure and
testable without downloading anything.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Protocol

from engine.types import PeriodType

#: A page with fewer extractable characters than this is treated as scanned
#: (BUILD_SPEC 2b). Text-layer pages in EGX filings carry far more.
SCANNED_CHARS_PER_PAGE = 100


class Downloader(Protocol):
    """Fetches bytes for a URL. Implemented at the process boundary."""

    def get(self, url: str) -> bytes: ...


class PdfTextReader(Protocol):
    """Returns the extractable text-layer content of each page."""

    def page_texts(self, data: bytes) -> list[str]: ...


class ObjectStore(Protocol):
    """Archives the original file (Supabase Storage in production)."""

    def put(self, path: str, data: bytes) -> str: ...


@dataclass(frozen=True)
class AcquiredFiling:
    """A downloaded filing with everything needed to insert a ``filings`` row."""

    file_sha256: str
    storage_path: str
    page_count: int
    is_scanned: bool
    language: str
    source_url: str
    size_bytes: int


def sha256_hex(data: bytes) -> str:
    """Content hash used as the idempotency key."""
    return hashlib.sha256(data).hexdigest()


def is_scanned(page_texts: list[str]) -> bool:
    """Classify a document as scanned when its text layer is too thin.

    Uses the mean characters per page so that a handful of blank pages in an
    otherwise digital filing does not force the whole document down the OCR
    path. An empty document is treated as scanned — the conservative choice,
    since OCR will either recover the text or the filing fails validation.
    """
    if not page_texts:
        return True
    total = sum(len(t.strip()) for t in page_texts)
    return (total / len(page_texts)) < SCANNED_CHARS_PER_PAGE


_ARABIC_RANGE = re.compile(r"[؀-ۿ]")
_LATIN_RANGE = re.compile(r"[A-Za-z]")


def detect_language(page_texts: list[str]) -> str:
    """Return ``'ar'``, ``'en'`` or ``'ar+en'`` for ``filings.language``.

    A document counts as bilingual when the weaker script carries at least 10%
    of the identified characters; EGX filings are frequently dual-column.
    """
    sample = "\n".join(page_texts)
    arabic = len(_ARABIC_RANGE.findall(sample))
    latin = len(_LATIN_RANGE.findall(sample))
    total = arabic + latin
    if total == 0:
        return "ar"  # scanned Arabic filing before OCR; corrected post-OCR
    minority_share = min(arabic, latin) / total
    if minority_share >= 0.10:
        return "ar+en"
    return "ar" if arabic > latin else "en"


def storage_path(
    egx_code: str,
    fiscal_year: int,
    period: PeriodType,
    file_sha256: str,
) -> str:
    """Deterministic archive path. The hash suffix keeps restatements distinct."""
    return f"filings/{egx_code}/{fiscal_year}/{period.value}/{file_sha256[:16]}.pdf"


def acquire(
    url: str,
    *,
    egx_code: str,
    fiscal_year: int,
    period: PeriodType,
    downloader: Downloader,
    reader: PdfTextReader,
    store: ObjectStore,
    known_hashes: set[str] | None = None,
) -> AcquiredFiling | None:
    """Download, hash, dedupe and archive one filing.

    Returns ``None`` when the file hash is already known — the idempotency
    guarantee that lets the scheduler run repeatedly without double-processing.
    """
    data = downloader.get(url)
    digest = sha256_hex(data)

    if known_hashes and digest in known_hashes:
        return None

    pages = reader.page_texts(data)
    path = storage_path(egx_code, fiscal_year, period, digest)
    store.put(path, data)

    return AcquiredFiling(
        file_sha256=digest,
        storage_path=path,
        page_count=len(pages),
        is_scanned=is_scanned(pages),
        language=detect_language(pages),
        source_url=url,
        size_bytes=len(data),
    )
