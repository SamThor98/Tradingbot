from __future__ import annotations

import json
from pathlib import Path

from core.entry_timing_live_compare import (
    compare_live_to_offline,
    extract_live_entry_shadow_metrics,
    load_last_scan_diagnostics,
    offline_entry_timing_targets,
)


def test_offline_targets_from_sweep_row() -> None:
    artifact = {
        "recommendation": {"action": "experiment_breakout_buffer_only"},
        "breakout_buffer_only_sweep": [
            {"min_breakout_buffer_pct": 0.01, "retention_pct": 50.1, "delta_early_stopout_pp": -3.9},
        ],
        "live_shadow_replay": {"replayed_trades": 758, "would_filter_any": 379},
    }
    offline = offline_entry_timing_targets(artifact)
    assert offline["would_filter_pct_offline"] == 49.9
    assert offline["recommended_action"] == "experiment_breakout_buffer_only"


def test_compare_passes_in_band_with_experiment_profile() -> None:
    offline = offline_entry_timing_targets(
        {
            "recommendation": {"action": "experiment_breakout_buffer_only"},
            "breakout_buffer_only_sweep": [{"min_breakout_buffer_pct": 0.01, "retention_pct": 50.0}],
        }
    )
    live = extract_live_entry_shadow_metrics(
        {
            "stage_a_candidates": 100,
            "entry_shadow_would_filter_any": 52,
            "entry_timing_shadow_mode": "shadow",
            "entry_timing_shadow_profile": "breakout_buffer_only_0.010",
        }
    )
    result = compare_live_to_offline(live, offline)
    assert result["verdict"] == "pass"
    assert result["delta_would_filter_pp"] == 2.0


def test_compare_skips_when_shadow_mode_off() -> None:
    offline = offline_entry_timing_targets(
        {"breakout_buffer_only_sweep": [{"min_breakout_buffer_pct": 0.01, "retention_pct": 50.0}]}
    )
    live = extract_live_entry_shadow_metrics({"stage_a_candidates": 80, "entry_shadow_would_filter_any": 0})
    result = compare_live_to_offline(live, offline)
    assert result["verdict"] == "skip"


def test_compare_fails_wrong_profile() -> None:
    offline = offline_entry_timing_targets(
        {
            "breakout_buffer_only_sweep": [{"min_breakout_buffer_pct": 0.01, "retention_pct": 50.0}],
        }
    )
    live = extract_live_entry_shadow_metrics(
        {
            "stage_a_candidates": 80,
            "entry_shadow_would_filter_any": 40,
            "entry_timing_shadow_mode": "shadow",
            "entry_timing_shadow_profile": "default_sma50_and_buffer",
        }
    )
    result = compare_live_to_offline(live, offline)
    assert result["verdict"] == "skip"


def test_compare_fails_when_experiment_rate_out_of_band() -> None:
    offline = offline_entry_timing_targets(
        {"breakout_buffer_only_sweep": [{"min_breakout_buffer_pct": 0.01, "retention_pct": 50.0}]},
        band=(35.0, 65.0),
    )
    live = extract_live_entry_shadow_metrics(
        {
            "stage_a_candidates": 100,
            "entry_shadow_would_filter_any": 5,
            "entry_timing_shadow_mode": "shadow",
            "entry_timing_shadow_profile": "breakout_buffer_only_0.010",
        }
    )
    result = compare_live_to_offline(live, offline)
    assert result["verdict"] == "fail"
    assert any("outside band" in err for err in result["errors"])


