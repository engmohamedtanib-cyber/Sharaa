"""The investable universe: the refusal path, and the real file (M0).

The most important test in this module is `test_empty_universe_raises_rather_than
_returning_nothing`. An unpopulated universe and a universe in which nothing
passed screening both produce an empty list, and they demand opposite actions.
If that distinction ever collapses, the engine reports an all-clear it never
computed — the exact failure `CLAUDE.md` exists to prevent.
"""

from __future__ import annotations

import copy
from decimal import Decimal

import pytest
import yaml

from config_loader import CONFIG_DIR, load_universe
from engine.universe import (
    AWAITING_CONSTITUENTS,
    POPULATED,
    Constituent,
    Provenance,
    Universe,
    UniverseIntegrityError,
    UniverseUnavailableError,
    parse_universe,
)

# ======================================================================
# Builders
# ======================================================================
ROW = {
    "ticker": "TMGH",
    "isin": "EGS691S1C011",
    "name_en": "T M G Holding",
    "name_ar": "مجموعة طلعت مصطفى القابضة",
    "reuters_code": "TMGH.CA",
    "issuer_id": "TMGH",
    "index_weight": "0.15000000",
    "sector": None,
}


def row(**overrides):
    return {**copy.deepcopy(ROW), **overrides}


def doc(constituents=None, **overrides):
    base = {
        "version": "1.0.0",
        "status": POPULATED,
        "index": {"code": "EGX33-SHARIAH", "constituent_count": 1},
        "retrieved": {"at": "2026-07-25", "from": "workbook.xlsx", "by": "user upload"},
        "constituents": [row()] if constituents is None else constituents,
    }
    return {**base, **overrides}


# ======================================================================
# The refusal — an unpopulated universe is not an empty result set
# ======================================================================
def test_empty_universe_raises_rather_than_returning_nothing():
    with pytest.raises(UniverseUnavailableError) as exc:
        parse_universe({"status": AWAITING_CONSTITUENTS, "constituents": []})
    assert "no company passed screening" in str(exc.value)


def test_populated_but_empty_list_still_refuses():
    """The dangerous case: a status that says ready over a list that is not."""
    with pytest.raises(UniverseUnavailableError, match="constituent list is empty"):
        parse_universe(doc(constituents=[]))


@pytest.mark.parametrize("status", [None, "", "AWAITING_CONSTITUENTS", "populated", "DRAFT"])
def test_only_the_exact_populated_status_is_accepted(status):
    with pytest.raises(UniverseUnavailableError):
        parse_universe(doc(status=status))


def test_unavailable_is_not_an_integrity_error():
    """Callers may reasonably surface these two differently; they must not be
    the same class, and neither may be caught as a generic ValueError."""
    assert not issubclass(UniverseUnavailableError, UniverseIntegrityError)
    assert not issubclass(UniverseUnavailableError, ValueError)


# ======================================================================
# Provenance is mandatory (R1)
# ======================================================================
@pytest.mark.parametrize("field_name", ["at", "from", "by"])
def test_populated_universe_without_provenance_is_rejected(field_name):
    bad = doc()
    bad["retrieved"][field_name] = None
    with pytest.raises(UniverseIntegrityError, match=field_name):
        parse_universe(bad)


def test_missing_retrieved_block_is_rejected():
    with pytest.raises(UniverseIntegrityError, match="provenance"):
        parse_universe(doc(retrieved=None))


def test_non_mapping_retrieved_block_is_rejected():
    with pytest.raises(UniverseIntegrityError, match="must be a mapping"):
        parse_universe(doc(retrieved=["2026-07-25"]))


def test_sha256_is_optional_but_preserved():
    assert parse_universe(doc()).retrieved.sha256 is None
    with_hash = doc()
    with_hash["retrieved"]["sha256"] = "abc123"
    assert parse_universe(with_hash).retrieved.sha256 == "abc123"


# ======================================================================
# Row integrity
# ======================================================================
@pytest.mark.parametrize("field_name", ["ticker", "isin", "name_en", "name_ar", "reuters_code", "issuer_id"])
def test_every_required_field_must_be_present(field_name):
    with pytest.raises(UniverseIntegrityError, match=field_name):
        parse_universe(doc(constituents=[row(**{field_name: None})]))


