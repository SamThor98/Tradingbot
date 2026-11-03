"""Rule-based backtest landing outcomes — no LLM."""

from __future__ import annotations

import pytest

from webapp.backtest_outcomes import evaluate_run


def test_failed_run_asks_to_fix_and_requeue() -> None:
    out = evaluate_run(status="failed", error_message="date range invalid")
    assert out["outcome"]["tone"] == "bad"
    assert out["next_steps"][0]["id"] == "fix_and_requeue"


def test_zero_trades_says_widen() -> None:
    out = evaluate_run(status="success", result={"total_trades": 0, "profit_factor_net": 0})
    assert out["outcome"]["tone"] == "warn"
    assert any(s["id"] == "widen_universe" for s in out["next_steps"])


def test_few_trades_is_not_a_verdict() -> None:
    out = evaluate_run(
        status="success",
        result={"total_trades": 4, "profit_factor_net": 1.4, "max_drawdown_net_pct": 5},
    )
    assert out["outcome"]["tone"] == "warn"
    assert any(s["id"] == "need_more_trades" for s in out["next_steps"])


def test_pf_below_one_blocks_promotion() -> None:
    out = evaluate_run(
        status="success",
        result={"total_trades": 20, "profit_factor_net": 0.82, "max_drawdown_net_pct": 8},
    )
    assert out["outcome"]["tone"] == "bad"
    assert any(s["id"] == "do_not_promote" for s in out["next_steps"])


def test_pf_between_one_and_gate_iterates() -> None:
    out = evaluate_run(
        status="success",
        spec={
            "universe_preset": "sp1500",
            "strategy_ids": ["trend_breakout"],
            "start_date": "2018-01-01",
            "end_date": "2026-01-01",
        },
        result={"total_trades": 120, "profit_factor_net": 1.08, "max_drawdown_net_pct": 8},
    )
    assert out["outcome"]["tone"] == "warn"
    assert any(s["id"] in {"iterate_window", "confirm_longer_window"} for s in out["next_steps"])


def test_pf_clears_gate_but_not_live_ready() -> None:
    out = evaluate_run(
        status="success",
        spec={
            "universe_preset": "sp1500",
            "strategy_ids": ["trend_breakout"],
            "start_date": "2018-01-01",
            "end_date": "2026-01-01",
        },
        result={"total_trades": 180, "profit_factor_net": 1.35, "max_drawdown_net_pct": 9},
    )
    assert out["outcome"]["tone"] == "ok"
    assert "not LIVE-ready" in out["outcome"]["summary"]
    assert any(s["id"] == "check_related" for s in out["next_steps"])


def test_thin_high_pf_asks_to_confirm_larger_index() -> None:
    out = evaluate_run(
        status="success",
        spec={
            "universe_preset": "sector_etfs",
            "strategy_ids": ["opening_range_breakout"],
            "start_date": "2024-01-01",
            "end_date": "2026-01-01",
        },
        result={"total_trades": 37, "profit_factor_net": 4.14, "max_drawdown_net_pct": 4.5},
    )
    assert out["outcome"]["tone"] == "warn"
    assert "thin" in out["outcome"]["headline"].lower()
    confirm = next(s for s in out["next_steps"] if s["id"] == "confirm_larger_universe")
    assert confirm["cta"]["spec"]["universe_preset"] == "nasdaq100"
    assert "Nasdaq-100 and S&P 1500" in confirm["title"]
    assert "promote" not in confirm["action"].lower() or "do not promote" in confirm["action"].lower()
    assert any(s["id"] == "confirm_longer_window" for s in out["next_steps"])


def test_large_n_two_year_winner_stresses_five_year() -> None:
    out = evaluate_run(
        status="success",
        spec={
            "universe_preset": "nasdaq100",
            "strategy_ids": ["weekly_swing"],
            "start_date": "2024-01-01",
            "end_date": "2026-01-01",
        },
        result={"total_trades": 321, "profit_factor_net": 2.03, "max_drawdown_net_pct": 9.5},
    )
    assert out["outcome"]["tone"] == "ok"
    longer = next(s for s in out["next_steps"] if s["id"] == "confirm_longer_window")
    assert longer["cta"]["spec"]["start_date"] == "2021-01-01"
    assert longer["cta"]["spec"]["overrides"]["skip_mirofish"] is True


def test_deep_drawdown_tightens_risk() -> None:
    out = evaluate_run(
        status="success",
        result={
            "total_trades": 140,
            "profit_factor_net": 1.40,
            "max_drawdown_net_pct": 31,
            "total_return_net_pct": 18,
        },
    )
    assert out["outcome"]["tone"] == "warn"
    assert any(s["id"] == "tighten_risk" for s in out["next_steps"])


def test_queue_busy_asks_to_wait() -> None:
    out = evaluate_run(status="queued", queue_busy=True, queue_position=2)
    assert out["next_steps"][0]["id"] == "wait_queue"
    assert "position 2" in out["outcome"]["headline"].lower()


