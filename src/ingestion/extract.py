"""Dual independent extraction (EXTRACTION_SPEC §2, §7).

This is the only place an LLM touches a financial number. Everything here is
built so that the model's output is *evidence*, not truth: it is parsed
strictly, rejected if it lacks provenance, and reconciled against a second
independent pass before any value is stored.

    Extraction is allowed to fail. Extraction is not allowed to be wrong.

Independence between passes comes from framing, not from a different model
(§2):
  * Pass 1 — statement-by-statement, whole statements chunked together
  * Pass 2 — line-item-by-line-item lookup, different page chunking
  * neither pass sees the other's output

The model client is injected as a :class:`LLMClient` protocol so the prompt
contract and the parser are testable without network access or an API key.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from ingestion.normalise import (
    NormalisationError,
    UnitScale,
    normalise_value,
    parse_number,
)

PROMPT_VERSION = "1.0.0"

# Response contract (§7). A value without a page number is rejected before it
# can reach the database, mirroring the NOT NULL provenance columns (R1).
_REQUIRED_ITEM_FIELDS = ("key", "raw_caption", "raw_value", "page_no", "statement")


class ExtractionError(RuntimeError):
    """Raised when a model response violates the extraction contract."""


@dataclass(frozen=True)
class ExtractedItem:
    """One candidate value with its mandatory provenance."""

    key: str
    raw_caption: str
    raw_value: str
    page_no: int
    statement: str
    note_ref: str | None = None

    def __post_init__(self) -> None:
        if self.page_no <= 0:
            raise ExtractionError(f"{self.key}: page_no must be > 0 (got {self.page_no})")
        if not self.raw_caption.strip():
            raise ExtractionError(f"{self.key}: raw_caption must be the caption exactly as printed")
        if not self.raw_value.strip():
            raise ExtractionError(f"{self.key}: raw_value must be the string as printed")


@dataclass(frozen=True)
class ExtractionResult:
    """A single pass's parsed output."""

    pass_number: int
    model: str
    prompt_version: str
    unit_scale_header_text: str
    items: tuple[ExtractedItem, ...]
    not_found: tuple[str, ...]
    raw_response: str = field(repr=False, default="")

    def by_key(self) -> dict[str, ExtractedItem]:
        return {item.key: item for item in self.items}


class LLMClient(Protocol):
    """Minimal model interface. Implementations live at the process boundary."""

    def complete(self, *, system: str, user: str) -> str: ...

    @property
    def model_name(self) -> str: ...


# ----------------------------------------------------------------------
# Prompt construction (§7 requirements 1-7)
# ----------------------------------------------------------------------
_SYSTEM_PROMPT = """\
You extract figures from Egyptian Exchange (EGX) financial statements.

You are an evidence-gathering instrument, not an analyst. Obey these rules
without exception:

1. PROVENANCE. Every value you return MUST carry the page number it was read
   from. A value without a page number is discarded.
2. VERBATIM CAPTION. Return `raw_caption` exactly as printed in the document,
   in its original language (Arabic or English). Do not translate it.
3. VERBATIM VALUE. Return `raw_value` exactly as printed, before any
   normalisation: keep separators, brackets and any trailing minus sign.
   Do not convert Arabic-Indic digits. Do not apply the unit scale.
4. NEVER INFER. If a line item is not present in this document, put its key in
   `not_found`. Never compute it from other lines, never derive it, never use
   prior knowledge of the company. A missing figure is useful information; a
   guessed figure is a defect.
5. THIS DOCUMENT ONLY. You can see only this document. Do not use knowledge of
   other filings, other periods, or the company generally.
6. STRICT JSON. Return one JSON object and nothing else: no prose, no
   explanation, no markdown fences.
7. HEADER TEXT. Return `unit_scale_header_text` copied verbatim from the
   statement header that declares the currency and scale (for example
   "بالألف جنيه مصري"). Do not interpret it — the caller determines the scale
   independently of your reading.

Response shape:

{
  "unit_scale_header_text": "<verbatim header text>",
  "items": [
    {
      "key": "<canonical key from the requested list>",
      "raw_caption": "<caption exactly as printed>",
      "raw_value": "<value exactly as printed>",
      "page_no": <integer, 1-based>,
      "note_ref": "<note number if the value came from a note, else null>",
      "statement": "BALANCE_SHEET|INCOME_STATEMENT|CASH_FLOW|EQUITY|NOTE"
    }
  ],
  "not_found": ["<key>", ...]
}

Every requested key must appear exactly once, either in `items` or in
`not_found`.\
"""

