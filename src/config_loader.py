"""Boundary loader for the YAML config files.

This is the ONE place that reads config from disk. It is deliberately *not*
inside ``engine/`` so that engine functions stay pure (``CLAUDE.md`` §5): they
receive already-constructed config objects as arguments.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from engine.config import Thresholds
from engine.policy import InvestmentPolicy, parse_policy
from engine.universe import Universe, parse_universe

# Repo root = parent of src/.
_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = _ROOT / "config"


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"config file {path} did not parse to a mapping")
    return data


@lru_cache(maxsize=1)
def load_thresholds(path: str | None = None) -> Thresholds:
    """Load ``config/thresholds.yaml`` into a :class:`Thresholds` wrapper."""
    p = Path(path) if path else CONFIG_DIR / "thresholds.yaml"
    return Thresholds(_read_yaml(p))


@lru_cache(maxsize=1)
def load_line_items(path: str | None = None) -> dict[str, Any]:
    p = Path(path) if path else CONFIG_DIR / "line_items.yaml"
    return _read_yaml(p)


@lru_cache(maxsize=1)
def load_prohibited_activities(path: str | None = None) -> dict[str, Any]:
    p = Path(path) if path else CONFIG_DIR / "prohibited_activities.yaml"
    return _read_yaml(p)


@lru_cache(maxsize=1)
def load_sources(path: str | None = None) -> dict[str, Any]:
    p = Path(path) if path else CONFIG_DIR / "sources.yaml"
    return _read_yaml(p)


@lru_cache(maxsize=1)
def load_universe(path: str | None = None) -> Universe:
    """Load ``config/universe.yaml``.

    Loading always succeeds; *using* an unpopulated universe is what raises
    (:meth:`engine.universe.Universe.require_available`). Keeping the two apart
    means a caller can ask "is the universe ready?" without handling an
    exception, while a caller who forgets to ask cannot silently screen zero
    companies and report the result as an answer.
    """
    p = Path(path) if path else CONFIG_DIR / "universe.yaml"
    return parse_universe(_read_yaml(p))


@lru_cache(maxsize=1)
def load_policy(path: str | None = None) -> InvestmentPolicy:
    """Load ``config/ips.yaml`` and validate it against the engine's own limits.

    A policy that would loosen a constitutional limit raises here, at load, so a
    rejected mandate can never be the one a decision was made under.
    """
    p = Path(path) if path else CONFIG_DIR / "ips.yaml"
    return parse_policy(_read_yaml(p), load_thresholds())
