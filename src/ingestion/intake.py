"""Intake: what happens to a filing the moment it lands, before anything reads it.

The user collects PDFs outside this sandbox and drops them in. This module is
the front door: it names each file, hashes it, checks it against what we already
have, and produces a report of what arrived and what is still missing.

It reads bytes and nothing else. No LLM, no network, no extraction — those come
later and only for files that got through here. The point of a separate intake
step is that **a file that cannot be identified never reaches extraction**: an
unparseable filename means we do not know which company or period the numbers
belong to, and numbers filed against the wrong period are worse than no numbers.

Three rules:

* **Identify or refuse.** ``TICKER_FYyyyy_PERIOD.pdf``. Anything else is
  reported by name, never guessed at from the file's contents.
* **Dedupe by content, not by name.** The same annual report arrives twice under
  two names constantly. The SHA-256 decides.
* **Say what is still missing.** The report compares what arrived against what
  the golden set needs, so the answer to "what else should I get?" is computed,
  not remembered.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from engine.types import PeriodType

#: Filenames must be TICKER_FYyyyy_PERIOD.pdf — see docs/DATA_REQUEST.md §5.
_EXPECTED_PARTS = 3


class IntakeError(ValueError):
    """A file cannot be admitted."""


@dataclass(frozen=True)
class IntakeItem:
    """One identified file, ready for extraction."""

    path: Path
    ticker: str
    fiscal_year: int
    period: PeriodType
    sha256: str
    size_bytes: int

    @property
    def label(self) -> str:
        return f"{self.ticker} {self.fiscal_year} {self.period.value}"

    @property
    def archive_path(self) -> str:
        """Deterministic storage path, matching ``ingestion.acquire.storage_path``."""
        return f"filings/{self.ticker}/{self.fiscal_year}/{self.period.value}/{self.sha256[:16]}.pdf"


@dataclass(frozen=True)
class IntakeReport:
    """Everything one intake scan learned."""

    accepted: tuple[IntakeItem, ...] = ()
    duplicates: tuple[tuple[Path, str], ...] = ()      # (path, sha of the file it duplicates)
    unidentified: tuple[tuple[Path, str], ...] = ()    # (path, why)
    issuers: frozenset[str] = field(default_factory=frozenset)

    @property
    def usable(self) -> int:
        return len(self.accepted)

    def summary(self) -> str:
        lines = [
            f"accepted     : {len(self.accepted)}",
            f"duplicates   : {len(self.duplicates)}",
            f"unidentified : {len(self.unidentified)}",
            f"issuers      : {len(self.issuers)}",
        ]
        for path, why in self.unidentified:
            lines.append(f"  ? {path.name}: {why}")
        return "\n".join(lines)


def sha256_file(path: Path, *, chunk_size: int = 1 << 20) -> str:
    """Hash a file without loading it into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def parse_filename(name: str) -> tuple[str, int, PeriodType]:
    """Read ticker, fiscal year and period out of a filename.

    Raises :class:`IntakeError` with the reason rather than returning a partial
    identification. "I think this is COMI" is not an identification.
    """
    stem = Path(name).stem
    parts = stem.split("_")
    if len(parts) < _EXPECTED_PARTS:
        raise IntakeError(
            "expected TICKER_FYyyyy_PERIOD.pdf (e.g. ABUK_FY2025_FY.pdf); "
            f"got {len(parts)} part(s)"
        )

    ticker = parts[0].strip().upper()
    if not ticker.isalnum():
        raise IntakeError(f"{parts[0]!r} is not a plausible EGX code")

    year_part = parts[1].strip().upper().removeprefix("FY")
    if not year_part.isdigit() or len(year_part) != 4:
        raise IntakeError(f"{parts[1]!r} is not a fiscal year (expected FY2025)")

    period_part = parts[2].strip().upper()
    try:
        period = PeriodType(period_part)
    except ValueError as exc:
        allowed = ", ".join(p.value for p in PeriodType)
        raise IntakeError(f"{period_part!r} is not a period ({allowed})") from exc

    return ticker, int(year_part), period


def scan_directory(directory: Path, *, known_hashes: set[str] | None = None) -> IntakeReport:
    """Identify every PDF in ``directory``.

    ``known_hashes`` are files already archived. A file whose hash is known is a
    duplicate regardless of what it is called — the same annual report routinely
    arrives twice under two different names.
    """
    if not directory.exists():
        raise IntakeError(f"{directory} does not exist")

    seen: set[str] = set(known_hashes or set())
    accepted: list[IntakeItem] = []
    duplicates: list[tuple[Path, str]] = []
    unidentified: list[tuple[Path, str]] = []

    for path in sorted(directory.glob("*.pdf")):
        try:
            ticker, year, period = parse_filename(path.name)
        except IntakeError as exc:
            unidentified.append((path, str(exc)))
            continue

        digest = sha256_file(path)
        if digest in seen:
            duplicates.append((path, digest))
            continue
        seen.add(digest)
        accepted.append(
            IntakeItem(
                path=path,
                ticker=ticker,
                fiscal_year=year,
                period=period,
                sha256=digest,
                size_bytes=path.stat().st_size,
            )
        )

    return IntakeReport(
        accepted=tuple(accepted),
        duplicates=tuple(duplicates),
        unidentified=tuple(unidentified),
        issuers=frozenset(item.ticker for item in accepted),
    )


# ======================================================================
# Golden-set coverage
# ======================================================================
@dataclass(frozen=True)
class CoverageRequirement:
    """One thing the golden set must contain (``ROADMAP_V1`` M6)."""

    key: str
    description: str
    minimum: int

    def met_by(self, count: int) -> bool:
        return count >= self.minimum


#: The composition the golden set needs. Counts that intake can compute on its
#: own are checked here; the rest (scanned, Arabic-only, restatement, unit scale)
#: need a PDF reader and are checked when the file is actually read.
GOLDEN_REQUIREMENTS: tuple[CoverageRequirement, ...] = (
    CoverageRequirement("filings", "filings in total", 15),
    CoverageRequirement("issuers", "distinct issuers", 5),
)

#: Requirements intake cannot see from a filename. Listed so the report can say
#: "still unknown" rather than silently reporting full coverage.
UNVERIFIABLE_AT_INTAKE: tuple[str, ...] = (
    "at least 3 scanned (image) PDFs",
    "at least 2 Arabic-only filings",
    "at least 1 with Islamic financing on the balance sheet",
    "at least 1 with interest income inside other income",
    "at least 1 reported in thousands and 1 in millions",
    "at least 1 containing a restatement",
)


def coverage(report: IntakeReport) -> dict[str, tuple[int, int, bool]]:
    """How far the collected set is from the golden-set composition.

    Returns ``{key: (have, need, met)}``. Only what a filename can prove.
    """
    counts = {"filings": len(report.accepted), "issuers": len(report.issuers)}
    return {
        req.key: (counts[req.key], req.minimum, req.met_by(counts[req.key]))
        for req in GOLDEN_REQUIREMENTS
    }


def missing_summary(report: IntakeReport) -> str:
    """Plain-language statement of what is still needed."""
    lines: list[str] = []
    for key, (have, need, met) in coverage(report).items():
        mark = "ok" if met else "->"
        lines.append(f"  {mark} {key}: {have} of {need}")
    lines.append("  ?  not checkable from filenames alone (verified when the PDF is read):")
    lines.extend(f"       - {item}" for item in UNVERIFIABLE_AT_INTAKE)
    return "\n".join(lines)