def test_empirical_band_accepts_live_enforcement_session_rate(tmp_path: Path) -> None:
    import shutil

    from core.entry_timing_live_compare import (
        build_live_entry_shadow_compare_report,
        compute_empirical_would_filter_band,
    )

    src_cache = Path(__file__).resolve().parents[1] / "validation_artifacts" / "entry_timing_replay_cache_control_legacy_aug.json"
    if not src_cache.exists():
        return

    art_dir = tmp_path / "validation_artifacts"
    art_dir.mkdir()
    shutil.copy(src_cache, art_dir / "entry_timing_replay_cache_control_legacy_aug.json")
    (art_dir / "entry_timing_shadow_counterfactual_control_legacy_aug.json").write_text(
        json.dumps(
            {
                "breakout_buffer_only_sweep": [{"min_breakout_buffer_pct": 0.01, "retention_pct": 41.9}],
                "live_shadow_replay": {"replayed_trades": 16402, "would_filter_any": 9533},
            }
        ),
        encoding="utf-8",
    )

    band = compute_empirical_would_filter_band(tmp_path)
    assert band["band_source"] == "empirical_replay_daily"
    assert band["band_low"] < 35.0
    assert band["band_high"] > 65.0

    report = build_live_entry_shadow_compare_report(
        {
            "stage_a_candidates": 35,
            "entry_shadow_would_filter_any": 0,
            "entry_shadow_stage2_evaluated": 514,
            "entry_shadow_stage2_would_filter_any": 468,
            "entry_timing_blocked": 159,
            "entry_timing_live_enforced": 1,
            "entry_timing_shadow_mode": "live",
            "entry_timing_shadow_profile": "breakout_buffer_only_0.010",
        },
        skill_dir=tmp_path,
    )
    assert report is not None
    assert report["comparison"]["verdict"] in {"pass", "warn"}
    assert report["comparison"]["verdict"] != "fail"


def test_write_live_entry_shadow_compare_report(tmp_path: Path) -> None:
    import json

    from core.entry_timing_live_compare import write_live_entry_shadow_compare_report

    art_dir = tmp_path / "validation_artifacts"
    art_dir.mkdir()
    (art_dir / "entry_timing_shadow_counterfactual_control_legacy_aug.json").write_text(
        json.dumps(
            {
                "breakout_buffer_only_sweep": [{"min_breakout_buffer_pct": 0.01, "retention_pct": 50.0}],
            }
        ),
        encoding="utf-8",
    )
    report = write_live_entry_shadow_compare_report(
        {
            "stage_a_candidates": 100,
            "entry_shadow_would_filter_any": 48,
            "entry_timing_shadow_mode": "shadow",
            "entry_timing_shadow_profile": "breakout_buffer_only_0.010",
        },
        skill_dir=tmp_path,
    )
    assert report is not None
    assert report["comparison"]["verdict"] == "pass"
    out = art_dir / "live_entry_shadow_compare_control_legacy_aug.json"
    assert out.exists()


def test_extract_live_metrics_prefers_stage2_when_stage_a_thin() -> None:
    live = extract_live_entry_shadow_metrics(
        {
            "stage_a_candidates": 3,
            "entry_shadow_would_filter_any": 0,
            "entry_shadow_stage2_evaluated": 40,
            "entry_shadow_stage2_would_filter_any": 18,
            "entry_timing_shadow_mode": "shadow",
            "entry_timing_shadow_profile": "breakout_buffer_only_0.010",
        }
    )
    assert live["rate_source"] == "stage2_universe"
    assert live["would_filter_pct"] == 45.0


def test_entry_timing_live_blocks_stage_a_helper(tmp_path, monkeypatch) -> None:
    from stage_analysis import entry_timing_blocks_stage_a

    monkeypatch.setenv("ENTRY_TIMING_SHADOW_MODE", "shadow")
    shadow = {"would_filter": True}
    assert entry_timing_blocks_stage_a(shadow, tmp_path) is False

    monkeypatch.setenv("ENTRY_TIMING_SHADOW_MODE", "live")
    assert entry_timing_blocks_stage_a(shadow, tmp_path) is True
    assert entry_timing_blocks_stage_a({"would_filter": False}, tmp_path) is False


def test_compare_accepts_live_experiment_mode() -> None:
    offline = offline_entry_timing_targets(
        {"breakout_buffer_only_sweep": [{"min_breakout_buffer_pct": 0.01, "retention_pct": 50.0}]}
    )
    live = extract_live_entry_shadow_metrics(
        {
            "stage_a_candidates": 88,
            "entry_shadow_would_filter_any": 41,
            "entry_timing_shadow_mode": "live",
            "entry_timing_shadow_profile": "breakout_buffer_only_0.010",
        }
    )
    result = compare_live_to_offline(live, offline, experiment_env_ready=True)
    assert result["verdict"] == "pass"


