"""Scheduled routines — the agent acting without being asked (``ROADMAP_V1`` M7).

A routine is the same agent with the same tools, started by a clock instead of a
user. Four properties are enforced by :mod:`routines.base` rather than left to
each routine:

* **``now`` is captured once**, at wake-up, and threaded through the whole run.
* **Running twice does not double-process.** Idempotency is by run key
  (routine + date) and by content (filing hash, decision id).
* **A routine either messages the user or deliberately stays quiet**, and says
  which it did. "Nothing happened" is a result, and on a quiet week it is the
  message the user gets — an agent that only speaks when something is wrong
  reads as an alarm, not a steward.
* **A failure is reported, never swallowed.** A poll that could not reach three
  companies says so; it does not report "no new filings".
"""

from __future__ import annotations

from routines.base import Routine, RoutineResult, already_ran, record_run
from routines.daily import daily_filing_poll, daily_market_refresh
from routines.periodic import quarterly_review, weekly_digest

__all__ = [
    "Routine",
    "RoutineResult",
    "already_ran",
    "daily_filing_poll",
    "daily_market_refresh",
    "quarterly_review",
    "record_run",
    "weekly_digest",
]
