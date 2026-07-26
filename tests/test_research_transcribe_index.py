"""Transcribing an exchange constituents export into config/universe.yaml (M0).

These tests defend one property: the transcriber either reproduces the source
faithfully or stops. It never repairs, guesses or partially transcribes — a
half-read constituent list is how a company ends up in a halal universe on no
evidence.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from research.transcribers.transcribe_index import (
    EXPECTED_HEADERS,
    SourceRow,
    TranscriptionError,
    issuer_id,
    parse_as_of,
    parse_rows,
    read_workbook,
    render_constituents,
    sha256_of,
    ticker_of,
)

SOURCE = "memory/evidence/egx/shariah_index/2026-04-30/constituents.xlsx"
SHA256 = "1ad43debdb1e626650837ac58a483f81bdccbad52b4ab04a75102c6d14b7470c"

HEADER = (*EXPECTED_HEADERS, "Weight as of 30/04/2026")


def sheet(*rows, header=HEADER):
    return [header, *rows]


def data_row(isin="EGS691S1C011", ar="مجموعة", en="TMG", ric="TMGH.CA  ", weight=1.0):
    return (isin, ar, en, ric, weight)


# ======================================================================
# Header handling
# ======================================================================
def test_as_of_date_is_read_from_the_header_not_assumed():
    assert parse_as_of("Weight as of 30/04/2026") == "2026-04-30"


@pytest.mark.parametrize("header", [None, "", "Weight", "Weight as of April 2026", "Weight as of 2026-04-30"])
def test_an_unreadable_as_of_date_stops_the_run(header):
    """A weight without its as-of date is uninterpretable, so guessing one is
    worse than failing."""
    with pytest.raises(TranscriptionError, match="refusing to guess"):
        parse_as_of(header)


def test_a_changed_column_layout_stops_the_run():
    bad = sheet(data_row(), header=("ISIN", "AR", "EN", "RIC", "Weight as of 30/04/2026"))
    with pytest.raises(TranscriptionError, match="unexpected column layout"):
        parse_rows(bad)


def test_a_missing_weight_column_stops_the_run():
    with pytest.raises(TranscriptionError, match="at least 5 columns"):
        parse_rows([EXPECTED_HEADERS])


def test_an_empty_worksheet_stops_the_run():
    with pytest.raises(TranscriptionError, match="empty"):
        parse_rows([])


def test_a_header_with_no_constituents_stops_the_run():
    with pytest.raises(TranscriptionError, match="no constituents"):
        parse_rows(sheet())


# ======================================================================
# Row handling
# ======================================================================
def test_weights_are_decimal_and_quantised_away_from_float_noise():
    """Excel delivers an exact 15% cap as 0.1500000000000203."""
    out = parse_rows(sheet(data_row(weight=0.1500000000000203)))
    assert out.rows[0].weight == Decimal("0.15000000")
    assert isinstance(out.rows[0].weight, Decimal)


def test_the_totals_row_is_used_as_a_cross_check():
    out = parse_rows(sheet(data_row(weight=0.6), data_row(isin="I2", ric="ETEL.CA", weight=0.4), (None,) * 4 + (1.0,)))
    assert out.declared_total == Decimal(1)
    assert len(out.rows) == 2


def test_a_sum_that_disagrees_with_the_totals_row_stops_the_run():
    """The single check that catches a truncated or misaligned transcription."""
    truncated = sheet(data_row(weight=0.6), (None,) * 4 + (1.0,))
    with pytest.raises(TranscriptionError, match="transcription is incomplete or misaligned"):
        parse_rows(truncated)


def test_a_row_with_no_identifier_and_no_weight_stops_the_run():
    with pytest.raises(TranscriptionError, match="not a total row"):
        parse_rows(sheet(data_row(), (None, None, None, None, None)))


def test_a_second_blank_identifier_row_stops_the_run():
    rows = sheet(data_row(weight=1.0), (None,) * 4 + (1.0,), (None,) * 4 + (1.0,))
    with pytest.raises(TranscriptionError, match="not a total row"):
        parse_rows(rows)


def test_a_constituent_without_a_weight_stops_the_run():
    with pytest.raises(TranscriptionError, match="has no weight"):
        parse_rows(sheet((*data_row()[:4], None)))


def test_a_constituent_without_a_reuters_code_stops_the_run():
    with pytest.raises(TranscriptionError, match="has no REUTERS_CODE"):
        parse_rows(sheet(data_row(ric="   ")))


def test_short_rows_do_not_crash_the_parser():
    with pytest.raises(TranscriptionError, match="has no weight"):
        parse_rows(sheet(("EGS691S1C011", "ar", "en", "TMGH.CA")))


# ======================================================================
# Tickers and issuers
# ======================================================================
@pytest.mark.parametrize(
    ("ric", "expected"),
    [("TMGH.CA", "TMGH"), ("TMGH.CA   ", "TMGH"), ("  etel.ca ", "ETEL"), ("ORAS", "ORAS")],
)
def test_ticker_strips_whitespace_and_the_venue_suffix(ric, expected):
    assert ticker_of(ric) == expected


def test_a_listing_is_its_own_issuer_unless_declared_otherwise():
    assert issuer_id("TMGH.CA") == "TMGH"


def test_the_usd_listing_maps_to_the_egp_issuer():
    """Both source rows name themselves as Faisal Islamic Bank of Egypt, one in
    EGP and one in USD. Two listings, one bank, one position slot."""
    assert issuer_id("FAITA.CA") == "FAIT"
    assert issuer_id("FAIT.CA") == "FAIT"


# ======================================================================
# Rendering
# ======================================================================
def test_rendered_yaml_quotes_names_and_nulls_sector():
    out = parse_rows(sheet(data_row(ar="شركة, م", en='He said "hi"')))
    text = render_constituents(out)
    assert 'name_ar: "شركة, م"' in text
    assert 'name_en: "He said \\"hi\\""' in text
    assert "sector: null" in text
    assert "reuters_code: TMGH.CA\n" in text


def test_rendered_yaml_reparses_to_the_same_values():
    yaml = pytest.importorskip("yaml")
    out = parse_rows(sheet(data_row(weight=0.6), data_row(isin="I2", ric="FAITA.CA", weight=0.4)))
    parsed = yaml.safe_load(render_constituents(out))["constituents"]
    assert [c["ticker"] for c in parsed] == ["TMGH", "FAITA"]
    assert parsed[1]["issuer_id"] == "FAIT"


def test_source_row_is_frozen():
    r = SourceRow("i", "ar", "en", "R.CA", Decimal("0.1"))
    with pytest.raises(Exception):  # noqa: B017  (FrozenInstanceError)
        r.weight = Decimal("0.2")


# ======================================================================
# Against the archived workbook — the file the universe was built from
# ======================================================================
@pytest.fixture(scope="module")
def archived():
    pytest.importorskip("openpyxl")
    path = Path(SOURCE)
    if not path.exists():  # pragma: no cover - the file is committed
        pytest.skip(f"{SOURCE} not present")
    return read_workbook(path), path


def test_the_archived_workbook_is_byte_identical_to_what_was_transcribed(archived):
    """If this fails, config/universe.yaml no longer describes the file it cites."""
    _, path = archived
    assert sha256_of(path) == SHA256


def test_the_archived_workbook_yields_34_listings_and_33_issuers(archived):
    transcription, _ = archived
    assert len(transcription.rows) == 34
    assert transcription.issuer_count == 33


def test_the_archived_workbook_weights_are_as_of_the_stated_date(archived):
    transcription, _ = archived
    assert transcription.as_of == "2026-04-30"
    assert transcription.declared_total == Decimal(1)


def test_retranscribing_reproduces_the_shipped_constituent_list(archived):
    """The proof that the list was read off a source and not recalled: regenerate
    it from the archived bytes and require an exact match with what shipped."""
    yaml = pytest.importorskip("yaml")
    transcription, _ = archived
    regenerated = yaml.safe_load(render_constituents(transcription))["constituents"]
    shipped = yaml.safe_load(Path("config/universe.yaml").read_text(encoding="utf-8"))["constituents"]
    assert regenerated == shipped
