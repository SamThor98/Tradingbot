from __future__ import annotations

import json
from pathlib import Path

from core.mirofish_live_compare import (
    build_mirofish_live_compare_report,
    load_last_scan,
    signal_has_live_mirofish,
)


def test_skip_when_no_signals() -> None:
    report = build_mirofish_live_compare_report(
        {"signals": [], "diagnostics": {}},
        backtest_skip_mirofish=True,
    )
    assert report["verdict"] == "skip"
    assert report["fidelity_gap"] == "none"
    assert report["live_mirofish_scored"] == 0
    assert report["live_mirofish_missing"] == 0
    assert report["backtest_skip_mirofish"] is True
    assert report["recommended_action"] == "No live scan signals to compare"


def test_skip_when_last_scan_missing() -> None:
    report = build_mirofish_live_compare_report(None, backtest_skip_mirofish=True)
    assert report["verdict"] == "skip"
    assert report["fidelity_gap"] == "none"
    assert report["recommended_action"] == "No live scan signals to compare"


def test_warn_when_live_has_convictions_and_skip_mirofish() -> None:
    last_scan = {
        "signals": [
            {
                "ticker": "AAPL",
                "mirofish_conviction": 72,
                "score_components": {"pts_mirofish": 8.0},
                "llm_model": "grok-4-fast",
                "llm_provider": "xai",
            },
            {"ticker": "MSFT", "mirofish_conviction": None, "score_components": {"pts_mirofish": 0.0}},
        ],
        "diagnostics": {},
    }
    report = build_mirofish_live_compare_report(last_scan, backtest_skip_mirofish=True)
    assert report["verdict"] == "warn"
    assert report["fidelity_gap"] == "live_llm_backtest_skipped"
    assert report["live_mirofish_scored"] == 1
    assert report["live_mirofish_missing"] == 1
    assert report["llm_model"] == "grok-4-fast"
    assert report["llm_provider"] == "xai"
    assert "Do not compare live rank_v2" in str(report["recommended_action"])


def test_pass_when_both_absent() -> None:
    last_scan = {
        "signals": [
            {"ticker": "MSFT", "mirofish_conviction": None, "score_components": {"pts_mirofish": 0.0}},
            {"ticker": "IBM", "pts_mirofish": 0.0},
        ],
        "diagnostics": {},
    }
    report = build_mirofish_live_compare_report(last_scan, backtest_skip_mirofish=True)
    assert report["verdict"] == "pass"
    assert report["fidelity_gap"] == "both_absent"
    assert report["live_mirofish_scored"] == 0
    assert report["live_mirofish_missing"] == 2
    assert "absent on both sides" in str(report["recommended_action"])


def test_pass_when_both_present() -> None:
    last_scan = {
        "signals": [
            {"ticker": "NVDA", "mirofish_conviction": 55, "score_components": {"pts_mirofish": 4.0}},
        ],
        "diagnostics": {"llm_model": "gpt-4.1-mini", "llm_provider": "openai"},
    }
    report = build_mirofish_live_compare_report(last_scan, backtest_skip_mirofish=False)
    assert report["verdict"] == "pass"
    assert report["fidelity_gap"] == "both_present"
    assert report["llm_model"] == "gpt-4.1-mini"


def test_fail_when_signals_malformed() -> None:
    report = build_mirofish_live_compare_report(
        {"signals": "AAPL", "diagnostics": {}},
        backtest_skip_mirofish=True,
    )
    assert report["verdict"] == "fail"
    assert report["fidelity_gap"] == "none"


def test_signal_has_live_mirofish_from_pts_only() -> None:
    assert signal_has_live_mirofish({"score_components": {"pts_mirofish": 3.5}})
    assert not signal_has_live_mirofish({"score_components": {"pts_mirofish": 0.0}})


def test_load_last_scan_from_diagnostics_json(tmp_path: Path) -> None:
    path = tmp_path / "last_scan.json"
    path.write_text(
        json.dumps(
            {
                "at": "2026-08-25T15:00:00Z",
                "signals_found": 1,
                "signals": [{"ticker": "AAPL", "mirofish_conviction": 40}],
                "diagnostics": {"llm_provider": "xai"},
            }
        ),
        encoding="utf-8",
    )
    payload, meta = load_last_scan(diagnostics_json=path)
    assert payload is not None
    assert meta["source"] == "diagnostics_json"
    report = build_mirofish_live_compare_report(payload, backtest_skip_mirofish=True)
    assert report["verdict"] == "warn"
    assert report["llm_provider"] == "xai"


def test_extracts_nested_mirofish_llm_diagnostics() -> None:
    last_scan = {
        "signals": [{"ticker": "AAPL", "mirofish_conviction": 11}],
        "diagnostics": {
            "mirofish_llm": {"provider": "xai", "model": "grok-4-fast", "weighting_mode": "shadow"}
        },
    }
    report = build_mirofish_live_compare_report(last_scan, backtest_skip_mirofish=True)
    assert report["llm_model"] == "grok-4-fast"
    assert report["llm_provider"] == "xai"
