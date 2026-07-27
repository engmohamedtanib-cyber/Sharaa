"""Where company data comes from — and what happens when it does not exist.

The engine is complete; the data is not. No filing has ever been ingested in
this environment (``memory/CURRENT_STATE.md``), so every analysis tool needs a
defined answer to "what if there is nothing?".

That answer is :class:`NoDataProvider`: it returns ``None`` for every company,
which the tools turn into an explicit refusal naming the missing input. The
alternative — returning an empty ``ScoringInputs`` — would let the scoring layer
produce a real-looking number out of nothing, which is the exact failure the
Prime Directive is written against.

A live provider (M5) implements the same protocol over validated stored figures.
Swapping it in changes no tool code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from engine.types import ScoringInputs


@dataclass(frozen=True)
class CompanyFacts:
    """Everything the engine needs about one company at one point in time.

    ``sector`` and ``core_prohibited`` come from the activity screen and the
    prohibited-activities config, not from the filing. They are carried here so
    a tool never has to guess a company's business from its name.
    """

    ticker: str
    sector: str
    inputs: ScoringInputs
    core_prohibited: bool = False
    filing_age_days: int = 0
    period_label: str = ""
    source_note: str = ""


class CompanyDataProvider(Protocol):
    """Read-only access to validated company data."""

    def get(self, ticker: str) -> CompanyFacts | None:
        """Validated facts for one company, or ``None`` when we do not have them."""
        ...

    def available_tickers(self) -> tuple[str, ...]:
        """Every company we hold validated data for."""
        ...


class NoDataProvider:
    """The honest default: we have no validated company data at all."""

    def get(self, ticker: str) -> CompanyFacts | None:
        return None

    def available_tickers(self) -> tuple[str, ...]:
        return ()


@dataclass
class InMemoryDataProvider:
    """Test double and seed-data holder. Never used against live decisions."""

    facts: dict[str, CompanyFacts] = field(default_factory=dict)

    def get(self, ticker: str) -> CompanyFacts | None:
        return self.facts.get(ticker.strip().upper())

    def available_tickers(self) -> tuple[str, ...]:
        return tuple(sorted(self.facts))

    def add(self, facts: CompanyFacts) -> None:
        self.facts[facts.ticker.strip().upper()] = facts