def test_capacity_driver_queues_higher_positions() -> None:
    spec = {
        "schema_version": 1,
        "universe_mode": "watchlist",
        "start_date": "2020-01-01",
        "end_date": "2025-01-01",
        "overrides": {"backtest_portfolio_max_positions": 10},
    }
    out = evaluate_run(
        status="success",
        spec=spec,
        result={
            "total_trades": 20,
            "profit_factor_net": 1.05,
            "max_drawdown_net_pct": 8,
            "diagnostics": {"entries": 20, "capital_filtered": 12, "position_limit_filtered": 4},
            "portfolio_summary": {"peak_concurrent": 10, "capacity_filtered": 16},
        },
    )
    assert any(d["id"] == "capacity" for d in out["briefing"]["drivers"])
    cap = next(s for s in out["next_steps"] if s["id"] == "raise_capacity")
    assert cap["cta"]["kind"] == "queue_spec"
    assert cap["cta"]["spec"]["overrides"]["backtest_portfolio_max_positions"] > 10


def test_fallback_data_has_no_queue_cta() -> None:
    out = evaluate_run(
        status="success",
        spec={"start_date": "2020-01-01", "end_date": "2025-01-01"},
        result={
            "total_trades": 140,
            "profit_factor_net": 1.3,
            "max_drawdown_net_pct": 8,
            "data_integrity": {"history_fallback_used": 40, "history_fetch_total": 100},
        },
    )
    assert out["briefing"]["data_quality"]["tone"] == "warn"
    assert any(s["id"] == "fix_data" for s in out["next_steps"])
    assert all(s.get("cta", {}).get("kind") != "queue_spec" for s in out["next_steps"] if s["id"] == "fix_data")


def test_vs_previous_headline() -> None:
    from webapp.backtest_outcomes import build_run_briefing

    brief = build_run_briefing(
        {"total_trades": 20, "profit_factor_net": 1.10, "total_return_net_pct": 8, "max_drawdown_net_pct": 9},
        previous_summary={"profit_factor_net": 1.30, "total_trades": 22, "total_return_net_pct": 12, "max_drawdown_net_pct": 7},
    )
    assert brief["vs_previous"] is not None
    assert "PF" in brief["vs_previous"]["headline"]
    assert brief["vs_previous"]["pf_delta"] == pytest.approx(-0.20)


def test_cancelled_run_is_neutral() -> None:
    out = evaluate_run(status="cancelled", spec={"start_date": "2020-01-01", "end_date": "2025-01-01"})
    assert out["outcome"]["tone"] == "neutral"
    assert "cancelled" in out["outcome"]["headline"].lower()


def test_trades_sample_converts_fraction_return() -> None:
    from webapp.backtest_outcomes import build_run_briefing

    brief = build_run_briefing(
        {
            "total_trades": 1,
            "trades_sample": [{"ticker": "AAPL", "net_return": 0.012, "exit_reason": "time"}],
        }
    )
    assert brief["trades_sample"][0]["net_return_pct"] == pytest.approx(1.2)


def test_inf_pf_is_not_a_champion() -> None:
    out = evaluate_run(
        status="success",
        spec={
            "universe_preset": "sector_etfs",
            "strategy_ids": ["weekly_reversal"],
            "start_date": "2024-01-01",
            "end_date": "2026-01-01",
        },
        result={"total_trades": 3, "profit_factor_net": "inf", "max_drawdown_net_pct": 1.0},
    )
    assert out["outcome"]["tone"] == "warn"
    assert "no losing trades" in out["outcome"]["headline"].lower()
    assert "n=3" in out["outcome"]["summary"]
    assert "champion" in out["outcome"]["headline"].lower() or "not a champion" in out["outcome"]["headline"].lower()
    confirm = next(s for s in out["next_steps"] if s["id"] == "confirm_larger_universe")
    assert "Nasdaq-100 and S&P 1500" in confirm["title"]
    assert "do not promote" in confirm["action"].lower()


def test_single_window_clear_names_the_universe() -> None:
    out = evaluate_run(
        status="success",
        spec={
            "universe_preset": "nasdaq100",
            "strategy_ids": ["weekly_vcp"],
            "start_date": "2018-01-01",
            "end_date": "2026-01-01",
        },
        result={"total_trades": 461, "profit_factor_net": 1.6, "max_drawdown_net_pct": 8.5},
    )
    headline = out["outcome"]["headline"].lower()
    assert "looks good" not in headline
    assert "nasdaq100" in headline or "nasdaq 100" in headline
    assert "not the signed" in out["outcome"]["summary"].lower()


def test_inf_pf_large_n_still_not_a_champion() -> None:
    out = evaluate_run(
        status="success",
        spec={
            "universe_preset": "nasdaq100",
            "strategy_ids": ["weekly_vcp"],
            "start_date": "2018-01-01",
            "end_date": "2026-01-01",
        },
        result={"total_trades": 120, "profit_factor_net": "inf", "max_drawdown_net_pct": 8},
    )
    assert "looks good" not in out["outcome"]["headline"].lower()
    assert "no losing trades" in out["outcome"]["headline"].lower()


