"""The evidence registry (R1).

The load-bearing tests are at the bottom: they walk every record committed to
`memory/evidence/` and recompute its digest from the actual bytes. That is what
turns a hash written in a YAML file into a claim that is checked, on every run,
against the thing it describes.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from store.evidence import (
    EVIDENCE_DIR,
    Acquisition,
    EvidenceMetadataError,
    EvidenceMismatchError,
    EvidenceMissingError,
    EvidenceRecord,
    find,
    iter_records,
    load_record,
    parse_record,
    sha256_of,
    verify,
)

# sha256 of b"hello"
HELLO_SHA = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"

META = {
    "id": "test/thing/2026-01-01",
    "version": 1,
    "title": "A thing",
    "publisher": "Someone",
    "kind": "TEST",
    "file": "thing.txt",
    "sha256": HELLO_SHA,
    "as_of": "2026-01-01",
}


@pytest.fixture
def item(tmp_path):
    """A well-formed evidence directory holding b"hello"."""

    def build(**overrides):
        directory = tmp_path / "test" / "thing" / "2026-01-01"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "thing.txt").write_bytes(b"hello")
        meta = {**META, **overrides}
        (directory / "metadata.yaml").write_text(yaml.safe_dump(meta), encoding="utf-8")
        return directory

    return build


# ======================================================================
# Verification — the point of the module
# ======================================================================
def test_matching_bytes_verify(item):
    verify(load_record(item()))


def test_altered_bytes_are_caught(item):
    directory = item()
    (directory / "thing.txt").write_bytes(b"tampered")
    with pytest.raises(EvidenceMismatchError, match="sha256 mismatch"):
        verify(load_record(directory))


def test_the_mismatch_message_says_evidence_is_append_only(item):
    """A future reader hitting this must not "fix" it by editing the hash."""
    directory = item()
    (directory / "thing.txt").write_bytes(b"tampered")
    with pytest.raises(EvidenceMismatchError, match="append-only"):
        verify(load_record(directory))


def test_a_truncated_file_is_caught_by_the_byte_count(item):
    directory = item(bytes=5)
    (directory / "thing.txt").write_bytes(b"hi")
    with pytest.raises(EvidenceMismatchError, match="declares 5 bytes, file is 2"):
        verify(load_record(directory))


def test_a_correct_byte_count_passes(item):
    verify(load_record(item(bytes=5)))


def test_absent_bytes_are_reported_not_ignored(item):
    directory = item()
    (directory / "thing.txt").unlink()
    record = load_record(directory)
    assert not record.exists
    with pytest.raises(EvidenceMissingError, match="not on disk"):
        verify(record)


def test_hash_comparison_is_case_insensitive_on_the_metadata_side(item):
    verify(load_record(item(sha256=HELLO_SHA.upper())))


def test_sha256_of_reads_files_larger_than_one_chunk(tmp_path):
    import hashlib

    blob = b"x" * 200_000
    path = tmp_path / "big.bin"
    path.write_bytes(blob)
    assert sha256_of(path) == hashlib.sha256(blob).hexdigest()


# ======================================================================
# Metadata integrity
# ======================================================================
@pytest.mark.parametrize("missing", ["id", "title", "publisher", "kind", "file", "sha256", "as_of"])
def test_every_required_field_is_required(item, missing):
    with pytest.raises(EvidenceMetadataError, match=missing):
        load_record(item(**{missing: None}))


def test_a_directory_without_metadata_is_rejected(tmp_path):
    with pytest.raises(EvidenceMetadataError, match=r"no metadata\.yaml"):
        load_record(tmp_path)


def test_metadata_that_is_not_a_mapping_is_rejected(tmp_path):
    (tmp_path / "metadata.yaml").write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(EvidenceMetadataError, match="did not parse to a mapping"):
        load_record(tmp_path)


def test_a_non_mapping_acquired_block_is_rejected(item):
    with pytest.raises(EvidenceMetadataError, match="`acquired` must be a mapping"):
        load_record(item(acquired="yesterday"))


def test_claiming_a_verified_url_without_a_url_is_rejected(item):
    """The exact shape of the lie this registry exists to prevent: an assertion
    of verification with nothing behind it."""
    with pytest.raises(EvidenceMetadataError, match="true but no URL"):
        load_record(item(acquired={"url": None, "url_verified": True}))


def test_optional_fields_default_cleanly(item):
    record = load_record(item())
    assert record.version == 1
    assert record.media_type is None and record.bytes_declared is None
    assert record.used_by == () and record.supersedes is None and record.superseded_by is None
    assert record.notes is None
    assert record.acquired == Acquisition()
    assert not record.provenance_is_complete


def test_optional_fields_round_trip(item):
    record = load_record(
        item(
            version=3,
            media_type="text/plain",
            bytes=5,
            used_by=["config/universe.yaml"],
            supersedes="test/thing/2025-01-01",
            superseded_by="test/thing/2027-01-01",
            notes="a note",
            acquired={
                "at": "2026-01-02",
                "via": "FETCH",
                "original_filename": "orig.txt",
                "url": "https://example.test/thing.txt",
                "url_verified": True,
            },
        )
    )
    assert record.version == 3
    assert record.used_by == ("config/universe.yaml",)
    assert record.supersedes == "test/thing/2025-01-01"
    assert record.superseded_by == "test/thing/2027-01-01"
    assert record.notes == "a note"
    assert record.acquired.via == "FETCH"
    assert record.acquired.original_filename == "orig.txt"
    assert record.provenance_is_complete


def test_an_unfetched_url_leaves_provenance_incomplete(item):
    record = load_record(item(acquired={"url": "https://example.test/x", "url_verified": False}))
    assert not record.provenance_is_complete


def test_parse_record_rejects_a_non_mapping():
    with pytest.raises(EvidenceMetadataError, match="did not parse to a mapping"):
        parse_record(["nope"], Path("/tmp/x"))  # type: ignore[arg-type]


def test_records_are_frozen(item):
    record = load_record(item())
    with pytest.raises(Exception):  # noqa: B017  (FrozenInstanceError)
        record.sha256 = "x"


def test_dataclass_defaults_without_yaml():
    r = EvidenceRecord("i", "t", "p", "k", "2026-01-01", "abc", Path("f"), Path("."))
    assert r.version == 1 and r.used_by == () and not r.provenance_is_complete


# ======================================================================
# Walking the tree
# ======================================================================
def test_iter_records_finds_records_and_skips_plain_directories(item, tmp_path):
    item()
    (tmp_path / "not-evidence").mkdir()
    (tmp_path / "README.md").write_text("prose", encoding="utf-8")
    assert [r.id for r in iter_records(tmp_path)] == ["test/thing/2026-01-01"]


def test_iter_records_on_a_missing_tree_is_empty(tmp_path):
    assert iter_records(tmp_path / "nope") == []


def test_find_returns_a_verified_record(item, tmp_path):
    item()
    assert find("test/thing/2026-01-01", tmp_path).title == "A thing"


def test_find_verifies_before_returning(item, tmp_path):
    """A caller resolving an id from config must never receive unchecked bytes."""
    directory = item()
    (directory / "thing.txt").write_bytes(b"tampered")
    with pytest.raises(EvidenceMismatchError):
        find("test/thing/2026-01-01", tmp_path)


def test_find_raises_for_an_unknown_id(item, tmp_path):
    item()
    with pytest.raises(EvidenceMetadataError, match="no evidence record with id"):
        find("test/thing/1999-01-01", tmp_path)


# ======================================================================
# The real tree — every committed record, checked against its bytes
# ======================================================================
@pytest.fixture(scope="module")
def committed() -> list[EvidenceRecord]:
    return iter_records()


def test_the_repository_holds_at_least_one_evidence_record(committed):
    assert committed, f"no evidence records under {EVIDENCE_DIR}"


def test_every_committed_record_matches_its_bytes(committed):
    """If this fails, some stored figure is traceable to bytes that changed under
    it. Do not edit the hash to match — work out which file moved and why."""
    for record in committed:
        if record.exists:
            verify(record)


def test_every_committed_record_id_matches_its_directory(committed):
    for record in committed:
        assert record.directory.as_posix().endswith(record.id), record.id


def test_every_used_by_path_exists(committed):
    """`used_by` is the blast radius of a corrected source. A stale entry makes
    it understate that radius, which is the direction that hurts."""
    root = EVIDENCE_DIR.parent.parent
    for record in committed:
        for dependant in record.used_by:
            assert (root / dependant).exists(), f"{record.id} -> missing {dependant}"


def test_the_universe_evidence_is_registered_and_matches_the_config(committed):
    """config/universe.yaml and the evidence record must agree on the hash. Two
    files naming the same bytes is fine; two files disagreeing is not."""
    root = EVIDENCE_DIR.parent.parent
    universe = yaml.safe_load((root / "config" / "universe.yaml").read_text(encoding="utf-8"))
    record = find(universe["retrieved"]["evidence_id"])
    assert record.sha256 == universe["retrieved"]["sha256"]
    assert record.as_of == universe["retrieved"]["weights_as_of"]


def test_the_universe_evidence_admits_its_url_is_unverified(committed):
    """decisions/0005: the bytes are attested, the chain of custody is not.
    If someone later fills the URL in, they must have actually fetched it."""
    record = find("egx/shariah_index/2026-04-30")
    assert record.acquired.via == "USER_UPLOAD"
    if not record.acquired.url:
        assert not record.acquired.url_verified
