"""Dashboard backtest leaderboard ranking — sample-size gates, not LIVE promotion."""

from __future__ import annotations

from webapp.backtest_leaderboard import MIN_TRADES, classify_run, rank_backtests


def _run(
    *,
    run_id: str,
    trades: int,
    pf: float | str,
    status: str = "success",
    years: int = 2,
    dd: float = 8.0,
    ret: float = 12.0,
    universe: str = "nasdaq100",
    strategy: str = "trend_breakout",
    theory: str | None = None,
) -> dict:
    return {
        "id": run_id,
        "status": status,
        "has_result": status == "success",
        "spec": {
            "theory_name": theory or f"wknd w1 {universe} {strategy} {years}y",
            "universe_preset": universe,
            "strategy_ids": [strategy],
            "start_date": "2024-01-01",
            "end_date": "2026-01-01" if years == 2 else "2029-01-01",
        },
        "result_summary": {
            "total_trades": trades,
            "profit_factor_net": pf,
            "max_drawdown_net_pct": dd,
            "total_return_net_pct": ret,
            "win_rate_net": 48.0,
        },
        "window_years": years,
    }


def test_inf_pf_and_tiny_samples_are_excluded() -> None:
    board = rank_backtests(
        [
            _run(run_id="inf", trades=40, pf="inf"),
            _run(run_id="tiny", trades=8, pf=3.5),
            _run(run_id="fail", trades=80, pf=1.4, status="failed"),
        ]
    )
    assert board["qualified"] == []
    assert board["thin"] == []
    assert board["excluded_count"] == 3
    assert board["criteria"]["min_trades"] == MIN_TRADES


def test_thin_sample_is_not_ranked() -> None:
    board = rank_backtests([_run(run_id="thin", trades=20, pf=2.1)])
    assert board["qualified"] == []
    assert len(board["thin"]) == 1
    assert board["thin"][0]["reason"] == "Too few trades to rank."
    assert "rank" not in board["thin"][0]


def test_sorts_by_trust_then_pf() -> None:
    board = rank_backtests(
        [
            _run(run_id="low", trades=40, pf=1.3, dd=5, ret=10, strategy="pullback"),
            _run(run_id="best", trades=40, pf=1.8, dd=12, ret=9, strategy="trend_breakout"),
            _run(run_id="more-trades", trades=90, pf=1.3, dd=5, ret=10, strategy="pead_primary"),
        ]
    )
    ids = [row["id"] for row in board["qualified"]]
    assert ids == ["best", "more-trades", "low"]
    assert board["qualified"][0]["rank"] == 1
    assert "_" not in board["qualified"][0]["display_label"]
    assert board["criteria"]["sort"] == "trust_then_robustness_then_pf"


def test_core_large_n_outranks_thin_etf_fireworks() -> None:
    board = rank_backtests(
        [
            _run(
                run_id="etf",
                trades=37,
                pf=4.14,
                universe="sector_etfs",
                strategy="opening_range_breakout",
                ret=7.9,
            ),
            _run(
                run_id="nq",
                trades=321,
                pf=2.03,
                universe="nasdaq100",
                strategy="weekly_swing",
                ret=100.0,
            ),
        ]
    )
    ids = [row["id"] for row in board["qualified"]]
    assert ids[0] == "nq"
    assert board["qualified"][0]["trust_label"] == "Core large-n"
    assert board["qualified"][1]["trust_label"] == "Thin index"


def test_clones_collapse_to_one_cluster() -> None:
    board = rank_backtests(
        [
            _run(run_id="a", trades=43, pf=2.78, universe="sector_etfs", strategy="weekly_breakout", theory="wknd w50 sector_etfs weekly_breakout 2y stage2_52w_pct=0.96"),
            _run(run_id="b", trades=38, pf=2.62, universe="sector_etfs", strategy="weekly_breakout", theory="wknd w46 sector_etfs weekly_breakout 2y stage2_52w_pct=0.92"),
        ]
    )
    assert board["qualified_count"] == 1
    row = board["qualified"][0]
    assert row["variant_count"] == 2
    assert row["id"] == "a"
    assert row["cluster_pf_min"] == 2.62
    assert row["cluster_pf_max"] == 2.78
    assert "Wave" not in row["display_label"]


def test_window_payload_keeps_longer_windows() -> None:
    runs = [_run(run_id=f"y2-{i}", trades=40, pf=2.4 - i * 0.01, years=2, strategy=f"sleeve_{i % 3}") for i in range(40)]
    runs.append(_run(run_id="y8", trades=50, pf=1.21, years=8))
    board = rank_backtests(runs)
    ids = {row["id"] for row in board["qualified"]}
    assert "y8" in ids
    eight = next(row for row in board["qualified"] if row["id"] == "y8")
    assert eight["window_years"] == 8