def test_configured_hold_is_not_realized_duration() -> None:
    from webapp.backtest_outcomes import build_run_briefing

    brief = build_run_briefing(
        {
            "total_trades": 40,
            "profit_factor_net": 1.3,
            "avg_holding_days": 40,
        }
    )
    assert brief["hold_note"] is not None
    assert "configured hold" in brief["hold_note"]
    assert "not a measured" in brief["hold_note"]
    assert any(d["id"] == "configured_hold" for d in brief["drivers"])


def test_horizon_actions_disable_current_window_and_skip_mirofish() -> None:
    from webapp.backtest_outcomes import build_horizon_actions

    spec = {
        "universe_preset": "nasdaq100",
        "strategy_ids": ["weekly_vcp"],
        "start_date": "2024-01-01",
        "end_date": "2026-01-01",
    }
    actions = {row["years"]: row for row in build_horizon_actions(spec, current_years=2)}
    assert actions[2]["available"] is False
    assert actions[2]["reason"] == "current"
    assert actions[5]["available"] is True
    assert actions[5]["cta"]["kind"] == "queue_spec"
    assert actions[5]["cta"]["spec"]["start_date"] == "2021-01-01"
    assert actions[5]["cta"]["spec"]["overrides"]["skip_mirofish"] is True
    assert actions[8]["cta"]["spec"]["overrides"]["skip_mirofish"] is True
    assert "skip_mirofish" not in (actions[2].get("cta") or {}).get("spec", {}).get("overrides", {})


def test_horizon_actions_prefer_existing_run() -> None:
    from webapp.backtest_outcomes import build_horizon_actions

    spec = {
        "universe_preset": "sp500",
        "strategy_ids": ["monthly_pullback"],
        "start_date": "2024-01-01",
        "end_date": "2026-01-01",
    }
    actions = {row["years"]: row for row in build_horizon_actions(spec, current_years=2, existing_by_years={5: "run-5"})}
    assert actions[5]["available"] is True
    assert actions[5]["existing_run_id"] == "run-5"
    assert actions[5]["label"] == "Already have 5y"
    assert actions[5]["cta"] is None


def test_briefing_short_has_verdict_tiles_and_chips() -> None:
    out = evaluate_run(
        status="success",
        spec={
            "universe_preset": "nasdaq100",
            "strategy_ids": ["weekly_vcp"],
            "start_date": "2024-01-01",
            "end_date": "2026-01-01",
        },
        result={
            "total_trades": 120,
            "profit_factor_net": 1.98,
            "total_return_net_pct": 22.0,
            "max_drawdown_net_pct": 9.0,
            "avg_holding_days": 40,
            "data_integrity": {
                "history_fallback_used": 40,
                "history_fetch_total": 100,
                "membership_filter_mode": "named_universe_live_constituents",
            },
        },
    )
    short = out["briefing"]["short"]
    assert short["verdict"]
    assert short["tiles"]["profit_factor_net"] == 1.98
    assert short["tiles"]["total_trades"] == 120
    labels = [c["label"] for c in short["chips"]]
    assert "not Schwab-clean" in labels
    assert "live membership" in labels
    assert "hold cap, not measured duration" in labels
    assert short["primary"] is not None
    assert len(out["horizon_actions"]) == 3
    assert out["outcome"]["tone"] == "warn"
    assert "today's constituents" in out["outcome"]["summary"]


def test_too_short_history_is_not_a_strategy_result() -> None:
    out = evaluate_run(
        status="success",
        spec={
            "universe_preset": "sp1500",
            "start_date": "2025-10-06",
            "end_date": "2026-10-06",
        },
        result={
            "total_trades": 0,
            "profit_factor_net": 0,
            "findings": "No valid timeline generated over the requested window.",
            "data_integrity": {"history_fetch_total": 100, "history_fetch_too_short": 100},
        },
    )
    assert out["outcome"]["headline"] == "Price history was too short"
    assert out["outcome"]["tone"] == "warn"
    assert any(step["id"] == "widen_history" for step in out["next_steps"])


def test_short_sp1500_book_is_not_a_clear() -> None:
    out = evaluate_run(
        status="success",
        spec={
            "universe_preset": "sp1500",
            "strategy_ids": ["weekly_swing"],
            "start_date": "2018-09-22",
            "end_date": "2026-09-22",
        },
        result={
            "total_trades": 1058,
            "profit_factor_net": 1.35,
            "max_drawdown_net_pct": 9,
            "data_integrity": {
                "membership_filter_mode": "membership_missing_live_fallback",
                "membership_input_universe": 100,
                "history_fetch_total": 100,
            },
        },
    )
    assert out["outcome"]["tone"] == "warn"
    assert "100 names" in out["outcome"]["headline"]
    assert "membership file is missing" in out["outcome"]["summary"]