def test_extract_live_metrics_uses_blocked_rate_in_live_mode() -> None:
    live = extract_live_entry_shadow_metrics(
        {
            "stage_a_candidates": 47,
            "entry_shadow_would_filter_any": 0,
            "entry_timing_blocked": 41,
            "entry_timing_shadow_mode": "live",
            "entry_timing_live_enforced": 1,
            "entry_timing_shadow_profile": "breakout_buffer_only_0.010",
        }
    )
    assert live["rate_source"] == "live_enforcement"
    assert live["would_filter_pct"] == 46.590909090909086


def test_compare_stale_scan_when_env_ready() -> None:
    offline = offline_entry_timing_targets(
        {"breakout_buffer_only_sweep": [{"min_breakout_buffer_pct": 0.01, "retention_pct": 50.0}]}
    )
    live = extract_live_entry_shadow_metrics({"stage_a_candidates": 86, "entry_shadow_would_filter_any": 0})
    result = compare_live_to_offline(live, offline, experiment_env_ready=True)
    assert result["verdict"] == "stale_scan"


def test_entry_timing_experiment_file_readiness(tmp_path: Path) -> None:
    from core.env_local import apply_entry_timing_experiment_env, entry_timing_experiment_file_readiness

    env_path = tmp_path / ".env"
    apply_entry_timing_experiment_env(env_path)
    readiness = entry_timing_experiment_file_readiness(env_path)
    assert readiness["ready"] is True
    assert readiness["profile"] == "breakout_buffer_only_0.010"


def test_load_diagnostics_from_json_file(tmp_path: Path) -> None:
    payload = {
        "at": "2026-06-27T12:00:00Z",
        "diagnostics": {"stage_a_candidates": 12, "entry_shadow_would_filter_any": 6},
    }
    path = tmp_path / "last_scan.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    diag, meta = load_last_scan_diagnostics(diagnostics_json=path)
    assert diag is not None
    assert diag["stage_a_candidates"] == 12
    assert meta["source"] == "diagnostics_json"


def test_assess_stage2b_readiness_requires_two_pass_scans() -> None:
    from core.entry_timing_live_compare import assess_stage2b_readiness

    one = assess_stage2b_readiness(
        [
            {
                "verdict": "pass",
                "stage_a_candidates": 88,
                "entry_timing_shadow_profile": "breakout_buffer_only_0.010",
                "watchlist_size": 1503,
            }
        ]
    )
    assert one["ready"] is False

    two = assess_stage2b_readiness(
        [
            {
                "verdict": "pass",
                "scan_at": "2026-06-28T15:20:00Z",
                "stage_a_candidates": 35,
                "entry_timing_shadow_profile": "breakout_buffer_only_0.010",
                "watchlist_size": 300,
            },
            {
                "verdict": "pass",
                "scan_at": "2026-06-28T15:37:00Z",
                "stage_a_candidates": 88,
                "entry_timing_shadow_profile": "breakout_buffer_only_0.010",
                "watchlist_size": 1503,
            },
        ]
    )
    assert two["ready"] is True
    assert two["full_universe_pass_scans"] == 1


def test_append_entry_timing_evidence_dedupes_scan_at(tmp_path: Path) -> None:
    from core.entry_timing_live_compare import append_entry_timing_evidence_record, load_entry_timing_evidence_log

    report = {
        "generated_at": "2026-06-28T16:00:00Z",
        "live_meta": {"scan_at": "2026-06-28T15:37:00Z", "source": "test", "watchlist_size": 1503},
        "live": {
            "scan_at": "2026-06-28T15:37:00Z",
            "stage_a_candidates": 88,
            "entry_shadow_would_filter_any": 41,
            "would_filter_pct": 46.6,
            "rate_source": "stage_a_candidates",
            "entry_timing_shadow_profile": "breakout_buffer_only_0.010",
            "entry_timing_shadow_mode": "shadow",
        },
        "comparison": {"verdict": "pass", "delta_would_filter_pp": -3.3},
    }
    append_entry_timing_evidence_record(report, skill_dir=tmp_path)
    append_entry_timing_evidence_record(report, skill_dir=tmp_path)
    log = load_entry_timing_evidence_log(tmp_path)
    assert len(log["records"]) == 1
    assert log["stage2b"]["pass_scans"] == 1
