"""Shadow neutral rank and weekly_swing entry buffer.

Default config must leave live order and membership unchanged. Shadow mode
still records ranks, cut survival, and the 1% weekly_swing buffer.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

import pandas as pd
import pytest

import signal_scanner
from config import get_neutral_rank_mode, get_weekly_swing_entry_buffer_mode
from core.neutral_rank import neutral_score_from_signal, stamp_neutral_rank
from core.shadow_tags import SHADOW_TAGS_JSONL, attach_shadow_tags, shadow_persistence_fields
from stage_analysis import evaluate_weekly_swing_entry_buffer, weekly_swing_entry_buffer_blocks


def _clear(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    for name in names:
        monkeypatch.delenv(name, raising=False)


def test_neutral_score_removes_52w_and_volume_points() -> None:
    score = neutral_score_from_signal(
        {
            "signal_score": 90.0,
            "score_components": {"pts_52w": 37.0, "pts_volume": 18.0, "pts_mirofish": 12.0},
        }
    )
    assert score == pytest.approx(35.0)


def test_neutral_score_clamps_and_treats_missing_points_as_zero() -> None:
    assert neutral_score_from_signal({"signal_score": 10.0, "pts_52w": 40.0}) == 0.0
    assert neutral_score_from_signal({"signal_score": 22.0}) == 22.0


def test_default_modes_are_shadow(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch, "NEUTRAL_RANK_MODE", "WEEKLY_SWING_ENTRY_BUFFER_MODE")
    assert get_neutral_rank_mode(tmp_path) == "shadow"
    assert get_weekly_swing_entry_buffer_mode(tmp_path) == "shadow"


def test_shadow_rank_logs_both_orders_without_reordering(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch, "NEUTRAL_RANK_MODE")
    monkeypatch.setenv("RANK_FILTER_SHADOW_MIN_PERCENTILE_RANK_V2", "76")
    signals: list[dict[str, Any]] = [
        {
            "ticker": "CHASE",
            "entry_family": "stage2",
            "signal_score": 90.0,
            "sort_score": 90.0,
            "rank_score_v2": 90.0,
            "score_components": {"pts_52w": 40.0, "pts_volume": 20.0},
        },
        {
            "ticker": "QUIET",
            "entry_family": "stage2",
            "signal_score": 40.0,
            "sort_score": 40.0,
            "rank_score_v2": 40.0,
            "score_components": {"pts_52w": 0.0, "pts_volume": 0.0, "pts_mirofish": 15.0},
        },
        {
            "ticker": "MID",
            "entry_family": "stage2",
            "signal_score": 50.0,
            "sort_score": 50.0,
            "rank_score_v2": 50.0,
            "score_components": {"pts_52w": 10.0, "pts_volume": 10.0},
        },
        {
            "ticker": "WEEK",
            "entry_family": "horizon",
            "signal_score": 70.0,
            "sort_score": 70.0,
            "rank_score_v2": 5.0,
            "horizon_hits": ["weekly_swing"],
            "score_components": {"pts_52w": 30.0, "pts_volume": 15.0},
            "weekly_swing_entry_buffer": {"applies": True, "would_cut": True, "mode": "shadow"},
        },
    ]
    original = [row["ticker"] for row in signals]
    diagnostics: dict[str, Any] = {}
    stamp_neutral_rank(signals, diagnostics, tmp_path, scan_id="scan-1", persist=True, exempt_research=True)

    assert [row["ticker"] for row in signals] == original
    assert [row["sort_score"] for row in signals] == [90.0, 40.0, 50.0, 70.0]
    assert diagnostics["neutral_rank_mode"] == "shadow"
    assert diagnostics["neutral_rank_cut_enforced"] == 0
    by_ticker = {row["ticker"]: row["neutral_rank"] for row in signals}
    assert by_ticker["CHASE"]["live_rank"] == 1
    assert by_ticker["QUIET"]["shadow_rank"] == 1
    assert by_ticker["CHASE"]["shadow_rank"] != 1
    assert by_ticker["WEEK"]["live_rank_v2_exempt"] is True
    assert by_ticker["WEEK"]["survives_live_rank_v2"] is True
    assert by_ticker["CHASE"]["survives_live_rank_v2"] is True
    assert by_ticker["QUIET"]["survives_shadow_rank_v2"] is True
    assert any(row["neutral_rank"]["survives_shadow_rank_v2"] is False for row in signals)
    assert "entry_timing" in signals[3]["shadow_tags"]

    lines = (tmp_path / SHADOW_TAGS_JSONL).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4
    payload = json.loads(lines[0])
    assert payload["record_type"] == "ranked_signal"
    assert payload["scan_id"] == "scan-1"
    assert "live_rank" in payload and "shadow_rank" in payload


def test_live_flag_is_opt_in_for_the_sort_helper(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from core.neutral_rank import apply_neutral_sort

    monkeypatch.setenv("NEUTRAL_RANK_MODE", "live")
    signals: list[dict[str, Any]] = [
        {
            "ticker": "CHASE",
            "entry_family": "stage2",
            "signal_score": 90.0,
            "sort_score": 90.0,
            "neutral_rank": {"neutral_score": 30.0, "applied_to_sort": False},
        },
        {
            "ticker": "WEEK",
            "entry_family": "horizon",
            "signal_score": 80.0,
            "sort_score": 80.0,
            "neutral_rank": {"neutral_score": 10.0, "applied_to_sort": False},
        },
    ]
    assert apply_neutral_sort(signals, {}, tmp_path) is True
    assert signals[0]["sort_score"] == 30.0
    assert signals[1]["sort_score"] == 80.0
    assert signals[0]["neutral_rank"]["applied_to_sort"] is True


def test_shadow_tags_cover_the_five_filters() -> None:
    tags = attach_shadow_tags(
        {
            "stage_a_penalties": ["stage2_shadow_would_filter"],
            "entry_timing_shadow": {"would_filter": True, "would_filter_reasons": ["breakout_buffer_low"]},
            "rank_filter_shadow": {"would_drop_any": True},
            "latest_volume": 50.0,
            "avg_vol_50": 100.0,
            "mirofish_result": {"bull_trap_probability": 0.6, "continuation_probability": 0.4},
        }
    )
    assert tags == ["stage2", "entry_timing", "rank", "weak_breakout_volume", "weak_mirofish"]
    stored = shadow_persistence_fields({"shadow_tags": tags, "neutral_rank": {"live_rank": 1}})
    assert stored["shadow_tags"] == tags
    assert stored["neutral_rank"]["live_rank"] == 1


def test_weekly_swing_buffer_shadow_does_not_block(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch, "WEEKLY_SWING_ENTRY_BUFFER_MODE", "ENTRY_SHADOW_MIN_BREAKOUT_BUFFER_PCT")
    block = evaluate_weekly_swing_entry_buffer(
        horizon_hits=["weekly_swing"],
        entry_shadow={"breakout_buffer_pct": 0.004},
        skill_dir=tmp_path,
    )
    assert block["applies"] is True
    assert block["mode"] == "shadow"
    assert block["would_cut"] is True
    assert block["reason"] == "breakout_buffer_low"
    assert weekly_swing_entry_buffer_blocks(block) is False

    clear = evaluate_weekly_swing_entry_buffer(
        horizon_hits=[{"name": "weekly_swing"}],
        entry_shadow={"breakout_buffer_pct": 0.02},
        skill_dir=tmp_path,
    )
    assert clear["would_cut"] is False
    assert evaluate_weekly_swing_entry_buffer(horizon_hits=["donchian_20"], skill_dir=tmp_path)["applies"] is False


def test_weekly_swing_buffer_live_blocks_only_when_flag_is_on(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEEKLY_SWING_ENTRY_BUFFER_MODE", "live")
    frame = pd.DataFrame(
        {
            "open": [10.0, 10.0, 10.0],
            "high": [10.0, 10.0, 10.2],
            "low": [9.0, 9.0, 9.5],
            "close": [9.5, 10.0, 10.05],
            "volume": [100.0, 100.0, 100.0],
        }
    )
    block = evaluate_weekly_swing_entry_buffer(horizon_hits=["weekly_swing"], df=frame, skill_dir=tmp_path)
    assert block["would_cut"] is True
    assert weekly_swing_entry_buffer_blocks(block) is True

    monkeypatch.setenv("WEEKLY_SWING_ENTRY_BUFFER_MODE", "off")
    skipped = evaluate_weekly_swing_entry_buffer(
        horizon_hits=["weekly_swing"],
        entry_shadow={"breakout_buffer_pct": 0.0},
        skill_dir=tmp_path,
    )
    assert skipped["applies"] is False
    assert weekly_swing_entry_buffer_blocks(skipped) is False


def test_quality_summary_reports_unique_tickers_beside_sums(tmp_path) -> None:
    diagnostics = {
        "stage2_shadow_would_filter": 2,
        "shadow_tickers": {"stage2": ["AAA", "CCC"], "entry_timing": ["AAA"]},
    }
    signal_scanner._record_quality_snapshot(
        tmp_path,
        diagnostics,
        [{"ticker": "aaa", "signal_score": 10, "mirofish_conviction": 1, "shadow_tags": ["stage2"]}],
    )
    signal_scanner._record_quality_snapshot(
        tmp_path,
        {"stage2_shadow_would_filter": 1, "shadow_tickers": {"stage2": ["AAA"]}},
        [{"ticker": "AAA", "signal_score": 20, "mirofish_conviction": 2}],
    )
    summary = signal_scanner.get_signal_quality_summary(tmp_path, days=7)
    assert summary["signals_total"] == 2
    assert summary["signals_unique"] == 1
    assert summary["diagnostics"]["stage2_shadow_would_filter"] == 3
    assert summary["shadow_tickers_unique"]["stage2"] == 2
    assert summary["shadow_tickers_unique"]["entry_timing"] == 1


def _record_nonfatal_stub(*_args: Any, **_kwargs: Any) -> None:
    return None


@pytest.fixture
def hermetic_chain(monkeypatch: pytest.MonkeyPatch):
    import sys
    import types

    monkeypatch.setitem(
        sys.modules,
        "self_study",
        types.SimpleNamespace(get_learned_min_conviction=lambda *_a, **_k: None),
    )
    monkeypatch.setitem(
        sys.modules,
        "strategy_plugins",
        types.SimpleNamespace(
            apply_strategy_ensemble=lambda *, signals, diagnostics, regime_v2_snapshot, skill_dir: signals
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "agent_intelligence",
        types.SimpleNamespace(
            apply_meta_policy_to_signal=lambda *, signal, diagnostics, skill_dir: (signal, True),
            log_counterfactual_event=lambda *_a, **_k: False,
            meta_policy_counterfactual_reason=lambda **_k: None,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "feature_store",
        types.SimpleNamespace(log_stage_b_signal=lambda *_a, **_k: None),
    )
    monkeypatch.setattr(signal_scanner, "_evaluate_quality_gates", lambda *_a, **_k: [])
    monkeypatch.setattr(
        signal_scanner,
        "_apply_event_risk_policy_to_signals",
        lambda signals, diagnostics, skill_dir: signals,
    )
    monkeypatch.setattr(signal_scanner, "_record_quality_snapshot", lambda *_a, **_k: None)
    for var in (
        "QUALITY_GATES_MODE",
        "EVENT_RISK_MODE",
        "CORRELATION_GUARD_MODE",
        "HISTORICAL_RANK_MODE",
        "ANALOG_OVERLAY_MODE",
        "PROB_RANK_MODE",
        "RANK_FILTER_V2_MODE",
    ):
        monkeypatch.setenv(var, "off" if var != "RANK_FILTER_V2_MODE" else "shadow")


def _chain_signals() -> list[dict[str, Any]]:
    return [
        {
            "ticker": "CHASE",
            "entry_family": "stage2",
            "signal_score": 90.0,
            "composite_score": 90.0,
            "rank_score": 90.0,
            "rank_score_v2": 90.0,
            "score_components": {"pts_52w": 40.0, "pts_volume": 20.0},
            "mirofish_conviction": 10.0,
        },
        {
            "ticker": "MID",
            "entry_family": "stage2",
            "signal_score": 50.0,
            "composite_score": 50.0,
            "rank_score": 50.0,
            "rank_score_v2": 50.0,
            "score_components": {"pts_52w": 10.0, "pts_volume": 10.0},
            "mirofish_conviction": 10.0,
        },
        {
            "ticker": "QUIET",
            "entry_family": "stage2",
            "signal_score": 40.0,
            "composite_score": 40.0,
            "rank_score": 40.0,
            "rank_score_v2": 40.0,
            "score_components": {"pts_52w": 0.0, "pts_volume": 0.0},
            "mirofish_conviction": 10.0,
        },
    ]


def _run_chain(signals: list[dict[str, Any]], skill_dir, monkeypatch: pytest.MonkeyPatch):
    del monkeypatch
    diagnostics: dict[str, Any] = defaultdict(int)
    out = signal_scanner._apply_post_stage_b_chain(
        signals,
        diagnostics,
        skill_dir=skill_dir,
        scan_id="chain",
        top_n=0,
        regime_v2_snapshot=None,
        regime_v2_mode="off",
        capture_shortlist=None,
        record_nonfatal=_record_nonfatal_stub,
    )
    return out, diagnostics


def test_default_chain_keeps_signal_score_order(tmp_path, hermetic_chain, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch, "NEUTRAL_RANK_MODE", "SCAN_LIVE_SORT_KEY")
    out, diagnostics = _run_chain(_chain_signals(), tmp_path, monkeypatch)
    assert [row["ticker"] for row in out] == ["CHASE", "MID", "QUIET"]
    assert diagnostics["neutral_rank_mode"] == "shadow"
    assert diagnostics["neutral_rank_applied"] == 0
    chase = next(row for row in out if row["ticker"] == "CHASE")
    quiet = next(row for row in out if row["ticker"] == "QUIET")
    assert chase["neutral_rank"]["live_rank"] == 1
    assert quiet["neutral_rank"]["shadow_rank"] == 1
    assert chase["neutral_rank"]["applied_to_sort"] is False
    logged = [json.loads(line) for line in (tmp_path / SHADOW_TAGS_JSONL).read_text(encoding="utf-8").splitlines()]
    assert {row["ticker"] for row in logged} == {"CHASE", "MID", "QUIET"}


def test_live_chain_sorts_by_neutral_score(tmp_path, hermetic_chain, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEUTRAL_RANK_MODE", "live")
    out, diagnostics = _run_chain(_chain_signals(), tmp_path, monkeypatch)
    assert [row["ticker"] for row in out] == ["QUIET", "CHASE", "MID"]
    assert diagnostics["neutral_rank_applied"] == 3
    assert all(row["neutral_rank"]["applied_to_sort"] is True for row in out)


def test_live_chain_reorders_tied_weekly_swing_by_neutral_score(
    tmp_path, hermetic_chain, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [
        {
            "ticker": "HIGH",
            "entry_family": "horizon",
            "signal_score": 80.0,
            "composite_score": 80.0,
            "rank_score": 80.0,
            "rank_score_v2": 10.0,
            "score_components": {"pts_52w": 35.0, "pts_volume": 20.0},
            "strategy_plugins": [{"name": "weekly_swing", "triggered": True, "raw_score": 100.0}],
            "mirofish_conviction": 1.0,
        },
        {
            "ticker": "LOW",
            "entry_family": "horizon",
            "signal_score": 30.0,
            "composite_score": 30.0,
            "rank_score": 30.0,
            "rank_score_v2": 10.0,
            "score_components": {"pts_52w": 0.0, "pts_volume": 0.0},
            "strategy_plugins": [{"name": "weekly_swing", "triggered": True, "raw_score": 100.0}],
            "mirofish_conviction": 1.0,
        },
    ]
    _clear(monkeypatch, "NEUTRAL_RANK_MODE")
    shadow, _shadow_diag = _run_chain([dict(row) for row in rows], tmp_path, monkeypatch)
    assert [row["ticker"] for row in shadow] == ["HIGH", "LOW"]

    monkeypatch.setenv("NEUTRAL_RANK_MODE", "live")
    live, _live_diag = _run_chain(rows, tmp_path, monkeypatch)
    assert [row["ticker"] for row in live] == ["LOW", "HIGH"]
