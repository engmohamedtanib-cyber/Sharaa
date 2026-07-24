"""Typed, pure wrapper around ``config/thresholds.yaml``.

Engine functions receive a :class:`Thresholds` instance as an argument and
never read a file themselves (``CLAUDE.md`` §5 — engine functions are pure).
The wrapper coerces numeric thresholds to :class:`Decimal` at access time and
parses band tables into :class:`~engine.bands.Band` rows.

Every computed row stores ``thresholds.version`` so historical results stay
reconstructible after a threshold bump (``ENGINE_SPEC`` §1, §11).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from common.decimals import D
from engine.bands import Band, parse_bands


@dataclass(frozen=True)
class Thresholds:
    """Immutable view over the parsed thresholds document."""

    raw: dict[str, Any]

    # ---- meta -------------------------------------------------------
    @property
    def version(self) -> str:
        return str(self.raw["version"])

    @property
    def governing_standard(self) -> str:
        return str(self.raw["governing_standard"])

    # ---- shariah ----------------------------------------------------
    def screen_threshold(self, code: str) -> Decimal:
        return D(self.raw["shariah"]["screens"][code]["threshold"])

    def screen_name(self, code: str) -> str:
        return str(self.raw["shariah"]["screens"][code]["name"])

    def status_bands(self) -> dict[str, Decimal]:
        bands = self.raw["shariah"]["status_bands"]
        return {name: D(spec["le"]) for name, spec in bands.items()}

    @property
    def staleness_days(self) -> int:
        return int(self.raw["shariah"]["staleness_days"])

    # ---- scoring ----------------------------------------------------
    def pillar_maxima(self) -> dict[str, Decimal]:
        return {k: D(v) for k, v in self.raw["scoring"]["pillar_maxima"].items()}

    @property
    def total_max(self) -> Decimal:
        return D(self.raw["scoring"]["total_max"])

    def sub(self, pillar: str, code: str) -> dict[str, Any]:
        """Raw config block for a scoring sub-criterion, e.g. ('P2','2A')."""
        return self.raw["scoring"][pillar][code]  # type: ignore[no-any-return]

    def bands(self, pillar: str, code: str) -> tuple[Band, ...]:
        return parse_bands(self.sub(pillar, code)["bands"])

    def points_max(self, pillar: str, code: str) -> Decimal:
        return D(self.sub(pillar, code)["points_max"])

    def sub_params(self, pillar: str, code: str) -> dict[str, Any]:
        return dict(self.sub(pillar, code).get("params", {}))

    def sub_points(self, pillar: str, code: str) -> dict[str, Decimal]:
        return {k: D(v) for k, v in self.sub(pillar, code).get("points", {}).items()}

    def score_bands(self) -> tuple[Band, ...]:
        return parse_bands(self.raw["scoring"]["bands"])

    # ---- decisions --------------------------------------------------
    def decisions(self) -> dict[str, Any]:
        return dict(self.raw["decisions"])

    def dec(self, key: str) -> Decimal:
        return D(self.raw["decisions"][key])

    # ---- watchlist --------------------------------------------------
    def watchlist(self) -> dict[str, Any]:
        return dict(self.raw["watchlist"])

    # ---- portfolio --------------------------------------------------
    def portfolio(self) -> dict[str, Any]:
        return dict(self.raw["portfolio"])

    def target_weight_bands(self) -> tuple[Band, ...]:
        return parse_bands(self.raw["portfolio"]["target_weights"])

    def constraint(self, key: str) -> Decimal:
        return D(self.raw["portfolio"]["constraints"][key])

    def execution(self) -> dict[str, Any]:
        return dict(self.raw["portfolio"]["execution"])

    # ---- purification ----------------------------------------------
    @property
    def purification_basis(self) -> str:
        return str(self.raw["purification"]["basis"])

    # ---- material change -------------------------------------------
    def material_change(self) -> dict[str, Any]:
        return dict(self.raw["material_change"])