def test_non_mapping_row_is_rejected():
    with pytest.raises(UniverseIntegrityError, match="not a mapping"):
        parse_universe(doc(constituents=["TMGH"]))


def test_duplicate_ticker_is_rejected():
    dupe = doc(
        constituents=[row(), row(isin="EGS48031C016")],
        index={"code": "X", "constituent_count": 1},
    )
    with pytest.raises(UniverseIntegrityError, match="duplicate ticker"):
        parse_universe(dupe)


def test_duplicate_isin_is_rejected():
    dupe = doc(
        constituents=[row(), row(ticker="ETEL", reuters_code="ETEL.CA", issuer_id="ETEL")],
        index={"code": "X", "constituent_count": 2},
    )
    with pytest.raises(UniverseIntegrityError, match="duplicate isin"):
        parse_universe(dupe)


# ======================================================================
# Weights — informational, but not allowed to be impossible
# ======================================================================
def test_weight_is_parsed_as_decimal_never_float():
    parsed = parse_universe(doc()).constituents[0]
    assert isinstance(parsed.index_weight, Decimal)
    assert parsed.index_weight == Decimal("0.15")


def test_absent_weight_is_none_not_zero():
    assert parse_universe(doc(constituents=[row(index_weight=None)])).constituents[0].index_weight is None


@pytest.mark.parametrize("bad", ["0", "-0.01", "1.0001"])
def test_impossible_weight_is_rejected(bad):
    with pytest.raises(UniverseIntegrityError, match="outside"):
        parse_universe(doc(constituents=[row(index_weight=bad)]))


def test_weight_above_the_cap_is_allowed():
    """Weights drift with price between rebalances. A post-rebalance weight above
    the 15% cap is a real published state, not a corrupt file — refusing to load
    the whole universe over it would be a self-inflicted outage."""
    parsed = parse_universe(doc(constituents=[row(index_weight="0.1731")]))
    assert parsed.constituents[0].index_weight == Decimal("0.1731")


# ======================================================================
# Listings vs issuers — the dual-listing distinction (decisions/0005)
# ======================================================================
def two_listings_one_issuer():
    return doc(
        constituents=[
            row(ticker="FAIT", isin="EGS60321C014", reuters_code="FAIT.CA", issuer_id="FAIT"),
            row(ticker="FAITA", isin="EGS60322C012", reuters_code="FAITA.CA", issuer_id="FAIT"),
        ],
        index={"code": "EGX33-SHARIAH", "constituent_count": 1},
    )


def test_two_listings_of_one_issuer_count_as_one_company():
    u = parse_universe(two_listings_one_issuer())
    assert len(u.constituents) == 2
    assert u.issuers == ("FAIT",)
    assert len(u.listings_of("FAIT")) == 2


def test_listings_of_an_unknown_issuer_is_empty():
    assert parse_universe(doc()).listings_of("NOPE") == ()


def test_issuer_count_mismatch_is_rejected():
    """A rebalance that adds or drops a company must fail loudly at load rather
    than be absorbed as a silent transcription error."""
    wrong = doc(index={"code": "EGX33-SHARIAH", "constituent_count": 33})
    with pytest.raises(UniverseIntegrityError, match="declares 33"):
        parse_universe(wrong)


def test_absent_declared_count_skips_the_check():
    parsed = parse_universe(doc(index={"code": "EGX33-SHARIAH"}))
    assert len(parsed.issuers) == 1


def test_missing_index_block_leaves_code_empty():
    assert parse_universe(doc(index=None)).index_code == ""


# ======================================================================
# Lookup surface
# ======================================================================
def test_tickers_preserve_file_order():
    u = parse_universe(
        doc(
            constituents=[row(), row(ticker="ETEL", isin="E2", reuters_code="ETEL.CA", issuer_id="ETEL")],
            index={"code": "X", "constituent_count": 2},
        )
    )
    assert u.tickers == ("TMGH", "ETEL")


