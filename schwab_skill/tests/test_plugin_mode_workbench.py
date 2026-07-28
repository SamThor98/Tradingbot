"""Tests for plugin mode workbench (roster, gaps, local writes)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import plugin_mode_workbench as wb
from core.env_local import parse_env_file, upsert_env_file


@pytest.fixture(autouse=True)
def _reset_restart_flag():
    wb.clear_restart_required()
    yield
    wb.clear_restart_required()


def test_build_workbench_tiers_and_session_gaps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    skill_dir = tmp_path
    (skill_dir / ".env").write_text("REGIME_V2_MODE=shadow\nCORRELATION_GUARD_MODE=off\n", encoding="utf-8")
    monkeypatch.chdir(skill_dir)

    evidence_dir = skill_dir / "validation_artifacts" / "plugin_shadow_evidence"
    evidence_dir.mkdir(parents=True)
    rows = [
        {
            "data_quality": "ok",
            "regime_v2": {"mode": "shadow", "score": 0.5},
            "correlation_guard": {"mode": "off"},
        },
        {
            "data_quality": "ok",
            "regime_v2": {"mode": "shadow", "score": 0.6},
            "correlation_guard": {"mode": "off"},
        },
        {
            "data_quality": "stale",
            "regime_v2": {"mode": "shadow", "score": 0.4},
            "correlation_guard": {"mode": "off"},
        },
    ]
    (evidence_dir / "shadow_scans.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n",
        encoding="utf-8",
    )

    # Point getters at tmp skill_dir via env file only (process env clear for keys).
    for key in ("REGIME_V2_MODE", "CORRELATION_GUARD_MODE"):
        monkeypatch.delenv(key, raising=False)

    from config import clear_env_cache

    clear_env_cache()
    payload = wb.build_workbench_payload(
        skill_dir=skill_dir,
        diagnostics={"regime_v2_mode": "shadow", "regime_v2_blocked": 1},
        execution_summary={"events": {}, "window_days": 7, "days_present": 2},
        scan_at="2026-07-28T12:00:00Z",
        writes_enabled=True,
    )
    assert payload["writes_enabled"] is True
    assert payload["summary"]["working_on_count"] >= 1
    by_id = {p["id"]: p for p in payload["plugins"]}
    assert by_id["regime_v2"]["mode"] == "shadow"
    assert by_id["regime_v2"]["sessions"]["collected"] == 2
    assert by_id["regime_v2"]["sessions"]["ok"] is False
    assert "lacking" in (by_id["regime_v2"]["sessions"]["lacking_summary"] or "")
    assert by_id["confluence_gate"]["sessions"]["collected"] == 0
    assert "teaser" in payload["summary"]


def test_set_plugin_mode_shadow_upsert_and_verify(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    skill_dir = tmp_path
    env_path = skill_dir / ".env"
    env_path.write_text("REGIME_V2_MODE=off\n", encoding="utf-8")
    monkeypatch.delenv("REGIME_V2_MODE", raising=False)
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    from config import clear_env_cache

    clear_env_cache()

    out = wb.set_plugin_mode(
        skill_dir=skill_dir,
        plugin_id="regime_v2",
        mode="shadow",
        reason="test",
        writes_enabled=True,
    )
    assert out["changed"] is True
    assert out["mode"] == "shadow"
    assert out["restart_required"] is False
    assert parse_env_file(env_path).get("REGIME_V2_MODE") == "shadow"

    audit = (skill_dir / "validation_artifacts" / "plugin_mode_audit" / "mode_writes.jsonl").read_text(
        encoding="utf-8"
    )
    assert "regime_v2" in audit
    assert '"to": "shadow"' in audit or '"to":"shadow"' in audit.replace(" ", "")


def test_live_blocked_without_checklist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    skill_dir = tmp_path
    (skill_dir / ".env").write_text("REGIME_V2_MODE=shadow\n", encoding="utf-8")
    monkeypatch.delenv("REGIME_V2_MODE", raising=False)
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    from config import clear_env_cache

    clear_env_cache()
    with pytest.raises(PermissionError, match="LIVE blocked|PROMOTE"):
        wb.set_plugin_mode(
            skill_dir=skill_dir,
            plugin_id="regime_v2",
            mode="live",
            confirm_phrase="PROMOTE REGIME_V2",
            api_key=None,
            writes_enabled=True,
        )


def test_live_requires_phrase_and_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    skill_dir = tmp_path
    (skill_dir / ".env").write_text("REGIME_V2_MODE=shadow\nWEB_API_KEY=secret\n", encoding="utf-8")
    monkeypatch.setenv("WEB_API_KEY", "secret")
    monkeypatch.delenv("REGIME_V2_MODE", raising=False)
    from config import clear_env_cache

    clear_env_cache()
    with pytest.raises(PermissionError, match="PROMOTE"):
        wb.set_plugin_mode(
            skill_dir=skill_dir,
            plugin_id="regime_v2",
            mode="live",
            confirm_phrase="wrong",
            api_key="secret",
            writes_enabled=True,
        )
    with pytest.raises(PermissionError, match="API-key"):
        wb.set_plugin_mode(
            skill_dir=skill_dir,
            plugin_id="regime_v2",
            mode="live",
            confirm_phrase="PROMOTE REGIME_V2",
            api_key="nope",
            writes_enabled=True,
        )


def test_demote_requires_confirm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    skill_dir = tmp_path
    (skill_dir / ".env").write_text("EXIT_MANAGER_MODE=live\n", encoding="utf-8")
    monkeypatch.delenv("EXIT_MANAGER_MODE", raising=False)
    from config import clear_env_cache

    clear_env_cache()
    with pytest.raises(PermissionError, match="confirm_demote"):
        wb.set_plugin_mode(
            skill_dir=skill_dir,
            plugin_id="exit_manager",
            mode="shadow",
            writes_enabled=True,
        )


def test_writes_disabled(tmp_path: Path) -> None:
    with pytest.raises(PermissionError, match="disabled"):
        wb.set_plugin_mode(
            skill_dir=tmp_path,
            plugin_id="regime_v2",
            mode="shadow",
            writes_enabled=False,
        )


def test_phrase_matches() -> None:
    assert wb.phrase_matches("regime_v2", "PROMOTE REGIME_V2")
    assert not wb.phrase_matches("regime_v2", "PROMOTE OTHER")


def test_promotion_ledger_append_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    scripts = Path(__file__).resolve().parents[1] / "scripts"
    sys.path.insert(0, str(scripts))
    import promotion_ledger

    ledger = tmp_path / "promotion_ledger.jsonl"
    monkeypatch.setenv("PROMOTION_LEDGER_PATH", str(ledger))
    row = promotion_ledger.append_entry("REGIME_V2_MODE=live", "unit test", approver="tester")
    assert row["seq"] == 1
    assert ledger.exists()
    ok, msg = promotion_ledger.verify_chain(promotion_ledger.read_entries(ledger))
    assert ok, msg


def test_plugin_stats_plain_language(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    skill_dir = tmp_path
    (skill_dir / ".env").write_text("REGIME_V2_MODE=shadow\n", encoding="utf-8")
    monkeypatch.delenv("REGIME_V2_MODE", raising=False)
    art_dir = skill_dir / "validation_artifacts"
    art_dir.mkdir(parents=True)
    (art_dir / "signal_stack_counterfactual_control_legacy_aug.json").write_text(
        json.dumps(
            {
                "scenarios": {
                    "exit_grace_breakout_buffer_0.010": {
                        "label": "Exit grace stack",
                        "pf_mean": 1.05,
                        "worst_era_pf": 0.92,
                        "passes_promotion_gates": False,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    from config import clear_env_cache

    clear_env_cache()
    spec = wb.plugin_spec_by_id("exit_manager")
    assert spec is not None
    stats = wb.build_plugin_stats(
        skill_dir=skill_dir,
        spec=spec,
        mode="live",
        counters={"would_partial_tp": 3, "would_move_stop": 1},
        sessions=2,
        session_target=5,
        checklist={"ready_for_live_write": False, "lacking_summary": "lacking 3 RTH session(s)"},
    )
    assert "partial" in stats["last_scan"]["headline"].lower() or "3" in stats["last_scan"]["headline"]
    assert stats["evidence"]["tone"] == "warn"
    assert stats["backtest"]["available"] is True
    assert "1.05" in stats["backtest"]["headline"]
    assert "purpose" in stats and stats["purpose"]


def test_build_workbench_includes_stats(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    skill_dir = tmp_path
    (skill_dir / ".env").write_text("CONFLUENCE_GATE_MODE=shadow\n", encoding="utf-8")
    monkeypatch.delenv("CONFLUENCE_GATE_MODE", raising=False)
    from config import clear_env_cache

    clear_env_cache()
    payload = wb.build_workbench_payload(
        skill_dir=skill_dir,
        diagnostics={"confluence_gate_mode": "shadow", "confluence_would_block": 4},
        execution_summary={"events": {}, "window_days": 7, "days_present": 1},
        writes_enabled=False,
    )
    by_id = {p["id"]: p for p in payload["plugins"]}
    conf = by_id["confluence_gate"]
    assert conf["stats"]["purpose"]
    assert "4" in conf["stats"]["last_scan"]["headline"]
    assert conf["stats"]["next_step"]
