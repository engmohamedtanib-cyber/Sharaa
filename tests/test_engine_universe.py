"""Universe parsing and — the point of the module — refusal.

The behaviour under test is that an unpopulated universe raises rather than
returning an empty list. Empty results and "we never looked" are the same shape
downstream, and only one of them is safe to report.
"""

from __future__ import annotations

import pytest

from config_loader import load_universe
from engine.universe import (
    Constituent,
    Provenance,
    Universe,
    UniverseConfigError,
    UniverseStatus,
    UniverseUnavailableError,
    parse_universe,
)


def _raw(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "version": "0.1.0",
        "status": "POPULATED",
        "index": {"name": "EGX 33 Shariah Index", "constituent_count": 2},
        "retrieved": {"at": "2026-07-27", "from": "https://example.test/list", "by": "user"},
        "constituents": [
            {"ticker": "aaaa", "name_en": "Alpha", "name_ar": "ألفا", "sector": "Materials"},
            {"ticker": "BBBB", "name_en": "Beta", "sector": "Industrials"},
        ],
        "off_index_watch": [],
    }
    base.update(overrides)
    return base


# ----------------------------------------------------------------------
# The real file
# ----------------------------------------------------------------------
def test_shipped_universe_is_awaiting_and_refuses_use() -> None:
    u = load_universe()
    assert u.status is UniverseStatus.AWAITING_CONSTITUENTS
    assert u.constituents == ()
    assert u.available is False
    with pytest.raises(UniverseUnavailableError) as exc:
        u.require_available()
    # The message must say what it is, because the wrong reading is the dangerous one.
    assert "never been transcribed" in str(exc.value)


def test_shipped_universe_refuses_to_hand_out_a_screening_list() -> None:
    with pytest.raises(UniverseUnavailableError):
        load_universe().screenable()


# ----------------------------------------------------------------------
# Parsing
# ----------------------------------------------------------------------
def test_parses_a_populated_universe_and_upper_cases_tickers() -> None:
    u = parse_universe(_raw())
    assert u.available is True
    assert u.tickers == ("AAAA", "BBBB")
    assert u.get("aaaa") == Constituent("AAAA", "Alpha", "ألفا", "Materials")
    assert u.contains("bbbb") is True
    assert u.contains("ZZZZ") is False
    assert u.get("ZZZZ") is None
    assert len(u.screenable()) == 2


def test_arabic_name_is_preserved_verbatim() -> None:
    u = parse_universe(_raw())
    assert u.get("AAAA") is not None
    assert u.get("AAAA").name_ar == "ألفا"  # type: ignore[union-attr]


def test_off_index_watch_is_screenable_but_flagged() -> None:
    u = parse_universe(_raw(off_index_watch=[{"ticker": "CCCC", "name_en": "Gamma"}]))
    assert u.is_off_index("CCCC") is True
    assert u.is_off_index("AAAA") is False
    assert u.get("CCCC") is not None
    assert [c.ticker for c in u.screenable()] == ["AAAA", "BBBB", "CCCC"]


def test_stale_universe_is_still_usable() -> None:
    u = parse_universe(_raw(status="STALE"))
    u.require_available()  # a stale list is a dated list, not an absent one
    assert u.available is True


# ----------------------------------------------------------------------
# Refusals
# ----------------------------------------------------------------------
def test_populated_but_empty_is_a_contradiction() -> None:
    with pytest.raises(UniverseConfigError, match="no constituents are listed"):
        parse_universe(_raw(constituents=[]))


def test_populated_without_provenance_is_refused() -> None:
    with pytest.raises(UniverseConfigError, match="provenance"):
        parse_universe(_raw(retrieved={"at": None, "from": None, "by": None}))


def test_partial_transcription_is_refused() -> None:
    raw = _raw()
    raw["index"] = {"name": "EGX 33 Shariah Index", "constituent_count": 33}
    with pytest.raises(UniverseConfigError, match="33 constituents but 2 were transcribed"):
        parse_universe(raw)


def test_constituents_without_a_status_change_are_refused() -> None:
    with pytest.raises(UniverseConfigError, match="AWAITING_CONSTITUENTS"):
        parse_universe(_raw(status="AWAITING_CONSTITUENTS"))


def test_duplicate_ticker_is_refused() -> None:
    raw = _raw()
    raw["constituents"] = [
        {"ticker": "AAAA", "name_en": "Alpha"},
        {"ticker": "AAAA", "name_en": "Alpha again"},
    ]
    with pytest.raises(UniverseConfigError, match="appears twice"):
        parse_universe(raw)


def test_ticker_duplicated_across_index_and_watch_is_refused() -> None:
    with pytest.raises(UniverseConfigError, match="appears twice"):
        parse_universe(_raw(off_index_watch=[{"ticker": "AAAA", "name_en": "Alpha"}]))


def test_unknown_status_is_refused() -> None:
    with pytest.raises(UniverseConfigError, match="unknown universe status"):
        parse_universe(_raw(status="PROBABLY_FINE"))


def test_constituent_without_ticker_is_refused() -> None:
    with pytest.raises(UniverseConfigError, match="must have a ticker"):
        parse_universe(_raw(constituents=[{"name_en": "No ticker"}]))


def test_constituent_without_any_name_is_refused() -> None:
    raw = _raw()
    raw["index"] = {"name": "X", "constituent_count": 1}
    raw["constituents"] = [{"ticker": "AAAA"}]
    with pytest.raises(UniverseConfigError, match="name in at least one language"):
        parse_universe(raw)


def test_non_mapping_entries_are_refused() -> None:
    with pytest.raises(UniverseConfigError, match="must be a mapping"):
        parse_universe(_raw(constituents=["AAAA", "BBBB"]))
    with pytest.raises(UniverseConfigError, match="`index` must be a mapping"):
        parse_universe(_raw(index=["nope"]))
    with pytest.raises(UniverseConfigError, match="`retrieved` must be a mapping"):
        parse_universe(_raw(retrieved="yesterday"))


def test_missing_index_count_skips_the_count_check() -> None:
    u = parse_universe(_raw(index={"name": "Ad-hoc list"}))
    assert u.expected_count is None
    assert u.available is True


# ----------------------------------------------------------------------
# Availability edge cases constructed directly
# ----------------------------------------------------------------------
def test_hand_built_universe_missing_provenance_refuses_at_use_time() -> None:
    u = Universe(
        version="0",
        status=UniverseStatus.POPULATED,
        index_name="X",
        expected_count=None,
        constituents=(Constituent("AAAA", "Alpha"),),
        off_index_watch=(),
        retrieved=Provenance(),
    )
    assert u.available is False
    with pytest.raises(UniverseUnavailableError, match="no retrieval provenance"):
        u.require_available()


def test_hand_built_populated_but_empty_refuses_at_use_time() -> None:
    u = Universe(
        version="0",
        status=UniverseStatus.POPULATED,
        index_name="X",
        expected_count=None,
        constituents=(),
        off_index_watch=(),
        retrieved=Provenance("2026-07-27", "https://example.test", "user"),
    )
    with pytest.raises(UniverseUnavailableError, match="contradicts itself"):
        u.require_available()
