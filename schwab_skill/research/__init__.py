"""Probabilistic ranking research platform (Phase B/C).

See ``docs/PROBABILISTIC_RANKING_RESEARCH_ARCHITECTURE.md``.
"""

from __future__ import annotations

from research.registry import (
    FEATURE_SCHEMA_VERSION,
    enabled_feature_names,
    load_feature_registry,
)

__all__ = [
    "FEATURE_SCHEMA_VERSION",
    "enabled_feature_names",
    "load_feature_registry",
]
