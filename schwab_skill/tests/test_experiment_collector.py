from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from core.experiment_collector import (
    build_brief_packet,
    campaign_key,
    format_daily_brief,
    harvest_scan,
    mark_campaign_notified,
)
from core.plugin_mode_workbench import PluginSpec, count_ok_sessions
from core.plugin_shadow_evidence import (
    evidence_session_date,
    load_plugin_shadow_records,
    upsert_plugin_shadow_record,
)

TZ_NY = ZoneInfo("America/New_York")

_OK_DIAG = {
    "regime_v2_mode": "shadow",
    "regime_v2_score": 70.5,
    "regime_v2_bucket": "high",
    "regime_v2_blocked": 0,
    "correlation_guard_mode": "shadow",
    "correlation_guard_would_demote": 1,
    "correlation_guard_demoted": 0,
    "correlation_guard_pair_demotions": 0,
    "strategy_pead_primary_mode": "off",
    "strategy_pead_primary_effective_mode": "off",
    "data_quality": "ok",
}


def _spec() -> PluginSpec:
    return PluginSpec(
        "regime_v2",
        "Regime v2",
        "REGIME_V2_MODE",
        getter=lambda _skill=None: "shadow",
        scope="scan",
        evidence_key="regime_v2",
        session_target=5,
    )


def test_evidence_session_date_converts_utc_to_et() -> None:
    row = {"scan_at": "2026-08-25T18:30:00+00:00"}
    assert evidence_session_date(row) == "2026-08-25"


def test_upsert_replaces_same_et_date(tmp_path: Path) -> None:
    rec_a = {
        "scan_at": "2026-08-25T13:30:00+00:00",
        "data_quality": "ok",
        "signals_found": 1,
        "regime_v2": {"mode": "shadow"},
    }
    rec_b = {
        "scan_at": "2026-08-25T18:00:00+00:00",
        "data_quality": "ok",
        "signals_found": 3,
        "regime_v2": {"mode": "shadow"},
    }
    upsert_plugin_shadow_record(tmp_path, rec_a)
    out = upsert_plugin_shadow_record(tmp_path, rec_b)
    rows = load_plugin_shadow_records(tmp_path)
    assert out["action"] == "replaced"
    assert len(rows) == 1
    assert rows[0]["signals_found"] == 3


def test_count_ok_sessions_unique_dates_and_undated() -> None:
    spec = _spec()
    rows = [
        {
            "scan_at": "2026-08-24T13:30:00+00:00",
            "data_quality": "ok",
            "regime_v2": {"mode": "shadow"},
        },
        {
            "scan_at": "2026-08-24T18:00:00+00:00",
            "data_quality": "ok",
            "regime_v2": {"mode": "shadow"},
        },
        {
            "scan_at": "2026-08-25T13:30:00+00:00",
            "data_quality": "ok",
            "regime_v2": {"mode": "shadow"},
        },
        {
            "data_quality": "ok",
            "regime_v2": {"mode": "shadow"},
        },
        {
            "data_quality": "stale",
            "scan_at": "2026-08-26T13:30:00+00:00",
            "regime_v2": {"mode": "shadow"},
        },
    ]
    assert count_ok_sessions(rows, spec) == 3


def test_harvest_skips_not_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "config.get_experiment_collector_enabled",
        lambda _skill=None: True,
    )
    diag = dict(_OK_DIAG)
    diag["data_quality"] = "stale"
    result = harvest_scan(
        tmp_path,
        diag,
        signals_found=0,
        scan_at="2026-08-25T13:30:00+00:00",
        persist_last_scan=False,
    )
    assert result["counted"] is False
    assert result["reason"] == "data_quality_not_ok"
    assert load_plugin_shadow_records(tmp_path) == []


def test_harvest_counts_and_upserts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "config.get_experiment_collector_enabled",
        lambda _skill=None: True,
    )
    first = harvest_scan(
        tmp_path,
        _OK_DIAG,
        signals_found=2,
        scan_at="2026-08-25T13:30:00+00:00",
        persist_last_scan=False,
    )
    second = harvest_scan(
        tmp_path,
        {**_OK_DIAG, "correlation_guard_would_demote": 4},
        signals_found=5,
        scan_at="2026-08-25T18:00:00+00:00",
        persist_last_scan=False,
    )
    assert first["counted"] is True
    assert second["counted"] is True
    assert second["action"] == "replaced"
    rows = load_plugin_shadow_records(tmp_path)
    assert len(rows) == 1
    assert rows[0]["signals_found"] == 5
    assert rows[0]["correlation_guard"]["would_demote"] == 4


