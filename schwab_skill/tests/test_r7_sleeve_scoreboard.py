"""S0 T+5 scoreboard matches S1 cardinality. Promotion still sees every row."""

from __future__ import annotations

from pathlib import Path

from core.experiment_collector import _r7_snapshot, format_daily_brief
from core.multi_sleeve_constitution import TOP_N_S0
from core.r7_promotion import (
    day_clustered_mean_ci,
    r7_may_promote,
    select_top_n_per_et_date,
)
from hypothesis_ledger import save_ledger_data

DAY = "2026-01-05"


def _s0(ticker: str, day: str, score: float | None, ret: float) -> dict:
    raw = {} if score is None else {"signal_score": score}
    return {
        "ticker": ticker,
        "sleeve_id": "S0",
        "source": "signal_scanner",
        "et_date": day,
        "raw_context": raw,
        "outcomes": {"5": {"thesis_hit": ret > 0, "return_pct": ret}},
    }


def test_top_n_tie_keeps_alphabetical_name() -> None:
    records = [_s0(f"W{i:02d}", DAY, 100 - i, 1.0) for i in range(max(0, TOP_N_S0 - 1))]
    records.extend(
        [
            _s0("ZZZ", DAY, 1.0, 1.0),
            _s0("MMM", DAY, 1.0, 1.0),
            _s0("AAA", DAY, 1.0, 1.0),
        ]
    )
    records.append(
        {
            "ticker": "PEAD",
            "sleeve_id": "S1",
            "source": "pead_canary",
            "et_date": DAY,
            "raw_context": {"edge_score": 99},
            "outcomes": {"5": {"thesis_hit": True, "return_pct": 4.0}},
        }
    )
    kept = select_top_n_per_et_date(records, sleeve_id="S0", top_n=TOP_N_S0)
    tickers = [row["ticker"] for row in kept]
    assert len(tickers) == TOP_N_S0
    assert tickers[-1] == "AAA"
    assert "MMM" not in tickers
    assert "ZZZ" not in tickers
    assert "PEAD" not in tickers


def test_short_day_keeps_every_name() -> None:
    count = max(1, TOP_N_S0 - 2)
    records = [_s0(f"T{i}", "2026-02-02", 10 - i, 1.0) for i in range(count)]
    records.append(
        {
            "ticker": "CCC",
            "sleeve_id": "S0",
            "created_at": "2026-02-03T15:00:00+00:00",
            "raw_context": {"signal_score": 3},
            "outcomes": {"5": {"thesis_hit": True, "return_pct": 1.0}},
        }
    )
    kept = select_top_n_per_et_date(records, sleeve_id="S0", top_n=TOP_N_S0)
    assert len(kept) == count + 1
    assert [row["ticker"] for row in kept if row["ticker"].startswith("T")] == [f"T{i}" for i in range(count)]
    assert any(row["ticker"] == "CCC" for row in kept)


def test_duplicate_ticker_same_day_keeps_best_score() -> None:
    records = [
        _s0("AAA", DAY, 1.0, -5.0),
        _s0("AAA", DAY, 9.0, 4.0),
        *[_s0(f"B{i}", DAY, 8 - i, 1.0) for i in range(TOP_N_S0)],
    ]
    kept = select_top_n_per_et_date(records, sleeve_id="S0", top_n=TOP_N_S0)
    aaa = [row for row in kept if row["ticker"] == "AAA"]
    assert len(aaa) == 1
    assert aaa[0]["raw_context"]["signal_score"] == 9.0
    assert len(kept) == TOP_N_S0


def test_day_cluster_ci_is_deterministic_and_spans_day_means() -> None:
    records: list[dict] = []
    for i in range(20):
        records.append(_s0(f"A{i}", "2026-03-02", 10, 1.0))
        records.append(_s0(f"B{i}", "2026-03-03", 10, 5.0))
    records.append(
        {
            "ticker": "S1X",
            "sleeve_id": "S1",
            "et_date": "2026-03-02",
            "outcomes": {"5": {"thesis_hit": False, "return_pct": -50.0}},
        }
    )
    first = day_clustered_mean_ci(records, sleeve_id="S0", horizon="5")
    second = day_clustered_mean_ci(records, sleeve_id="S0", horizon="5")
    assert first == second
    assert first["seed"] == 42
    assert first["method"] == "day_cluster_bootstrap"
    assert first["n"] == 40
    assert first["n_days"] == 2
    assert first["mean"] == 3.0
    assert first["low"] == 1.0
    assert first["high"] == 5.0
    assert first["low"] <= first["mean"] <= first["high"]

    empty = day_clustered_mean_ci([], sleeve_id="S0", horizon="5")
    assert empty["low"] is None
    assert empty["high"] is None
    assert empty["n"] == 0

    one_day_rows = [row for row in records if row.get("et_date") == "2026-03-02" and row.get("sleeve_id") == "S0"]
    one_day = day_clustered_mean_ci(one_day_rows, sleeve_id="S0", horizon="5")
    assert one_day["n_days"] == 1
    assert one_day["low"] == one_day["high"] == one_day["mean"]


def test_promotion_still_scores_every_s0_row() -> None:
    records = [
        {
            "sleeve_id": "S0",
            "ticker": f"T{i:02d}",
            "et_date": DAY,
            "raw_context": {"signal_score": 100 - i},
            "outcomes": {"5": {"thesis_hit": False, "return_pct": -0.5}},
        }
        for i in range(40)
    ]
    decision = r7_may_promote(records, sleeve_id="S0", offline_floors_ok=True)
    assert decision["ok"] is False
    assert decision["summary"]["n"] == 40
    assert any(str(reason).startswith("expectancy_non_positive") for reason in decision["reasons"])
    assert len(select_top_n_per_et_date(records, sleeve_id="S0", top_n=TOP_N_S0)) == TOP_N_S0


def test_snapshot_uses_constitution_cardinality(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("core.multi_sleeve_constitution.TOP_N_S0", 2)
    records = [
        _s0("AAA", DAY, 9, 4.0),
        _s0("BBB", DAY, 8, 2.0),
        _s0("CCC", DAY, 1, -10.0),
        _s0("DDD", "2026-01-06", 5, 1.0),
        {
            "ticker": "PEAD",
            "sleeve_id": "S1",
            "source": "pead_canary",
            "et_date": DAY,
            "outcomes": {"5": {"thesis_hit": True, "return_pct": 3.0}},
        },
    ]
    save_ledger_data({"schema": 1, "records": records}, tmp_path)
    snap = _r7_snapshot(tmp_path)
    assert snap is not None
    assert snap["S0"]["n"] == 3
    assert snap["S0"]["top_n"] == 2
    assert snap["S0"]["selection"] == "top_n_per_et_date"
    assert snap["S0 all"]["n"] == 4
    assert snap["S0 all"]["selection"] == "all_signals"
    assert snap["S1"]["n"] == 1
    assert snap["S1"]["selection"] == "recorded_names"
    assert "top_n" not in snap["S1"]
    assert snap["S0"]["mean_ci95"]["n_days"] == 2
    assert snap["S0"]["mean_ci95"]["low"] <= snap["S0"]["expectancy"] <= snap["S0"]["mean_ci95"]["high"]
    text = format_daily_brief(
        {
            "et_date": "2026-01-06",
            "counted": False,
            "reason": "counted",
            "plugins": [],
            "r7": snap,
        }
    )
    assert "S0 T+5 (top 2/day)" in text
    assert "S0 all T+5" in text
    assert "S1 T+5" in text
    assert "CI [" in text
    assert "Do not promote" in text
