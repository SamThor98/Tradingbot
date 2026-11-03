"""Operator-facing backtest labels — no underscores, catalog display names."""

from __future__ import annotations

from webapp.backtest_labels import (
    attach_run_labels,
    exit_reason_label,
    format_backtest_label,
    status_label,
    window_years_from_spec,
)


def test_campaign_theory_uses_catalog_names() -> None:
    spec = {
        "theory_name": "wknd w33 sp500 trend_breakout+pead_primary 2y stage2_52w_pct=0.85",
        "universe_preset": "sp500",
        "strategy_ids": ["trend_breakout", "pead_primary"],
        "start_date": "2024-01-01",
        "end_date": "2026-01-01",
    }
    out = format_backtest_label(spec)
    assert "_" not in out["title"]
    assert "_" not in out["subtitle"]
    assert out["title"].startswith("Wave 33")
    assert "S&P 500" in out["title"]
    assert "Stage 2 / VCP breakout" in out["title"]
    assert "PEAD earnings drift" in out["title"]
    assert "2 years" in out["title"]
    assert "Near 52-week high 0.85" in out["subtitle"]
    assert out["parts"]["window_years"] == 2


def test_prose_theory_becomes_subtitle_when_catalog_title_exists() -> None:
    spec = {"theory_name": "tighter 52-week, Nasdaq-100 only", "universe_preset": "nasdaq100"}
    out = format_backtest_label(spec)
    assert "Nasdaq-100" in out["title"]
    assert "tighter 52-week, Nasdaq-100 only" in out["subtitle"]
    assert "_" not in out["title"]


def test_date_preset_theory_is_not_the_title() -> None:
    spec = {
        "theory_name": "last 8 years",
        "universe_preset": "sp1500",
        "strategy_ids": ["trend_breakout"],
        "start_date": "2018-09-14",
        "end_date": "2026-09-14",
    }
    out = format_backtest_label(spec)
    assert out["title"] != "last 8 years"
    assert "S&P 1500" in out["title"]
    assert "Stage 2" in out["title"]


def test_underscore_theory_is_humanized() -> None:
    spec = {"theory_name": "manual_check"}
    out = format_backtest_label(spec)
    assert "_" not in out["title"]
    assert "Manual" in out["title"]


def test_status_and_exit_labels() -> None:
    assert status_label("queued") == "Waiting"
    assert status_label("success") == "Done"
    assert exit_reason_label("trailing_stop") == "Trailing stop"
    assert "_" not in exit_reason_label("vcp_invalidation")


def test_window_years_from_dates() -> None:
    years = window_years_from_spec({"start_date": "2018-01-01", "end_date": "2026-01-01"})
    assert years == 8


def test_attach_run_labels() -> None:
    item = {
        "status": "queued",
        "spec": {
            "theory_name": "wknd w36 nasdaq100 pullback 2y",
            "universe_preset": "nasdaq100",
            "strategy_ids": ["pullback"],
        },
    }
    attach_run_labels(item)
    assert item["status_label"] == "Waiting"
    assert "Nasdaq-100" in item["display_label"]
    assert "Trend pullback" in item["display_label"]
    assert "_" not in item["display_label"]


def test_short_sp1500_label_shows_the_loaded_count() -> None:
    item = {
        "status": "success",
        "spec": {
            "universe_preset": "sp1500",
            "strategy_ids": ["weekly_swing"],
            "start_date": "2018-09-22",
            "end_date": "2026-09-22",
        },
        "result_summary": {"membership_input_universe": 100},
    }
    attach_run_labels(item)
    label = item["display_label"]
    assert isinstance(label, str)
    assert label.endswith("· 100 names")