def test_harvest_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "config.get_experiment_collector_enabled",
        lambda _skill=None: False,
    )
    result = harvest_scan(tmp_path, _OK_DIAG, persist_last_scan=False)
    assert result["reason"] == "disabled"
    assert result["counted"] is False
    assert load_plugin_shadow_records(tmp_path) == []


def test_brief_packet_miss_pings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "core.experiment_collector.campaign_plugins",
        lambda _skill: [
            {"id": "regime_v2", "label": "Regime v2", "mode": "shadow", "collected": 2, "target": 5, "ok": False}
        ],
    )
    now = datetime(2026, 8, 25, 16, 15, tzinfo=TZ_NY)
    packet = build_brief_packet(tmp_path, now=now)
    assert packet["scan_today"] is False
    assert packet["reason"] == "missing_harvest"
    assert packet["ping"] is True
    assert packet["campaign_fire"] is False
    text = format_daily_brief(packet)
    assert "missing_harvest" in text


def test_campaign_fire_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugins = [
        {"id": "regime_v2", "label": "Regime v2", "mode": "shadow", "collected": 5, "target": 5, "ok": True},
        {
            "id": "correlation_guard",
            "label": "Correlation guard",
            "mode": "shadow",
            "collected": 5,
            "target": 5,
            "ok": True,
        },
    ]
    monkeypatch.setattr("core.experiment_collector.campaign_plugins", lambda _skill: plugins)
    harvest_dir = tmp_path / "validation_artifacts" / "experiment_collector"
    harvest_dir.mkdir(parents=True)
    (harvest_dir / "harvest_state.json").write_text(
        '{"et_date": "2026-08-25", "counted": true, "reason": "counted", "data_quality": "ok", "signals_found": 3}',
        encoding="utf-8",
    )
    now = datetime(2026, 8, 25, 16, 15, tzinfo=TZ_NY)
    packet = build_brief_packet(tmp_path, now=now)
    assert packet["campaign_complete"] is True
    assert packet["campaign_fire"] is True
    assert campaign_key(plugins) == "correlation_guard,regime_v2"
    mark_campaign_notified(tmp_path, packet)
    again = build_brief_packet(tmp_path, now=now)
    assert again["campaign_fire"] is False
    assert again["campaign_complete"] is True


def test_harvest_appends_hypotheses_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "config.get_experiment_collector_enabled",
        lambda _skill=None: True,
    )
    monkeypatch.setattr(
        "config.get_hypothesis_ledger_enabled",
        lambda _skill=None: True,
    )
    signals = [
        {"ticker": "AAA", "price": 10.0, "signal_score": 40, "mirofish_conviction": 30},
    ]
    first = harvest_scan(
        tmp_path,
        _OK_DIAG,
        signals=signals,
        signals_found=1,
        scan_at="2026-08-25T13:30:00+00:00",
        persist_last_scan=False,
        now=datetime(2026, 8, 25, 9, 30, tzinfo=TZ_NY),
    )
    second = harvest_scan(
        tmp_path,
        _OK_DIAG,
        signals=signals,
        signals_found=1,
        scan_at="2026-08-25T18:00:00+00:00",
        persist_last_scan=False,
        now=datetime(2026, 8, 25, 14, 0, tzinfo=TZ_NY),
    )
    assert first["hypotheses_appended"] == 1
    assert second["hypotheses_appended"] == 0
    assert second["hypotheses_skipped"] == 1


def test_harvest_appends_s1_canary_hypotheses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "config.get_experiment_collector_enabled",
        lambda _skill=None: True,
    )
    monkeypatch.setattr(
        "config.get_hypothesis_ledger_enabled",
        lambda _skill=None: True,
    )
    diag = {
        **_OK_DIAG,
        "strategy_pead_primary_mode": "shadow",
        "strategy_pead_primary_effective_mode": "shadow",
        "pead_primary_capacity_rank_arm": "top5_by_edge_score",
        "pead_primary_capacity_rank_top_n": 5,
        "pead_primary_shadow_names": [
            {
                "ticker": "PEAD1",
                "edge_score": 91.0,
                "pead_beat": True,
                "pead_surprise_pct": 0.08,
                "capacity_top_n": True,
                "executable": False,
            }
        ],
    }
    first = harvest_scan(
        tmp_path,
        diag,
        signals=[{"ticker": "AAA", "price": 10.0, "signal_score": 40}],
        signals_found=1,
        scan_at="2026-09-03T14:30:00+00:00",
        persist_last_scan=False,
        now=datetime(2026, 9, 3, 10, 30, tzinfo=TZ_NY),
    )
    second = harvest_scan(
        tmp_path,
        diag,
        signals=[{"ticker": "AAA", "price": 10.0, "signal_score": 40}],
        signals_found=1,
        scan_at="2026-09-03T15:00:00+00:00",
        persist_last_scan=False,
        now=datetime(2026, 9, 3, 11, 0, tzinfo=TZ_NY),
    )
    assert first["s1_hypotheses_appended"] == 1
    assert second["s1_hypotheses_appended"] == 0
    from hypothesis_ledger import load_ledger_data

    s1 = [r for r in load_ledger_data(tmp_path)["records"] if r.get("sleeve_id") == "S1"]
    assert len(s1) == 1
    assert s1[0]["raw_context"]["executable"] is False


