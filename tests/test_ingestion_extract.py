"""Extraction prompt contract and strict response parsing (§2, §7)."""

from __future__ import annotations

import json

import pytest

from conftest import D
from ingestion.extract import (
    ExtractionError,
    build_prompt,
    check_coverage,
    normalise_pass,
    parse_response,
    parse_shares_outstanding,
    run_pass,
    scale_disagreement,
)
from ingestion.normalise import UnitScale

KEYS = ["total_assets", "total_revenue", "interest_income"]


def _payload(**overrides):
    base = {
        "unit_scale_header_text": "بالألف جنيه مصري",
        "items": [
            {
                "key": "total_assets",
                "raw_caption": "إجمالي الأصول",
                "raw_value": "12,345",
                "page_no": 4,
                "note_ref": None,
                "statement": "BALANCE_SHEET",
            }
        ],
        "not_found": ["treasury_bills_and_bonds"],
    }
    base.update(overrides)
    return json.dumps(base, ensure_ascii=False)


class FakeClient:
    def __init__(self, response: str) -> None:
        self._response = response
        self.calls: list[tuple[str, str]] = []

    def complete(self, *, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self._response

    @property
    def model_name(self) -> str:
        return "fake-model"


# ---- §7 prompt requirements -----------------------------------------
def test_prompt_states_all_seven_requirements():
    system, _ = build_prompt(pass_number=1, document_text="doc", requested_keys=KEYS)
    lowered = system.lower()
    assert "page number" in lowered            # 1 provenance
    assert "raw_caption" in system             # 2 verbatim caption
    assert "raw_value" in system               # 3 verbatim value
    assert "never infer" in lowered            # 4 no inference
    assert "only this document" in lowered     # 5 no cross-filing knowledge
    assert "strict json" in lowered            # 6 strict JSON
    assert "unit_scale_header_text" in system  # 7 header text


def test_prompt_forbids_markdown_fences():
    system, _ = build_prompt(pass_number=1, document_text="doc", requested_keys=KEYS)
    assert "markdown" in system.lower()


def test_passes_use_different_framing():
    _, user1 = build_prompt(pass_number=1, document_text="doc", requested_keys=KEYS)
    _, user2 = build_prompt(pass_number=2, document_text="doc", requested_keys=KEYS)
    assert user1 != user2
    assert "statement by statement" in user1.lower()
    assert "line item by line item" in user2.lower()


def test_pass1_requires_note_level_interest_work():
    _, user1 = build_prompt(pass_number=1, document_text="doc", requested_keys=KEYS)
    assert "other income" in user1.lower()
    assert "note" in user1.lower()


def test_requested_keys_listed_in_prompt():
    _, user = build_prompt(pass_number=1, document_text="doc", requested_keys=KEYS)
    for k in KEYS:
        assert k in user


def test_invalid_pass_number_rejected():
    with pytest.raises(ValueError):
        build_prompt(pass_number=3, document_text="doc", requested_keys=KEYS)


# ---- strict parsing --------------------------------------------------
def test_parse_valid_response():
    r = parse_response(_payload(), 1, "m")
    assert r.unit_scale_header_text == "بالألف جنيه مصري"
    assert len(r.items) == 1
    assert r.items[0].page_no == 4
    assert r.not_found == ("treasury_bills_and_bonds",)


def test_parse_tolerates_markdown_fences():
    fenced = "```json\n" + _payload() + "\n```"
    assert len(parse_response(fenced, 1, "m").items) == 1


def test_malformed_json_rejected():
    with pytest.raises(ExtractionError):
        parse_response("not json at all", 1, "m")


def test_non_object_json_rejected():
    with pytest.raises(ExtractionError):
        parse_response("[1,2,3]", 1, "m")


def test_missing_header_text_rejected():
    payload = json.loads(_payload())
    del payload["unit_scale_header_text"]
    with pytest.raises(ExtractionError):
        parse_response(json.dumps(payload), 1, "m")


def test_item_without_page_number_rejected():
    """A value without provenance never reaches the database (R1)."""
    payload = json.loads(_payload())
    payload["items"][0]["page_no"] = None
    with pytest.raises(ExtractionError, match="page_no"):
        parse_response(json.dumps(payload), 1, "m")


def test_item_with_zero_page_rejected():
    payload = json.loads(_payload())
    payload["items"][0]["page_no"] = 0
    with pytest.raises(ExtractionError):
        parse_response(json.dumps(payload), 1, "m")


def test_item_with_non_integer_page_rejected():
    payload = json.loads(_payload())
    payload["items"][0]["page_no"] = "four"
    with pytest.raises(ExtractionError):
        parse_response(json.dumps(payload), 1, "m")


def test_item_missing_caption_rejected():
    payload = json.loads(_payload())
    payload["items"][0]["raw_caption"] = "  "
    with pytest.raises(ExtractionError):
        parse_response(json.dumps(payload), 1, "m")


def test_duplicate_keys_rejected():
    payload = json.loads(_payload())
    payload["items"].append(dict(payload["items"][0]))
    with pytest.raises(ExtractionError, match="duplicate"):
        parse_response(json.dumps(payload), 1, "m")


def test_items_must_be_list():
    with pytest.raises(ExtractionError):
        parse_response(json.dumps({"unit_scale_header_text": "x", "items": {}}), 1, "m")


def test_not_found_must_be_list():
    payload = json.loads(_payload())
    payload["not_found"] = "nope"
    with pytest.raises(ExtractionError):
        parse_response(json.dumps(payload), 1, "m")


# ---- coverage --------------------------------------------------------
def test_coverage_reports_unaccounted_keys():
    r = parse_response(_payload(), 1, "m")
    # total_assets in items; total_revenue and interest_income accounted nowhere
    assert check_coverage(r, KEYS) == ["total_revenue", "interest_income"]


def test_coverage_empty_when_all_accounted():
    payload = json.loads(_payload())
    payload["not_found"] = ["total_revenue", "interest_income"]
    r = parse_response(json.dumps(payload), 1, "m")
    assert check_coverage(r, KEYS) == []


# ---- running a pass --------------------------------------------------
def test_run_pass_uses_client():
    client = FakeClient(_payload())
    r = run_pass(client, pass_number=2, document_text="doc", requested_keys=KEYS)
    assert r.model == "fake-model"
    assert r.pass_number == 2
    assert len(client.calls) == 1


# ---- normalising a pass ---------------------------------------------
def test_normalise_pass_applies_scale():
    r = parse_response(_payload(), 1, "m")
    n = normalise_pass(r, UnitScale.THOUSANDS)
    assert n.values["total_assets"] == D("12345000")
    assert n.provenance["total_assets"].page_no == 4


def test_normalise_pass_collects_unparseable_without_guessing():
    payload = json.loads(_payload())
    payload["items"][0]["raw_value"] = "n/a"
    r = parse_response(json.dumps(payload), 1, "m")
    n = normalise_pass(r, UnitScale.THOUSANDS)
    assert "total_assets" not in n.values
    assert "total_assets" in n.unparseable


# ---- scale disagreement ---------------------------------------------
def test_scale_disagreement_detected():
    assert scale_disagreement("بالألف جنيه مصري", "بالمليون") is True
    assert scale_disagreement("بالألف جنيه مصري", " بالألف جنيه مصري ") is False


def test_shares_outstanding_not_money_scaled():
    assert parse_shares_outstanding("1,234,567") == D("1234567")
