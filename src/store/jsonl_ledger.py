"""Git-native append-only ledger persistence (``decisions/0002``).

The portfolio lives in a JSONL file inside the repository: one JSON object per
line, append-only, diffable by ``git diff``, and auditable forever through git
history. At this scale (one portfolio, hundreds of events) this is a better fit
than a database — see ``decisions/0002`` for the full argument.

Two properties are load-bearing:

* **Append-only by construction.** The store exposes ``append`` and reads. There
  is no update and no delete, mirroring the ``reject_mutation`` triggers in
  ``db/schema.sql``.
* **Exact decimals.** Money is serialised as a *string* and parsed with
  :class:`~decimal.Decimal`. JSON floats never touch a monetary value, which
  preserves the guarantee ``NUMERIC(24,2)`` gave us in Postgres.

Validation is not repeated here: an event is applied to the current state before
it is written, so an impossible movement is rejected by ``engine.ledger`` and
never reaches the file.
"""

from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from engine.ledger import (
    EventSource,
    EventType,
    LedgerEvent,
    PortfolioState,
    fold,
)

#: Fields serialised as strings to preserve exact decimal values.
_DECIMAL_FIELDS = ("amount", "shares", "price", "fees", "taxes", "ratio")

#: Fields written only when set, keeping lines short and diffs readable.
_OPTIONAL_FIELDS = ("ticker", "memo", "verbatim", "decision_id")


class LedgerFileError(RuntimeError):
    """The ledger file is malformed or inconsistent with itself."""


def event_to_dict(event: LedgerEvent) -> dict[str, Any]:
    """Serialise an event to a JSON-ready mapping with exact decimals."""
    out: dict[str, Any] = {
        "seq": event.seq,
        "event_type": event.event_type.value,
        "occurred_at": event.occurred_at,
        "source": event.source.value,
    }
    # Zero and absent are both omitted: a zero fee and no fee are the same fact,
    # and omitting them keeps lines short and git diffs readable.
    for name in _DECIMAL_FIELDS:
        value = getattr(event, name)
        if value is not None and value != 0:
            out[name] = str(value)
    for name in _OPTIONAL_FIELDS:
        value = getattr(event, name)
        if value:
            out[name] = value
    return out


def event_from_dict(data: dict[str, Any]) -> LedgerEvent:
    """Rebuild an event from its serialised form.

    Decimal fields are parsed from strings; a JSON number would silently
    introduce float error, so numeric input is stringified before conversion.
    """

    def dec(name: str, default: Decimal | None = None) -> Decimal | None:
        raw = data.get(name)
        if raw is None:
            return default
        return Decimal(str(raw))

    return LedgerEvent(
        seq=int(data["seq"]),
        event_type=EventType(data["event_type"]),
        occurred_at=str(data["occurred_at"]),
        amount=dec("amount"),
        ticker=data.get("ticker"),
        shares=dec("shares"),
        price=dec("price"),
        fees=dec("fees", Decimal(0)) or Decimal(0),
        taxes=dec("taxes", Decimal(0)) or Decimal(0),
        ratio=dec("ratio"),
        memo=str(data.get("memo", "")),
        source=EventSource(data.get("source", EventSource.USER_CONFIRMED.value)),
        verbatim=str(data.get("verbatim", "")),
        decision_id=data.get("decision_id"),
    )


class JsonlLedgerStore:
    """Append-only ledger backed by a JSONL file in the repository."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    # -- reads ---------------------------------------------------------
    def read_all(self) -> list[LedgerEvent]:
        """Load every event in file order.

        A missing file is an empty ledger, not an error — the first
        contribution creates it.
        """
        if not self.path.exists():
            return []
        events: list[LedgerEvent] = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    events.append(event_from_dict(json.loads(stripped)))
                except (json.JSONDecodeError, KeyError, ValueError) as exc:
                    raise LedgerFileError(
                        f"{self.path}:{line_no} is not a valid ledger event: {exc}"
                    ) from exc
        _assert_ordered(events, self.path)
        return events

    def state(self) -> PortfolioState:
        """Derive current portfolio state. The fold *is* the balance."""
        return fold(self.read_all())

    def next_seq(self) -> int:
        events = self.read_all()
        return events[-1].seq + 1 if events else 1

    # -- the only write ------------------------------------------------
    def append(self, event: LedgerEvent) -> LedgerEvent:
        """Validate against current state, then append one line.

        The caller does not choose ``seq``; the store owns fold order. Applying
        the event first means an unpayable buy or a sale of unheld shares raises
        *before* anything is written, so the file is never left inconsistent.
        """
        existing = self.read_all()
        state = fold(existing)
        numbered = replace(event, seq=state.last_seq + 1)

        fold([numbered], initial=state)  # raises on an impossible movement

        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event_to_dict(numbered), ensure_ascii=False, sort_keys=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
        return numbered

    def append_many(self, events: list[LedgerEvent]) -> list[LedgerEvent]:
        """Append several events, each validated against the running state.

        Not atomic across the batch by design: if the fourth event is
        impossible, the first three are legitimate and stay. The failure is
        raised so the caller knows where it stopped.
        """
        return [self.append(e) for e in events]


def _assert_ordered(events: list[LedgerEvent], path: Path) -> None:
    """Sequence numbers must be strictly increasing in file order."""
    previous = 0
    for event in events:
        if event.seq <= previous:
            raise LedgerFileError(
                f"{path}: seq {event.seq} follows {previous}; the ledger must be "
                "strictly increasing in file order"
            )
        previous = event.seq
