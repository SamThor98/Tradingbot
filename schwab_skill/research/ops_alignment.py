"""Ops SQL vs research registry name alignment.

Enabled registry names must exist as FeatureRecord columns, documented aliases,
or documented overflow in ``raw_features_json``. A new enabled feature written
under a different ops name fails CI.
"""

from __future__ import annotations

from typing import Any

from feature_store import FeatureRecord
from research.registry import OPS_TO_REGISTRY_ALIASES, enabled_feature_names, feature_entries, load_feature_registry

# Dedicated SQL columns on FeatureRecord (canonical or aliased).
OPS_SQL_COLUMNS: frozenset[str] = frozenset(c.name for c in FeatureRecord.__table__.columns)

# Enabled schema-v1 features that live in raw_features_json rather than a typed column.
# Add a name here when enabling a research-only field; do not silently rename it in ops.
RAW_FEATURES_JSON_OVERFLOW: frozenset[str] = frozenset(
    {
        "sma_20",
        "sma_20_slope",
        "sma_50_slope",
        "sma_200_slope",
        "dist_sma50_pct",
        "dist_sma200_pct",
        "stage_score",
        "weinstein_score",
        "trend_strength_score",
        "atr_14",
        "atr_pct",
        "atr_percentile_126d",
        "atr_expansion_rate",
        "atr_contraction_duration",
        "realized_vol_20d",
        "hist_vol_percentile_252d",
        "bb_bandwidth_20",
        "compression_score",
        "vol_contraction_score",
        "avg_vcp_volume_ratio",
        "rvol_percentile_60d",
        "volume_anomaly_score",
        "dollar_volume",
        "dollar_volume_percentile_60d",
        "accumulation_score",
        "distribution_score",
        "obv_slope_20d",
        "volume_trend_20d",
        "volume_score",
        "vcp_score",
        "breakout_distance_pct",
        "breakout_velocity",
        "dist_above_pivot_pct",
        "breakout_persistence",
        "breakout_quality_score",
        "failed_breakout_attempts_63d",
        "prior_resistance_touches_63d",
        "ret_5d_prev",
        "ret_10d_prev",
        "ret_20d_prev",
        "ret_60d_prev",
        "ret_120d_prev",
        "ret_252d_prev",
        "momentum_acceleration",
        "adv_20d",
        "dollar_adv_20d",
        "base_duration_days",
        "base_depth_pct",
        "gap_stats_20d",
        "rank_score_v2",
        "advisory_p_up_10d",
        "sector_rel_21d",
        "spy_ret_20d",
        "spy_ret_60d",
        "spy_dist_sma200_pct",
        "spy_realized_vol_20d",
        "spy_above_sma200",
        "regime_risk_off",
        "rel_spy_20d",
        "spy_trend_efficiency_20d",
        "spy_choppiness_14",
        "spy_vol_of_vol_20d",
        "regime_chop_score",
        "trend_efficiency_20d",
        "breakout_hot_raw",
        "sec_risk_score",
        "sma_50",
        "sma_150",
        "sma_200",
        "pct_from_52w_high",
        "volume_ratio",
        "vcp_contraction_days",
        "signal_score",
        "pead_surprise_pct",
        "forensic_sloan",
    }
)


def ops_known_names() -> set[str]:
    known = set(OPS_SQL_COLUMNS)
    known.update(OPS_TO_REGISTRY_ALIASES.keys())
    known.update(OPS_TO_REGISTRY_ALIASES.values())
    known.update(RAW_FEATURES_JSON_OVERFLOW)
    return known


def alignment_errors(registry: dict[str, Any] | None = None) -> list[str]:
    """Fail if an enabled registry feature is not in SQL, aliases, or overflow."""
    reg = registry if registry is not None else load_feature_registry()
    known = ops_known_names()
    errors: list[str] = []
    for name in enabled_feature_names(reg):
        aliases = []
        for feat in feature_entries(reg):
            if str(feat.get("name")) == name:
                aliases = [str(a) for a in (feat.get("aliases") or [])]
                break
        if name in known or any(a in known for a in aliases):
            continue
        errors.append(
            f"enabled feature {name} missing from feature_store columns and "
            f"raw_features_json overflow allowlist (do not write it under a different ops name)"
        )
    # Inverse: ops alias must map to a registry name that exists
    registry_names = {str(f.get("name")) for f in feature_entries(reg)}
    for ops_name, canon in OPS_TO_REGISTRY_ALIASES.items():
        if canon not in registry_names:
            errors.append(f"ops alias {ops_name} -> {canon} is not a registry feature")
    return errors
