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
