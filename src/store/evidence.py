"""The evidence registry — provenance you can verify, not provenance you assert.

``CLAUDE.md`` R1 requires every stored figure to carry a source reference. A
reference is only worth the check behind it, so this module does the check: it
reads a ``metadata.yaml``, recomputes the digest of the bytes beside it, and
raises if they disagree. ``tests/test_evidence.py`` runs that over the whole tree
on every test run, which is what makes a hash in a YAML file mean something.

Layout and admission rules: ``memory/evidence/README.md``.

This is a boundary module: it reads files. Nothing in ``engine/`` or
``validation/`` imports it — they receive already-verified values.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

#: Repo root = parent of src/.
_ROOT = Path(__file__).resolve().parent.parent.parent

#: Every evidence record lives under here, one directory per item.
EVIDENCE_DIR = _ROOT / "memory" / "evidence"

METADATA_FILENAME = "metadata.yaml"

_REQUIRED = ("id", "title", "publisher", "kind", "file", "sha256", "as_of")

_CHUNK = 65536


class EvidenceError(RuntimeError):
    """Base class for evidence registry failures."""


class EvidenceMetadataError(EvidenceError):
    """A ``metadata.yaml`` is missing, malformed, or incomplete."""


class EvidenceMismatchError(EvidenceError):
    """The bytes on disk do not match what the metadata claims.

    This is never a benign condition. Either the file was replaced (evidence is
    append-only — a new vintage is a new directory, R5) or the metadata was
    edited to fit. Both invalidate every figure traced to this record.
    """


class EvidenceMissingError(EvidenceError):
    """The metadata describes a file that is not on disk."""


@dataclass(frozen=True)
class Acquisition:
    """How a document reached us. ``url`` is null unless actually fetched."""

    at: str | None = None
    via: str | None = None
    original_filename: str | None = None
    url: str | None = None
    url_verified: bool = False


@dataclass(frozen=True)
class EvidenceRecord:
    """One source document and everything known about it."""

    id: str
    title: str
    publisher: str
    kind: str
    as_of: str
    sha256: str
    path: Path
    directory: Path
    version: int = 1
    media_type: str | None = None
    bytes_declared: int | None = None
    acquired: Acquisition = field(default_factory=Acquisition)
    used_by: tuple[str, ...] = ()
    supersedes: str | None = None
    superseded_by: str | None = None
    notes: str | None = None

    @property
    def exists(self) -> bool:
        """False for records whose bytes are deliberately not committed."""
        return self.path.is_file()

    @property
    def provenance_is_complete(self) -> bool:
        """True only when the origin URL was actually fetched and verified.

        The universe record is deliberately False: the bytes are attested, the
        chain of custody before upload is not (``decisions/0005``).
        """
        return bool(self.acquired.url) and self.acquired.url_verified


def sha256_of(path: Path) -> str:
    """Digest a file without reading it entirely into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _acquisition(raw: Any) -> Acquisition:
    if raw is None:
        return Acquisition()
    if not isinstance(raw, dict):
        raise EvidenceMetadataError("`acquired` must be a mapping")
    url = raw.get("url")
    verified = bool(raw.get("url_verified", False))
    if verified and not url:
        raise EvidenceMetadataError("`acquired.url_verified` is true but no URL is recorded")
    return Acquisition(
        at=None if raw.get("at") is None else str(raw["at"]),
        via=None if raw.get("via") is None else str(raw["via"]),
        original_filename=None if raw.get("original_filename") is None else str(raw["original_filename"]),
        url=None if url is None else str(url),
        url_verified=verified,
    )


def parse_record(raw: dict[str, Any], directory: Path) -> EvidenceRecord:
    """Build a record from a parsed ``metadata.yaml``. Pure apart from path joins."""
    if not isinstance(raw, dict):
        raise EvidenceMetadataError(f"{directory}: metadata did not parse to a mapping")
    missing = [k for k in _REQUIRED if not raw.get(k)]
    if missing:
        raise EvidenceMetadataError(f"{directory}: metadata is missing {', '.join(missing)}")

    declared_bytes = raw.get("bytes")
    return EvidenceRecord(
        id=str(raw["id"]),
        title=str(raw["title"]),
        publisher=str(raw["publisher"]),
        kind=str(raw["kind"]),
        as_of=str(raw["as_of"]),
        sha256=str(raw["sha256"]).lower(),
        path=directory / str(raw["file"]),
        directory=directory,
        version=int(raw.get("version", 1)),
        media_type=None if raw.get("media_type") is None else str(raw["media_type"]),
        bytes_declared=None if declared_bytes is None else int(declared_bytes),
        acquired=_acquisition(raw.get("acquired")),
        used_by=tuple(str(u) for u in (raw.get("used_by") or [])),
        supersedes=None if raw.get("supersedes") is None else str(raw["supersedes"]),
        superseded_by=None if raw.get("superseded_by") is None else str(raw["superseded_by"]),
        notes=None if raw.get("notes") is None else str(raw["notes"]),
    )


def load_record(directory: Path) -> EvidenceRecord:
    """Read one evidence directory. Does not verify — call :func:`verify` for that."""
    metadata = directory / METADATA_FILENAME
    if not metadata.is_file():
        raise EvidenceMetadataError(f"{directory}: no {METADATA_FILENAME}")
    with metadata.open("r", encoding="utf-8") as fh:
        return parse_record(yaml.safe_load(fh), directory)


def verify(record: EvidenceRecord) -> None:
    """Recompute the digest and raise unless it matches the metadata exactly.

    Also checks the declared byte count when present — a cheap second signal
    that catches a truncated file whose partial content happens to be read
    without error.
    """
    if not record.exists:
        raise EvidenceMissingError(f"{record.id}: {record.path} is not on disk")
    actual_size = record.path.stat().st_size
    if record.bytes_declared is not None and actual_size != record.bytes_declared:
        raise EvidenceMismatchError(
            f"{record.id}: metadata declares {record.bytes_declared} bytes, file is {actual_size}"
        )
    actual = sha256_of(record.path)
    if actual != record.sha256:
        raise EvidenceMismatchError(
            f"{record.id}: sha256 mismatch — metadata says {record.sha256}, file digests to {actual}. "
            "Evidence is append-only: a new vintage is a new directory, never a replaced file."
        )


def iter_records(root: Path | None = None) -> list[EvidenceRecord]:
    """Every record in the tree, sorted by id. A directory is a record iff it
    holds a ``metadata.yaml``, so READMEs and grouping directories are skipped."""
    base = root if root is not None else EVIDENCE_DIR
    if not base.is_dir():
        return []
    found = [load_record(p.parent) for p in sorted(base.rglob(METADATA_FILENAME))]
    return sorted(found, key=lambda r: r.id)


def find(evidence_id: str, root: Path | None = None) -> EvidenceRecord:
    """Look up a record by id, verifying it before handing it back.

    Callers that resolve an id from ``config/`` get a checked record or an
    exception — never an unchecked one, which is the whole point.
    """
    for record in iter_records(root):
        if record.id == evidence_id:
            verify(record)
            return record
    raise EvidenceMetadataError(f"no evidence record with id {evidence_id!r}")
