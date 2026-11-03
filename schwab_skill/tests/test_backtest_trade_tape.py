"""Best/worst close ranks for the live backtest monitor."""

from __future__ import annotations

from core.backtest_trade_tape import rank_closed_trades
from webapp.backtest_hub import annotate_run_list_item, result_metrics_summary


def test_rank_skips_overlap_when_the_sample_is_short() -> None:
    trades = [
        {"ticker": "AAA", "net_return": 0.12, "exit_reason": "trailing_stop", "exit_date": "2024-01-02T00:00:00"},
        {"ticker": "BBB", "net_return_pct": 4.0, "exit_reason": "time_exit"},
        {"ticker": "CCC", "net_return": -0.02, "exit_reason": "sma50_break"},
        {"ticker": "", "net_return": 0.5},
        {"ticker": "DDD", "net_return": "nope"},
    ]
    ranks = rank_closed_trades(trades, limit=5)
    assert ranks["closed"] == 3
    assert [row["ticker"] for row in ranks["best"]] == ["AAA", "BBB", "CCC"]
    assert ranks["best"][0]["net_return_pct"] == 12.0
    assert ranks["worst"] == []


def test_rank_splits_a_full_ledger() -> None:
    trades = [{"ticker": f"T{i}", "net_return_pct": float(i)} for i in range(12)]
    ranks = rank_closed_trades(trades, limit=5)
    best = [row["ticker"] for row in ranks["best"]]
    worst = [row["ticker"] for row in ranks["worst"]]
    assert best == ["T11", "T10", "T9", "T8", "T7"]
    assert worst == ["T0", "T1", "T2", "T3", "T4"]
    assert set(best).isdisjoint(worst)
    short = rank_closed_trades([{"ticker": f"S{i}", "net_return_pct": float(8 - i)} for i in range(8)], limit=5)
    short_best = {row["ticker"] for row in short["best"]}
    short_worst = [row["ticker"] for row in short["worst"]]
    assert len(short["best"]) == 5
    assert short_worst == ["S7", "S6", "S5"]
    assert short_best.isdisjoint(short_worst)


def test_list_item_keeps_live_progress_out_of_finished_results() -> None:
    item: dict = {"id": "run-1"}
    annotate_run_list_item(
        item,
        {
            "progress": {
                "closed": 4,
                "best": [{"ticker": "AAA", "net_return_pct": 8.0}],
                "worst": [{"ticker": "BBB", "net_return_pct": -3.0}],
            }
        },
    )
    assert item["has_result"] is False
    assert "result_summary" not in item
    assert item["progress"]["closed"] == 4
    assert item["progress"]["best"][0]["ticker"] == "AAA"


def test_summary_includes_trade_ranks_from_a_finished_result() -> None:
    summary = result_metrics_summary(
        {
            "total_trades": 20,
            "win_rate_net": 55.0,
            "profit_factor_net": 1.4,
            "trade_ranks": {
                "closed": 20,
                "best": [{"ticker": "AAA", "net_return_pct": 11.0}],
                "worst": [{"ticker": "ZZZ", "net_return_pct": -8.0}],
            },
        }
    )
    assert summary is not None
    assert summary["trade_ranks"]["best"][0]["ticker"] == "AAA"
    assert summary["trade_ranks"]["worst"][0]["ticker"] == "ZZZ"
