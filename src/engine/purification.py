"""Purification / tathir ledger (ENGINE_SPEC §8).

``purification_ratio = impure_income / net_profit_attributable``
``amount_due        = base * purification_ratio``

If ``net_profit_attributable <= 0`` the ratio is undefined — the impure income
is carried forward to the next profitable period rather than dividing by a
non-positive number. Pure module.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from common.decimals import ZERO
from engine.config import Thresholds

BASIS_DIVIDENDS = "DIVIDENDS"
BASIS_TOTAL_RETURN = "TOTAL_RETURN"


@dataclass(frozen=True)
class PurificationResult:
    ratio: Decimal | None
    amount_due: Decimal | None
    carried_forward: Decimal   # impure income awaiting a profitable period
    basis: str
    net_profit_nonpositive: bool


def purify(
    impure_income: Decimal,
    net_profit_attributable: Decimal,
    dividends_received: Decimal,
    cfg: Thresholds,
    *,
    realised_gains: Decimal = ZERO,
    carried_in: Decimal = ZERO,
) -> PurificationResult:
    """Compute the purification amount due for one review (§8).

    ``carried_in`` is impure income deferred from a prior loss-making period;
    it is folded into this period's impure income once profit is positive.
    """
    basis = cfg.purification_basis
    total_impure = impure_income + carried_in

    if net_profit_attributable <= ZERO:
        # Undefined ratio: defer, never divide by a non-positive number.
        return PurificationResult(
            ratio=None,
            amount_due=None,
            carried_forward=total_impure,
            basis=basis,
            net_profit_nonpositive=True,
        )

    ratio = total_impure / net_profit_attributable
    base = dividends_received + realised_gains if basis == BASIS_TOTAL_RETURN else dividends_received
    amount_due = base * ratio
    return PurificationResult(
        ratio=ratio,
        amount_due=amount_due,
        carried_forward=ZERO,
        basis=basis,
        net_profit_nonpositive=False,
    )