def test_issuers_preserve_first_appearance_order():
    u = parse_universe(
        doc(
            constituents=[
                row(ticker="A", isin="I1", reuters_code="A.CA", issuer_id="A"),
                row(ticker="B", isin="I2", reuters_code="B.CA", issuer_id="B"),
                row(ticker="A2", isin="I3", reuters_code="A2.CA", issuer_id="A"),
            ],
            index={"code": "X", "constituent_count": 2},
        )
    )
    assert u.issuers == ("A", "B")


def test_is_constituent_and_get():
    u = parse_universe(doc())
    assert u.is_constituent("TMGH")
    assert not u.is_constituent("COMI")
    assert u.get("TMGH").name_en == "T M G Holding"


def test_get_raises_for_a_non_constituent():
    """A company outside the index must not be silently resolvable. It needs the
    full Screen A-E gate with no index membership to lean on."""
    with pytest.raises(KeyError, match="COMI"):
        parse_universe(doc()).get("COMI")


def test_off_index_watch_defaults_to_empty():
    assert parse_universe(doc()).off_index_watch == ()
    assert parse_universe(doc(off_index_watch=["COMI"])).off_index_watch == ("COMI",)


def test_universe_is_frozen():
    u = parse_universe(doc())
    for target, attr in ((u, "version"), (u.constituents[0], "ticker"), (u.retrieved, "at")):
        with pytest.raises(Exception):  # noqa: B017  (FrozenInstanceError)
            setattr(target, attr, "mutated")


def test_dataclass_defaults():
    c = Constituent("T", "I", "en", "ar", "T.CA", "T")
    assert c.index_weight is None and c.sector is None
    assert Provenance("2026-07-25", "src", "me").sha256 is None
    assert Universe("1", "X", Provenance("a", "b", "c"), ()).off_index_watch == ()


# ======================================================================
# The real config/universe.yaml (M0 definition of done)
# ======================================================================
@pytest.fixture(scope="module")
def real() -> Universe:
    return load_universe()


def test_the_shipped_universe_loads(real):
    assert real.index_code == "EGX33-SHARIAH"


def test_the_shipped_universe_holds_33_issuers_across_34_listings(real):
    assert len(real.issuers) == 33
    assert len(real.constituents) == 34


def test_faisal_islamic_bank_is_one_issuer_in_two_currencies(real):
    listings = real.listings_of("FAIT")
    assert {c.ticker for c in listings} == {"FAIT", "FAITA"}
    assert all("Faisal Islamic Bank" in c.name_en for c in listings)


def test_shipped_provenance_is_recorded(real):
    assert real.retrieved.sha256 == "1ad43debdb1e626650837ac58a483f81bdccbad52b4ab04a75102c6d14b7470c"
    assert "EGX33-SHARIAH_constituents_2026-05.xlsx" in real.retrieved.source


def test_every_shipped_sector_is_null(real):
    """The source export carries no sector column. Until a primary source for
    sector is opened, every sector stays null — a null is a known gap, a guessed
    sector is an unsourced fact in the file that must contain only sourced ones."""
    assert all(c.sector is None for c in real.constituents)


def test_every_shipped_row_carries_an_egyptian_isin_and_a_ca_ric(real):
    for c in real.constituents:
        assert c.isin.startswith("EG") and len(c.isin) == 12, c.isin
        assert c.reuters_code == f"{c.ticker}.CA", c.reuters_code


def test_shipped_arabic_names_are_preserved_not_transliterated(real):
    """CLAUDE.md §7: Arabic is stored as UTF-8, verbatim."""
    assert any("؀" <= ch <= "ۿ" for c in real.constituents for ch in c.name_ar)


def test_shipped_weights_sum_to_one(real):
    total = sum((c.index_weight for c in real.constituents), start=Decimal(0))
    assert abs(total - Decimal(1)) <= Decimal("0.000001")


def test_shipped_file_declares_populated_status():
    raw = yaml.safe_load((CONFIG_DIR / "universe.yaml").read_text(encoding="utf-8"))
    assert raw["status"] == POPULATED
    assert raw["index"]["constituent_count"] == 33