def test_harvest_hypotheses_when_plugins_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "config.get_experiment_collector_enabled",
        lambda _skill=None: True,
    )
    monkeypatch.setattr(
        "config.get_hypothesis_ledger_enabled",
        lambda _skill=None: True,
    )
    diag = {
        "regime_v2_mode": "off",
        "correlation_guard_mode": "off",
        "strategy_pead_primary_mode": "off",
        "entry_timing_shadow_mode": "off",
        "rank_filter_v2_mode": "off",
        "prob_rank_mode": "off",
        "data_quality": "ok",
    }
    result = harvest_scan(
        tmp_path,
        diag,
        signals=[{"ticker": "CCC", "price": 8.0, "signal_score": 20}],
        scan_at="2026-08-25T13:30:00+00:00",
        persist_last_scan=False,
        now=datetime(2026, 8, 25, 9, 30, tzinfo=TZ_NY),
    )
    assert result["reason"] == "plugins_off"
    assert result["counted"] is False
    assert result["hypotheses_appended"] == 1


def test_count_ok_sessions_entry_timing() -> None:
    spec = PluginSpec(
        "entry_timing",
        "Entry timing",
        "ENTRY_TIMING_SHADOW_MODE",
        getter=lambda _skill=None: "shadow",
        scope="scan",
        evidence_key="entry_timing",
    )
    rows = [
        {
            "scan_at": "2026-08-25T13:30:00+00:00",
            "data_quality": "ok",
            "entry_timing": {"mode": "shadow", "would_filter_any": 3},
        }
    ]
    assert count_ok_sessions(rows, spec) == 1


def test_harvest_skips_post_close(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "config.get_experiment_collector_enabled",
        lambda _skill=None: True,
    )
    result = harvest_scan(
        tmp_path,
        _OK_DIAG,
        signals_found=0,
        scan_at="2026-08-26T22:56:55+00:00",
        persist_last_scan=False,
        now=datetime(2026, 8, 26, 18, 56, tzinfo=TZ_NY),
    )
    assert result["counted"] is False
    assert result["reason"] == "not_rth"
    assert load_plugin_shadow_records(tmp_path) == []


def test_harvest_failure_is_persisted_and_pings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(_skill: Path | None = None) -> bool:
        raise RuntimeError("collector config broke")

    monkeypatch.setattr("config.get_experiment_collector_enabled", _boom)
    result = harvest_scan(
        tmp_path,
        _OK_DIAG,
        signals_found=4,
        scan_at="2026-08-25T13:30:00+00:00",
        persist_last_scan=False,
    )
    assert result["counted"] is False
    assert result["reason"] == "harvest_failed"
    assert result["error"] == "RuntimeError"
    assert result["et_date"] == "2026-08-25"
    state_path = tmp_path / "validation_artifacts" / "experiment_collector" / "harvest_state.json"
    assert state_path.exists()
    packet = build_brief_packet(tmp_path, now=datetime(2026, 8, 25, 16, 15, tzinfo=TZ_NY))
    assert packet["ping"] is True
    assert packet["reason"] == "harvest_failed"
    text = format_daily_brief(packet)
    assert "harvest_failed" in text
    assert "RuntimeError" in text
    assert "Do not promote" in text
    assert "Open question" in text


def test_collector_defaults_on_when_unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EXPERIMENT_COLLECTOR_ENABLED", raising=False)
    from config import clear_env_cache, get_experiment_collector_enabled

    clear_env_cache()
    assert get_experiment_collector_enabled(tmp_path) is True
    (tmp_path / ".env").write_text("EXPERIMENT_COLLECTOR_ENABLED=false\n", encoding="utf-8")
    clear_env_cache()
    assert get_experiment_collector_enabled(tmp_path) is False


def test_count_ok_sessions_skips_post_close() -> None:
    spec = _spec()
    rows = [
        {
            "scan_at": "2026-08-25T17:54:00+00:00",
            "data_quality": "ok",
            "regime_v2": {"mode": "shadow"},
        },
        {
            "scan_at": "2026-08-26T22:56:55+00:00",
            "data_quality": "ok",
            "regime_v2": {"mode": "shadow"},
        },
    ]
    assert count_ok_sessions(rows, spec) == 1