_PASS1_FRAMING = """\
APPROACH FOR THIS PASS — statement by statement.

Work through the document one financial statement at a time, in the order the
statements appear. For each statement, read down its face and record every
requested item you find there. After finishing the statement faces, open the
notes referenced by any line you recorded and record items that live in notes.

Interest income requires note-level work. Egyptian income statements often
aggregate interest into "other income" / "إيرادات أخرى" without breaking it out
on the statement face. Locate the other-income note, and report the interest
component you find there with that note's page number and note reference.\
"""

_PASS2_FRAMING = """\
APPROACH FOR THIS PASS — line item by line item.

Take the requested keys one at a time, in the order given. For each key,
search the whole document for that specific item: the statement faces first,
then the notes, then any supplementary schedules. Record where you found it.
Move to the next key only once you have either found the item or established
that it is absent.

Do not assume an item lives on a particular statement. If a borrowing appears
only in a note, take it from the note and record that note reference.\
"""


def build_prompt(
    *,
    pass_number: int,
    document_text: str,
    requested_keys: list[str],
    company_name: str | None = None,
) -> tuple[str, str]:
    """Return ``(system, user)`` prompts for one extraction pass.

    ``pass_number`` selects the framing that makes the two passes genuinely
    independent (§2). Passes must never see one another's output, so no prior
    result is accepted as an argument here by design.
    """
    if pass_number == 1:
        framing = _PASS1_FRAMING
    elif pass_number == 2:
        framing = _PASS2_FRAMING
    else:
        raise ValueError(f"pass_number must be 1 or 2, got {pass_number}")

    keys_block = "\n".join(f"- {k}" for k in requested_keys)
    header = f"Document for: {company_name}\n\n" if company_name else ""
    user = (
        f"{framing}\n\n"
        f"REQUESTED KEYS\n{keys_block}\n\n"
        f"DOCUMENT\n{header}<<<BEGIN DOCUMENT>>>\n{document_text}\n<<<END DOCUMENT>>>"
    )
    return _SYSTEM_PROMPT, user


