"""Append-only decision journal (R5, ``ENGINE_SPEC`` §10).

Every decision is written once, with the inputs that produced it, and never
updated. Two years from now "why did we sell SWDY?" must be answerable from this
file alone: the decision, the numbers it was made on, the falsification
condition stated at the time, and the threshold and policy versions in force.

The decision id is derived from its content, not from a counter: the same
decision written twice produces the same id, which is what makes the recording
step idempotent under a retried routine.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools.audit import digest


class DecisionStoreError(RuntimeError):
    """The decision journal is malformed."""


@dataclass(frozen=True)
class DecisionRecord:
    """One journalled decision."""

    decision_id: str
    at: str
    ticker: str
    decision: str
    shariah_status: str
    breach: str
    trigger: str
    reason: str
    falsification_condition: str
    score: str | None = None
    valuation_gap_pct: str | None = None
    veto_fired: str | None = None
    threshold_version: str = ""
    policy_version: int = 0
    inputs_digest: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DecisionRecord:
        try:
            return cls(
                decision_id=str(data["decision_id"]),
                at=str(data["at"]),
                ticker=str(data["ticker"]),
                decision=str(data["decision"]),
                shariah_status=str(data["shariah_status"]),
                breach=str(data["breach"]),
                trigger=str(data["trigger"]),
                reason=str(data["reason"]),
                falsification_condition=str(data["falsification_condition"]),
                score=data.get("score"),
                valuation_gap_pct=data.get("valuation_gap_pct"),
                veto_fired=data.get("veto_fired"),
                threshold_version=str(data.get("threshold_version", "")),
                policy_version=int(data.get("policy_version", 0)),
                inputs_digest=str(data.get("inputs_digest", "")),
            )
        except KeyError as exc:
            raise DecisionStoreError(f"decision record missing {exc}") from exc


def decision_id(ticker: str, decision: str, inputs_digest: str) -> str:
    """Content-addressed id: ticker + decision + the inputs it was made on.

    Deliberately **not** date-stamped. A review that re-affirms the same decision
    on the same numbers is not a new decision, and journalling it daily would
    bury the moments something actually changed under a pile of re-affirmations.
    A new record appears exactly when the decision changes or the numbers behind
    it do — which is what "why did we sell SWDY?" needs to stay answerable.
    """
    return f"{ticker}-{digest({'d': decision, 'i': inputs_digest})[:10]}"


class JsonlDecisionStore:
    """Append-only decision journal backed by a JSONL file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def read_all(self) -> list[DecisionRecord]:
        if not self.path.exists():
            return []
        out: list[DecisionRecord] = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    out.append(DecisionRecord.from_dict(json.loads(stripped)))
                except (json.JSONDecodeError, DecisionStoreError, ValueError) as exc:
                    raise DecisionStoreError(f"{self.path}:{line_no}: {exc}") from exc
        return out

    def get(self, decision_id_: str) -> DecisionRecord | None:
        for record in self.read_all():
            if record.decision_id == decision_id_:
                return record
        return None

    def for_ticker(self, ticker: str) -> list[DecisionRecord]:
        key = ticker.strip().upper()
        return [r for r in self.read_all() if r.ticker == key]

    def append(self, record: DecisionRecord) -> DecisionRecord:
        """Write a decision. Re-writing an identical decision is a no-op.

        Idempotence matters because a quarterly review may be re-run after a
        crash; the same review of the same numbers must not appear twice in the
        history and make one decision look like two.
        """
        if self.get(record.decision_id) is not None:
            return record
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
            fh.flush()
        return record
