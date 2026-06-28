from __future__ import annotations

from pathlib import Path

from core.env_local import apply_entry_timing_experiment_env, upsert_env_file


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