# ----------------------------------------------------------------------
# Strict response parsing (§7)
# ----------------------------------------------------------------------
def parse_response(raw: str, pass_number: int, model: str) -> ExtractionResult:
    """Parse and validate a model response against the §7 contract.

    Rejects the whole response on malformed JSON. Rejects individual items that
    lack provenance — a value without a page number never reaches the database.
    """
    text = _strip_fences(raw)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ExtractionError(f"response is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ExtractionError("response JSON must be an object")

    header_text = payload.get("unit_scale_header_text")
    if not isinstance(header_text, str):
        raise ExtractionError("unit_scale_header_text is required and must be a string")

    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise ExtractionError("items must be a list")

    items: list[ExtractedItem] = []
    for entry in raw_items:
        if not isinstance(entry, dict):
            raise ExtractionError(f"item must be an object, got {type(entry).__name__}")
        missing = [f for f in _REQUIRED_ITEM_FIELDS if entry.get(f) is None]
        if missing:
            raise ExtractionError(
                f"item {entry.get('key', '<unknown>')!r} missing required field(s): {', '.join(missing)}"
            )
        page_no = entry["page_no"]
        if not isinstance(page_no, int) or isinstance(page_no, bool):
            raise ExtractionError(f"item {entry['key']!r}: page_no must be an integer")
        items.append(
            ExtractedItem(
                key=str(entry["key"]),
                raw_caption=str(entry["raw_caption"]),
                raw_value=str(entry["raw_value"]),
                page_no=page_no,
                statement=str(entry["statement"]),
                note_ref=None if entry.get("note_ref") is None else str(entry["note_ref"]),
            )
        )

    not_found = payload.get("not_found", [])
    if not isinstance(not_found, list):
        raise ExtractionError("not_found must be a list")

    duplicates = _duplicates([i.key for i in items])
    if duplicates:
        raise ExtractionError(f"duplicate keys in items: {', '.join(sorted(duplicates))}")

    return ExtractionResult(
        pass_number=pass_number,
        model=model,
        prompt_version=PROMPT_VERSION,
        unit_scale_header_text=header_text,
        items=tuple(items),
        not_found=tuple(str(k) for k in not_found),
        raw_response=raw,
    )


def _strip_fences(raw: str) -> str:
    """Tolerate markdown fences even though the contract forbids them."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _duplicates(values: list[str]) -> set[str]:
    seen: set[str] = set()
    dupes: set[str] = set()
    for v in values:
        if v in seen:
            dupes.add(v)
        seen.add(v)
    return dupes


def check_coverage(result: ExtractionResult, requested_keys: list[str]) -> list[str]:
    """Return requested keys the pass accounted for in neither list.

    A silent omission is ambiguity; an explicit ``not_found`` is information
    (§7). Unaccounted keys are surfaced so the caller can treat them as gaps.
    """
    accounted = {i.key for i in result.items} | set(result.not_found)
    return [k for k in requested_keys if k not in accounted]


# ----------------------------------------------------------------------
# Running a pass
# ----------------------------------------------------------------------
def run_pass(
    client: LLMClient,
    *,
    pass_number: int,
    document_text: str,
    requested_keys: list[str],
    company_name: str | None = None,
) -> ExtractionResult:
    """Execute one independent extraction pass through ``client``."""
    system, user = build_prompt(
        pass_number=pass_number,
        document_text=document_text,
        requested_keys=requested_keys,
        company_name=company_name,
    )
    raw = client.complete(system=system, user=user)
    return parse_response(raw, pass_number, client.model_name)


# ----------------------------------------------------------------------
# Normalising a pass into comparable values
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class NormalisedPass:
    """Values normalised to EGP units, plus the items that refused to parse."""

    values: dict[str, Decimal]
    provenance: dict[str, ExtractedItem]
    unparseable: dict[str, str]


def normalise_pass(result: ExtractionResult, scale: UnitScale) -> NormalisedPass:
    """Convert a pass's raw strings into EGP-unit Decimals.

    Items whose ``raw_value`` cannot be parsed unambiguously are collected in
    ``unparseable`` rather than dropped or guessed — the caller routes those to
    DATA_INSUFFICIENT (R3).
    """
    values: dict[str, Decimal] = {}
    provenance: dict[str, ExtractedItem] = {}
    unparseable: dict[str, str] = {}

    for item in result.items:
        try:
            values[item.key] = normalise_value(item.raw_value, scale)
            provenance[item.key] = item
        except NormalisationError as exc:
            unparseable[item.key] = f"{item.raw_value!r}: {exc}"

    return NormalisedPass(values=values, provenance=provenance, unparseable=unparseable)


def scale_disagreement(
    header_text_pass1: str,
    header_text_pass2: str,
) -> bool:
    """True when the two passes copied different scale headers.

    Distinct header text means at least one pass misread the statement header,
    which is exactly the setup for a 1000x error (§5.2).
    """
    return header_text_pass1.strip() != header_text_pass2.strip()


def parse_shares_outstanding(raw: str) -> Decimal:
    """Share counts are never scaled by the statement's currency scale.

    A separate helper exists because applying the money scale to a share count
    is a silent, plausible-looking error.
    """
    return parse_number(raw)
