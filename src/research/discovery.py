"""Filing discovery: idempotent across runs, loud on failure.

The orchestration around a :class:`~research.protocols.FilingDiscovery` adapter,
kept separate from any adapter so it can be tested without a network and reused
by every source type.

Idempotency is the property that matters: the daily poll runs unattended, and a
filing that gets processed twice produces a duplicate decision and, in the worst
case, a duplicate order proposal. Seen filings are therefore recorded in an
append-only JSONL and matched on ``(ticker, url)`` before download and on
content hash after — the same document is routinely published at two URLs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from research.protocols import FilingDiscovery, FilingRef, SourceUnavailableError


@dataclass(frozen=True)
class DiscoveryOutcome:
    """What one discovery run found, and what it could not check.

    ``failed`` is never empty-and-ignored: a company whose source refused us is
    listed by name so the run can say "I could not check EAST today" instead of
    quietly reporting no new filings.
    """

    new: tuple[FilingRef, ...]
    already_seen: tuple[FilingRef, ...]
    failed: tuple[tuple[str, str], ...]     # (ticker, reason)

    @property
    def complete(self) -> bool:
        return not self.failed


class SeenFilingsLog:
    """Append-only record of every filing we have already discovered."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if stripped:
                    rows.append(json.loads(stripped))
        return rows

    def identities(self) -> set[str]:
        return {f"{r['ticker']}:{r['url']}" for r in self.read_all()}

    def hashes(self) -> set[str]:
        return {r["content_hash"] for r in self.read_all() if r.get("content_hash")}

    def record(self, ref: FilingRef) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        row: dict[str, Any] = {
            "ticker": ref.ticker,
            "url": ref.url,
            "title": ref.title,
            "published_at": ref.published_at,
            "source_type": ref.source_type,
            "discovered_at": ref.discovered_at,
        }
        for key, value in (
            ("fiscal_year", ref.fiscal_year),
            ("period_type", ref.period_type),
            ("content_hash", ref.content_hash),
        ):
            if value is not None:
                row[key] = value
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            fh.flush()


def discover_filings(
    tickers: list[str],
    adapter: FilingDiscovery,
    seen: SeenFilingsLog,
    *,
    since: str,
    now: str,
) -> DiscoveryOutcome:
    """Poll every company, recording what is new and reporting what failed.

    One company's failure never aborts the run — the other twenty still get
    checked — but it is carried out in ``failed`` so the caller can tell the
    user which names today's "nothing new" does not cover.
    """
    known = seen.identities()
    new: list[FilingRef] = []
    already: list[FilingRef] = []
    failed: list[tuple[str, str]] = []

    for ticker in tickers:
        try:
            refs = adapter.discover(ticker, since)
        except SourceUnavailableError as exc:
            failed.append((ticker, exc.reason))
            continue

        for ref in refs:
            stamped = replace(ref, discovered_at=ref.discovered_at or now)
            if stamped.identity in known:
                already.append(stamped)
                continue
            seen.record(stamped)
            known.add(stamped.identity)
            new.append(stamped)

    return DiscoveryOutcome(new=tuple(new), already_seen=tuple(already), failed=tuple(failed))
