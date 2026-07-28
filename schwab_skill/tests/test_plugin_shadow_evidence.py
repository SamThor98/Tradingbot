from __future__ import annotations

from pathlib import Path

from core.plugin_shadow_evidence import (
    append_plugin_shadow_record,
    build_plugin_shadow_record,
    load_plugin_shadow_records,
    summarize_plugin_shadow_records,
)


def test_build_returns_none_when_both_off() -> None:
    assert (
        build_plugin_shadow_record(
            {
                "regime_v2_mode": "off",
                "correlation_guard_mode": "off",
                "strategy_pead_primary_mode": "off",
                "strategy_pead_primary_effective_mode": "off",
            }
        )
        is None
    )


def test_build_and_append_roundtrip(tmp_path: Path) -> None:
    skill = tmp_path
    (skill / "validation_artifacts").mkdir()
    diag = {
        "regime_v2_mode": "shadow",
        "regime_v2_score": 70.5,
        "regime_v2_bucket": "high",
        "regime_v2_blocked": 0,
        "correlation_guard_mode": "shadow",
        "correlation_guard_would_demote": 2,
        "correlation_guard_demoted": 0,
        "correlation_guard_pair_demotions": 0,
        "entry_timing_live_enforced": 1,
        "rank_filter_v2_mode": "live",
        "rank_filter_v2_min_percentile": 76,
        "rank_filter_v2_evaluated": 20,
        "rank_filter_v2_dropped": 15,
        "data_quality": "ok",
    }
    rec = build_plugin_shadow_record(diag, scan_at="2026-07-20T00:00:00Z", signals_found=5)
    assert rec is not None
    assert rec["stack"]["rank_retention_pct"] == 25.0
    append_plugin_shadow_record(skill, rec)
    rows = load_plugin_shadow_records(skill)
    assert len(rows) == 1
    summary = summarize_plugin_shadow_records(rows)
    assert summary["n_scans"] == 1
    assert summary["correlation_would_demote_max"] == 2
    assert summary["regime_v2_score_mean"] == 70.5
