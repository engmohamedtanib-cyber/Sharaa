"""Append-only audit log for every tool call (R5, ``ARCHITECTURE_V2`` §10).

Two jobs, and the second is the reason this file exists at all:

1. **Accountability.** Every call — arguments, outcome, result digest — is
   written to a JSONL file in the repository, so "why did the system do that?"
   is answerable in two years from git history alone.

2. **Idempotency.** A write tool is called with a ``request_id``. If the same
   ``request_id`` has already succeeded, the stored result is returned and
   nothing is written a second time. This is what makes a retry after a dropped
   connection safe: the second "record 25,000 EGP" cannot become 50,000 EGP.

The digest is a SHA-256 over the canonical JSON of the result. It lets a reader
confirm that a stored result was not edited afterwards without having to trust
that the file was never touched.

Money never round-trips through a float: results are serialised with Decimals
already stringified by the tool layer.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class AuditLogError(RuntimeError):
    """The audit log is malformed."""


def canonical_json(value: Any) -> str:
    """Stable JSON for hashing: sorted keys, no incidental whitespace."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def digest(value: Any) -> str:
    """SHA-256 of the canonical JSON, truncated to 16 hex chars.

    Truncated because this is a change-detector for a personal audit trail, not
    a security primitive; 64 bits is far past accidental collision here and
    keeps the log readable in a diff.
    """
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class AuditRecord:
    """One tool invocation, as written to the log."""

    seq: int
    at: str                      # ISO timestamp, supplied by the caller (UTC)
    tool: str
    request_id: str | None
    args_digest: str
    outcome: str                 # OK | REFUSED | ERROR
    result_digest: str | None
    error: str | None
    result: dict[str, Any] | None  # stored only for idempotent replay of writes

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "seq": self.seq,
            "at": self.at,
            "tool": self.tool,
            "args_digest": self.args_digest,
            "outcome": self.outcome,
        }
        for key, value in (
            ("request_id", self.request_id),
            ("result_digest", self.result_digest),
            ("error", self.error),
            ("result", self.result),
        ):
            if value is not None:
                out[key] = value
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuditRecord:
        return cls(
            seq=int(data["seq"]),
            at=str(data["at"]),
            tool=str(data["tool"]),
            request_id=data.get("request_id"),
            args_digest=str(data["args_digest"]),
            outcome=str(data["outcome"]),
            result_digest=data.get("result_digest"),
            error=data.get("error"),
            result=data.get("result"),
        )


class AuditLog:
    """JSONL audit log. Append and read; there is no update and no delete."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def read_all(self) -> list[AuditRecord]:
        if not self.path.exists():
            return []
        records: list[AuditRecord] = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    records.append(AuditRecord.from_dict(json.loads(stripped)))
                except (json.JSONDecodeError, KeyError, ValueError) as exc:
                    raise AuditLogError(f"{self.path}:{line_no} is not a valid audit record: {exc}") from exc
        return records

    def next_seq(self) -> int:
        records = self.read_all()
        return records[-1].seq + 1 if records else 1

    def find_success(self, tool: str, request_id: str) -> AuditRecord | None:
        """The prior successful call for this ``request_id``, if any.

        Scoped by tool name as well as id so that a client reusing an id across
        two different tools gets a fresh call rather than a nonsense replay.
        """
        for record in reversed(self.read_all()):
            if record.request_id == request_id and record.tool == tool and record.outcome == "OK":
                return record
        return None

    def append(
        self,
        *,
        at: str,
        tool: str,
        args: dict[str, Any],
        outcome: str,
        request_id: str | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        store_result: bool = False,
    ) -> AuditRecord:
        """Write one record.

        ``store_result`` is set for write tools: their result is kept verbatim so
        a retry can be answered from the log. Read results are not stored — they
        are reproducible by calling again, and storing them would bloat the log
        with data that is already derivable.
        """
        record = AuditRecord(
            seq=self.next_seq(),
            at=at,
            tool=tool,
            request_id=request_id,
            args_digest=digest(args),
            outcome=outcome,
            result_digest=digest(result) if result is not None else None,
            error=error,
            result=result if (store_result and result is not None) else None,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
            fh.flush()
        return record
