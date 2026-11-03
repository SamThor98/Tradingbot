"""Bind ``EVENT_PROB_*`` environment settings into a config object.

Kept separate from ``PRED_MARKET_*`` on purpose: that flag family controls the
Stage B scan-score overlay in ``prediction_market.py``. This layer must be able
to run while the overlay stays off, and promoting one must never promote the
other.
"""

from __future__ import annotations

from pathlib import Path

from core.event_probability.schema import EventProbabilityConfig


def build_event_probability_config(skill_dir: Path | None = None) -> EventProbabilityConfig:
    from config import (
        get_event_prob_cache_ttl_sec,
        get_event_prob_ems_lookback_days,
        get_event_prob_enabled,
        get_event_prob_historical_snapshot_path,
        get_event_prob_max_event_age_hours,
        get_event_prob_max_market_weight,
        get_event_prob_max_spread,
        get_event_prob_min_liquidity,
        get_event_prob_mode,
        get_event_prob_provider,
        get_event_prob_timeout_ms,
    )

    return EventProbabilityConfig(
        enabled=bool(get_event_prob_enabled(skill_dir)),
        mode=str(get_event_prob_mode(skill_dir)),
        provider=str(get_event_prob_provider(skill_dir)),
        timeout_ms=int(get_event_prob_timeout_ms(skill_dir)),
        cache_ttl_sec=int(get_event_prob_cache_ttl_sec(skill_dir)),
        max_event_age_hours=float(get_event_prob_max_event_age_hours(skill_dir)),
        min_liquidity=float(get_event_prob_min_liquidity(skill_dir)),
        max_spread=float(get_event_prob_max_spread(skill_dir)),
        max_market_weight=float(get_event_prob_max_market_weight(skill_dir)),
        lookback_days=(1, 7, 30),
        ems_lookback_days=int(get_event_prob_ems_lookback_days(skill_dir)),
        historical_snapshot_path=str(get_event_prob_historical_snapshot_path(skill_dir)),
    )
