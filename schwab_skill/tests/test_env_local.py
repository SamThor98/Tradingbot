from __future__ import annotations

from pathlib import Path

from core.env_local import (
    SIGNAL_STACK_ENFORCED_ENV,
    apply_entry_timing_experiment_env,
    apply_signal_stack_enforced_env,
    signal_stack_enforced_readiness_from_values,
    upsert_env_file,
)


def test_upsert_env_file_adds_and_updates(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("FOO=bar\nENTRY_TIMING_SHADOW_MODE=off\n", encoding="utf-8")
    changed = upsert_env_file(env_path, {"ENTRY_TIMING_SHADOW_MODE": "shadow", "BAZ": "1"})
    assert "ENTRY_TIMING_SHADOW_MODE" in changed
    assert "BAZ" in changed
    text = env_path.read_text(encoding="utf-8")
    assert "ENTRY_TIMING_SHADOW_MODE=shadow" in text
    assert "BAZ=1" in text
    assert "FOO=bar" in text


def test_apply_entry_timing_experiment_env_idempotent(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    first = apply_entry_timing_experiment_env(env_path)
    assert len(first) == 3
    second = apply_entry_timing_experiment_env(env_path)
    assert second == []


def test_apply_signal_stack_enforced_env_ready(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    changed = apply_signal_stack_enforced_env(env_path)
    assert set(SIGNAL_STACK_ENFORCED_ENV) <= set(changed) or changed == []
    # Fresh file should write all keys
    assert len(changed) == len(SIGNAL_STACK_ENFORCED_ENV)
    second = apply_signal_stack_enforced_env(env_path)
    assert second == []
    readiness = signal_stack_enforced_readiness_from_values(
        {k: v for k, v in (line.split("=", 1) for line in env_path.read_text(encoding="utf-8").splitlines() if "=" in line)}
    )
    assert readiness["ready"] is True
    assert readiness["profile"] == "breakout_buffer_only_0.010"
    assert readiness["entry_timing_mode"] == "live"
    assert readiness["exit_manager_mode"] == "live"
    assert readiness["rank_filter_v2_mode"] == "live"
    assert readiness["rank_filter_v2_min_percentile"] == 75
    assert readiness["pts_52w_cap_mode"] == "live"
    assert readiness["pts_52w_cap_max"] == 37.0


def test_signal_stack_enforced_readiness_rejects_shadow_pts_52w_cap() -> None:
    values = dict(SIGNAL_STACK_ENFORCED_ENV)
    values["PTS_52W_CAP_MODE"] = "shadow"
    readiness = signal_stack_enforced_readiness_from_values(values)
    assert readiness["ready"] is False
    assert "PTS_52W_CAP_MODE=live" in readiness["missing_env"]


def test_signal_stack_enforced_readiness_rejects_shadow_entry() -> None:
    values = dict(SIGNAL_STACK_ENFORCED_ENV)
    values["ENTRY_TIMING_SHADOW_MODE"] = "shadow"
    readiness = signal_stack_enforced_readiness_from_values(values)
    assert readiness["ready"] is False
    assert "ENTRY_TIMING_SHADOW_MODE=live" in readiness["missing_env"]


def test_signal_stack_enforced_readiness_rejects_shadow_rank_filter() -> None:
    values = dict(SIGNAL_STACK_ENFORCED_ENV)
    values["RANK_FILTER_V2_MODE"] = "shadow"
    readiness = signal_stack_enforced_readiness_from_values(values)
    assert readiness["ready"] is False
    assert "RANK_FILTER_V2_MODE=live" in readiness["missing_env"]