"""Band matching semantics and config band-table boundaries (§4)."""

from __future__ import annotations

import pytest

from conftest import D
from engine.bands import Band, match, parse_bands


@pytest.mark.parametrize(
    "op,value,x,expected",
    [
        ("le", "0.30", "0.30", True),
        ("le", "0.30", "0.30001", False),
        ("lt", "1.0", "1.0", False),
        ("lt", "1.0", "0.999", True),
        ("gt", "10", "10", False),
        ("gt", "10", "10.1", True),
        ("ge", "90", "90", True),
        ("ge", "90", "89.99", False),
        ("eq", "0", "0", True),
    ],
)
def test_band_matches(op, value, x, expected):
    assert Band(op, D(value), {}).matches(D(x)) is expected


def test_match_returns_first_matching_band():
    bands = parse_bands([{"le": 0.30, "points": 7}, {"le": 0.45, "points": 6}])
    assert match(D("0.30"), bands).payload["points"] == D("7")
    assert match(D("0.40"), bands).payload["points"] == D("6")
    assert match(D("0.50"), bands) is None


def test_parse_bands_requires_exactly_one_operator():
    with pytest.raises(ValueError):
        parse_bands([{"le": 1, "gt": 2, "points": 1}])
    with pytest.raises(ValueError):
        parse_bands([{"points": 1}])


def test_unknown_operator_raises():
    with pytest.raises(ValueError):
        Band("neq", D("1"), {}).matches(D("1"))


# ---- config band boundaries: 1A (le semantics, most generous first) --
def test_p1a_bands_boundaries(cfg):
    bands = cfg.bands("P1", "1A")
    assert match(D("0.0"), bands).payload["points"] == D("8")
    assert match(D("0.005"), bands).payload["points"] == D("7")   # exact boundary -> more generous
    assert match(D("0.015"), bands).payload["points"] == D("5")
    assert match(D("0.050"), bands).payload["points"] == D("0.5")
    assert match(D("0.06"), bands) is None                         # above all bands


def test_p1b_bands_boundaries(cfg):
    bands = cfg.bands("P1", "1B")
    assert match(D("0.30"), bands).payload["points"] == D("7")
    assert match(D("0.300001"), bands).payload["points"] == D("6")
    assert match(D("1.00"), bands).payload["points"] == D("0.5")