def test_missing_window_is_excluded() -> None:
    run = _run(run_id="nowindow", trades=50, pf=1.4)
    run["spec"] = {"universe_preset": "sp500"}
    run["window_years"] = None
    assert classify_run(run) == "excluded"


def test_cross_universe_robustness_outranks_single_book_fireworks() -> None:
    board = rank_backtests(
        [
            _run(
                run_id="swing-nq",
                trades=324,
                pf=2.10,
                universe="nasdaq100",
                strategy="weekly_swing",
                ret=105.0,
            ),
            _run(
                run_id="vcp-nq",
                trades=115,
                pf=1.98,
                universe="nasdaq100",
                strategy="weekly_vcp",
                ret=40.0,
            ),
            _run(
                run_id="vcp-spx",
                trades=256,
                pf=1.76,
                universe="sp500",
                strategy="weekly_vcp",
                ret=22.0,
            ),
            _run(
                run_id="vcp-sp1500",
                trades=102,
                pf=1.73,
                universe="sp1500",
                strategy="weekly_vcp",
                ret=18.0,
            ),
        ]
    )
    ids = [row["id"] for row in board["qualified"]]
    assert ids[0] == "vcp-nq"
    assert "swing-nq" in ids
    assert ids.index("vcp-nq") < ids.index("swing-nq")
    vcp = next(row for row in board["qualified"] if row["id"] == "vcp-nq")
    assert vcp["robustness_universes"] == ["nasdaq100", "sp500", "sp1500"]
    assert vcp["robustness_horizons"] == [2]
    assert vcp["robustness_pf_min"] == 1.73
    assert vcp["robustness_score"] > 300
    swing = next(row for row in board["qualified"] if row["id"] == "swing-nq")
    assert swing["robustness_universes"] == ["nasdaq100"]
    assert swing["robustness_score"] < vcp["robustness_score"]


def test_horizon_min_pf_is_on_clustered_rows() -> None:
    board = rank_backtests(
        [
            _run(run_id="v2", trades=120, pf=1.98, years=2, strategy="weekly_vcp"),
            _run(run_id="v5", trades=140, pf=1.10, years=5, strategy="weekly_vcp"),
            _run(run_id="v8", trades=160, pf=0.90, years=8, strategy="weekly_vcp"),
        ]
    )
    two = next(row for row in board["qualified"] if row["id"] == "v2")
    assert two["robustness_horizons"] == [2, 5, 8]
    assert two["robustness_pf_min"] == 0.90


def test_sector_etf_thin_sample_has_reason_not_rank() -> None:
    board = rank_backtests(
        [
            _run(
                run_id="etf-thin",
                trades=10,
                pf=28.39,
                universe="sector_etfs",
                strategy="st_reversal_5d",
            )
        ]
    )
    assert board["qualified"] == []
    assert board["thin"][0]["id"] == "etf-thin"
    assert "Thin index" in board["thin"][0]["reason"]
    assert "rank" not in board["thin"][0]


def test_fallback_share_flags_not_schwab_clean() -> None:
    run = _run(run_id="fb", trades=120, pf=1.5, strategy="weekly_vcp")
    run["result_summary"]["fallback_share"] = 1.0
    run["result_summary"]["membership_filter_mode"] = "named_universe_live_constituents"
    board = rank_backtests([run])
    row = board["qualified"][0]
    assert row["not_schwab_clean"] is True
    assert row["fallback_share"] == 1.0
    assert row["membership_filter_mode"] == "named_universe_live_constituents"
    assert row["live_membership"] is True
    assert row["variant_count"] == 1


def test_leaderboard_rows_include_horizon_actions() -> None:
    board = rank_backtests(
        [
            _run(run_id="v2", trades=120, pf=1.98, years=2, strategy="weekly_vcp"),
            _run(run_id="v5", trades=140, pf=1.10, years=5, strategy="weekly_vcp"),
        ]
    )
    two = next(row for row in board["qualified"] if row["id"] == "v2")
    actions = {row["years"]: row for row in two["horizon_actions"]}
    assert "_spec" not in two
    assert actions[2]["available"] is False
    assert actions[2]["reason"] == "current"
    assert actions[5]["existing_run_id"] == "v5"
    assert actions[5]["label"] == "Already have 5y"
    assert actions[8]["available"] is True
    assert actions[8]["cta"]["kind"] == "queue_spec"
    assert actions[8]["cta"]["spec"]["overrides"]["skip_mirofish"] is True
