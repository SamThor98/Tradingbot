"""Feature registry loader and ops-name alignment helpers."""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from research.paths import DEFAULT_REGISTRY_PATH, RESEARCH_STORE_DIR

LOG = logging.getLogger(__name__)
FEATURE_SCHEMA_VERSION = 1

# Live signal / feature_store field → registry canonical name
OPS_TO_REGISTRY_ALIASES: dict[str, str] = {
    "close_vs_sma50_pct": "dist_sma50_pct",
    "close_vs_sma200_pct": "dist_sma200_pct",
    "advisory_prob": "advisory_p_up_10d",
    "p_up_calibrated": "advisory_p_up_10d",
}


def _registry_path(path: Path | None = None) -> Path:
    if path is not None:
        return Path(path)
    store_copy = RESEARCH_STORE_DIR / "feature_registry.json"
    if store_copy.is_file():
        return store_copy
    return DEFAULT_REGISTRY_PATH


@lru_cache(maxsize=4)
def _load_raw(path_str: str) -> dict[str, Any]:
    with open(path_str, encoding="utf-8") as fh:
        payload = json.load(fh)
    if not isinstance(payload, dict):
        raise ValueError("feature registry root must be an object")
    features = payload.get("features")
    if not isinstance(features, list) or not features:
        raise ValueError("feature registry must include a non-empty features list")
    return payload


def load_feature_registry(path: Path | None = None, *, reload: bool = False) -> dict[str, Any]:
    """Load and validate the feature registry JSON."""
    reg_path = _registry_path(path)
    if reload:
        _load_raw.cache_clear()
    payload = _load_raw(str(reg_path.resolve()))
    schema_version = int(payload.get("schema_version") or FEATURE_SCHEMA_VERSION)
    if schema_version != FEATURE_SCHEMA_VERSION:
        LOG.warning(
            "Registry schema_version=%s differs from code FEATURE_SCHEMA_VERSION=%s",
            schema_version,
            FEATURE_SCHEMA_VERSION,
        )
    # Build alias index from feature entries
    alias_map: dict[str, str] = dict(OPS_TO_REGISTRY_ALIASES)
    for feat in payload["features"]:
        name = str(feat.get("name") or "")
        for alias in feat.get("aliases") or []:
            alias_map[str(alias)] = name
    payload = dict(payload)
    payload["_alias_map"] = alias_map
    payload["_path"] = str(reg_path)
    return payload


def feature_entries(registry: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    reg = registry if registry is not None else load_feature_registry()
    return list(reg.get("features") or [])


def enabled_feature_names(
    registry: dict[str, Any] | None = None,
    *,
    statuses: frozenset[str] | None = None,
    ohlcv_only: bool = False,
) -> list[str]:
    """Return enabled feature names, optionally filtered by status / data_source."""
    allowed_status = statuses or frozenset({"reuse", "new"})
    names: list[str] = []
    for feat in feature_entries(registry):
        if not feat.get("enabled", True):
            continue
        if str(feat.get("status") or "") not in allowed_status:
            continue
        if ohlcv_only and str(feat.get("data_source") or "") != "ohlcv":
            continue
        name = str(feat.get("name") or "").strip()
        if name:
            names.append(name)
    return names


def feature_coverage(row: dict[str, Any], feature_names: list[str]) -> float:
    if not feature_names:
        return 0.0
    present = 0
    for name in feature_names:
        val = row.get(name)
        if val is None:
            continue
        try:
            if val != val:  # NaN
                continue
        except Exception:
            pass
        present += 1
    return float(present) / float(len(feature_names))


def align_ops_features(raw: dict[str, Any], registry: dict[str, Any] | None = None) -> dict[str, Any]:
    """Map ops/signal fields onto registry canonical names (non-destructive copy)."""
    reg = registry if registry is not None else load_feature_registry()
    alias_map: dict[str, str] = dict(reg.get("_alias_map") or OPS_TO_REGISTRY_ALIASES)
    out: dict[str, Any] = {}
    for key, value in raw.items():
        canon = alias_map.get(key, key)
        # Prefer first non-null write; do not overwrite with None
        if canon in out and out[canon] is not None and value is None:
            continue
        out[canon] = value
    return out


def extract_registry_aligned_from_signal(
    signal: dict[str, Any],
    registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pull overlapping live-signal fields into registry-aligned names for SQL logging."""
    advisory = signal.get("advisory") or {}
    components = signal.get("score_components") or {}
    raw = {
        "sma_50": signal.get("sma_50"),
        "sma_200": signal.get("sma_200"),
        "pct_from_52w_high": components.get("pct_from_52w_high") or signal.get("pct_from_52w_high"),
        "volume_ratio": signal.get("volume_ratio"),
        "avg_vcp_volume_ratio": components.get("avg_vcp_volume_ratio"),
        "atr_14": components.get("atr_14") or signal.get("atr_14"),
        "atr_pct": signal.get("atr_pct") or components.get("atr_pct"),
        "close_vs_sma50_pct": signal.get("close_vs_sma50_pct"),
        "close_vs_sma200_pct": signal.get("close_vs_sma200_pct") or signal.get("close_vs_sma200_pct"),
        "ret_5d_prev": signal.get("ret_5d_prev"),
        "ret_20d_prev": signal.get("ret_20d_prev"),
        "signal_score": signal.get("signal_score"),
        "rank_score_v2": signal.get("rank_score_v2"),
        "edge_score": signal.get("edge_score"),
        "reliability_score": signal.get("reliability_score"),
        "execution_score": signal.get("execution_score"),
        "composite_score": signal.get("composite_score"),
        "rank_score": signal.get("rank_score"),
        "p_up_calibrated": signal.get("p_up_calibrated"),
        "advisory_prob": signal.get("p_up_calibrated", advisory.get("p_up_10d")),
        "sector_rel_21d": signal.get("sector_rel_21d"),
        "pead_surprise_pct": signal.get("pead_surprise_pct"),
        "forensic_sloan": signal.get("forensic_sloan"),
        "forensic_beneish": signal.get("forensic_beneish"),
        "forensic_altman": signal.get("forensic_altman"),
        "sec_risk_score": signal.get("sec_risk_score"),
        "miro_continuation_prob": (signal.get("mirofish_result") or {}).get("continuation_probability"),
        "miro_bull_trap_prob": (signal.get("mirofish_result") or {}).get("bull_trap_probability"),
    }
    # Prefer explicit dist_* if already present
    if signal.get("dist_sma50_pct") is not None:
        raw["dist_sma50_pct"] = signal.get("dist_sma50_pct")
    if signal.get("dist_sma200_pct") is not None:
        raw["dist_sma200_pct"] = signal.get("dist_sma200_pct")
    aligned = align_ops_features(raw, registry)
    return {k: v for k, v in aligned.items() if v is not None}
