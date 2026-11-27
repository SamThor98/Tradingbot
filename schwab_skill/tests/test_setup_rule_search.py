"""Setup-rule search: holdout, metrics, and the pass gate. No network."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from core.setup_rule_search import (
    Clause,
    Rule,
    SearchGates,
    _norm_ppf,
    audit_exit_reasons,
    expected_max_z,
    load_trade_records,
    normalize_trades,
    parse_hypotheses,
    quality_floor_report,
    search_rules,
)
from core.shadow_setup_filter import (
    ShadowSetupThresholds,
    evaluate_rules,
    record_final_candidates,
)
from core.tab_agent_lab import CHICAGO, LabConfig, run_night

GATES = SearchGates(min_oos_n=40, min_discovery_n=40, min_era_n=20, era_majority=0.6, min_voting_eras=3)
GRID = {"signal_score": (30.0, 34.0, 36.0, 38.0)}


def _dates(start: str, count: int) -> list[pd.Timestamp]:
    return list(pd.bdate_range(start, periods=count))


def _trade(day: pd.Timestamp, score: float, ret: float, *, reason: str = "time_exit") -> dict:
    return {
        "entry_date": day.date().isoformat(),
        "exit_date": (day + pd.Timedelta(days=5)).date().isoformat(),
        "net_return": ret,
        "signal_score": score,
        "stop_pct": 0.05,
        "exit_reason": reason,
        "atr_pct": 0.03,
        "pct_from_52w_high": 0.96,
        "sector_breadth50": 0.40,
    }


def _era_book(per_side: int, low_return: float, high_return: float) -> list[dict]:
    rows: list[dict] = []
    starts = ("2016-01-04", "2018-01-02", "2020-01-02", "2022-01-03", "2024-01-02")
    for start in starts:
        days = _dates(start, per_side * 2)
        for index, day in enumerate(days):
            low = index < per_side
            rows.append(_trade(day, 30.0 if low else 50.0, low_return if low else high_return))
    return rows


def _score_rules() -> list[Rule]:
    return [
        Rule((Clause("signal_score", "<=", 30.0),)),
        Rule((Clause("signal_score", "<=", 34.0),)),
        Rule((Clause("signal_score", "<=", 36.0),), name="R1"),
        Rule((Clause("signal_score", "<=", 38.0),)),
    ]


def test_norm_ppf_and_single_trial_has_no_haircut() -> None:
    assert _norm_ppf(0.975) == pytest.approx(1.95996398454, abs=1e-6)
    assert expected_max_z(1) == 0.0
    assert expected_max_z(100) > 2.0


def test_metrics_and_shadow_rule_agreement() -> None:
    day = pd.Timestamp("2016-01-04")
    records = [
        _trade(day, 30.0, 0.10),
        _trade(day + pd.Timedelta(days=1), 30.0, -0.05),
        _trade(day + pd.Timedelta(days=2), 30.0, 0.0),
    ]
    report = search_rules(
        records, catalog=_score_rules(), grids=GRID, gates=SearchGates(min_oos_n=1, min_discovery_n=1, min_era_n=1)
    )
    rule = next(row for row in report["rules"] if row["name"] == "R1")
    assert rule["n"] == 3
    assert rule["mean_return"] == pytest.approx(0.05 / 3, abs=1e-6)
    assert rule["win_rate"] == pytest.approx(1 / 3)
    assert rule["trades_per_day"] == pytest.approx(1.0)
    assert rule["mean_r"] == pytest.approx((2.0 - 1.0 + 0.0) / 3)
    assert rule["max_drawdown"] == pytest.approx(0.05, abs=1e-6)
    downside = np.array([0.0, -0.05, 0.0])
    expected_sortino = float(np.mean([0.10, -0.05, 0.0]) / np.sqrt(np.mean(np.minimum(downside, 0.0) ** 2)))
    assert rule["sortino"] == pytest.approx(expected_sortino, abs=1e-6)

    frame, notes = normalize_trades(records)
    assert notes["atr_pct_scale"] == "fraction_to_percent"
    assert notes["pct_below_source"] == "pct_from_52w_high"
    assert "pct_from_52w_high" not in notes["ignored_columns"]
    limits = ShadowSetupThresholds()
    flags = evaluate_rules(
        signal_score=30.0,
        pct_below_52w_high=0.04,
        sector_breadth50=0.40,
        atr_pct=3.0,
        thresholds=limits,
    )
    assert flags["r1_keep"] and flags["r1b_keep"] and flags["r2_keep"] and flags["r4_keep"] and flags["r3_keep"]
    wide = next(row for row in report["rules"] if row["rule_id"] == "signal_score<=36")
    assert wide["n"] == 3


def test_percent_below_and_atr_fraction_match_the_shadow_cutoffs() -> None:
    near = _trade(pd.Timestamp("2016-01-04"), 30.0, 0.01)
    near["pct_from_52w_high"] = 0.99
    near["atr_pct"] = 0.01
    far = _trade(pd.Timestamp("2016-01-05"), 30.0, 0.02)
    far["pct_from_52w_high"] = 0.96
    far["atr_pct"] = 0.03
    frame, _notes = normalize_trades([near, far])
    assert frame["pct_below_52w_high"].tolist() == pytest.approx([0.01, 0.04])
    assert frame["atr_pct"].tolist() == pytest.approx([1.0, 3.0])
    limits = ShadowSetupThresholds()
    near_flags = evaluate_rules(
        signal_score=30.0,
        pct_below_52w_high=float(frame["pct_below_52w_high"].iloc[0]),
        sector_breadth50=0.40,
        atr_pct=float(frame["atr_pct"].iloc[0]),
        thresholds=limits,
    )
    far_flags = evaluate_rules(
        signal_score=30.0,
        pct_below_52w_high=float(frame["pct_below_52w_high"].iloc[1]),
        sector_breadth50=0.40,
        atr_pct=float(frame["atr_pct"].iloc[1]),
        thresholds=limits,
    )
    assert near_flags["r1b_keep"] is False and near_flags["r4_keep"] is False
    assert far_flags["r1b_keep"] is True and far_flags["r4_keep"] is True


def test_passing_rule_beats_baseline_out_of_sample_and_across_eras() -> None:
    report = search_rules(_era_book(30, 0.02, -0.02), catalog=_score_rules(), grids=GRID, gates=GATES)
    winner = next(row for row in report["rules"] if row["name"] == "R1")
    assert winner["passed"] is True
    assert winner["reasons"] == []
    assert winner["oos"]["excess_mean"] > 0
    assert winner["era_beat"] >= 3
    assert report["cost_usd"] == 0.0
    assert report["orders_touched"] is False
    assert report["live_scan_changed"] is False
    assert report["stops_changed"] is False
    assert "R1" in {row["name"] for row in report["rules"] if row["passed"]}
    assert any(row["rule_id"] == winner["rule_id"] for row in report["shadow_recommendations"])


def test_oos_returns_do_not_change_the_discovery_score() -> None:
    base = _era_book(30, 0.02, -0.02)
    flipped = []
    for row in base:
        copied = dict(row)
        if copied["entry_date"] >= "2022-01-01":
            copied["net_return"] = -0.03 if copied["signal_score"] <= 36 else 0.04
        flipped.append(copied)
    before = search_rules(base, catalog=_score_rules(), grids=GRID, gates=GATES)
    after = search_rules(flipped, catalog=_score_rules(), grids=GRID, gates=GATES)
    before_rule = next(row for row in before["rules"] if row["name"] == "R1")
    after_rule = next(row for row in after["rules"] if row["name"] == "R1")
    assert before_rule["discovery"]["mean_return"] == pytest.approx(after_rule["discovery"]["mean_return"])
    assert before_rule["oos"]["mean_return"] != pytest.approx(after_rule["oos"]["mean_return"])
    assert before["walk_forward"][0]["selected"] == after["walk_forward"][0]["selected"]
    assert before["walk_forward"][2]["test"] == "2022_plus"
    assert before["walk_forward"][2]["test_excess_mean"] != pytest.approx(after["walk_forward"][2]["test_excess_mean"])
    assert after_rule["passed"] is False
    assert "oos_not_above_baseline" in after_rule["reasons"]


def test_overall_win_fails_when_most_eras_do_not() -> None:
    rows: list[dict] = []
    plan = {
        "2016-01-04": (0.20, -0.01),
        "2018-01-02": (-0.02, 0.03),
        "2020-01-02": (-0.02, 0.03),
        "2022-01-03": (-0.02, 0.03),
        "2024-01-02": (0.20, -0.01),
    }
    for start, (low, high) in plan.items():
        days = _dates(start, 60)
        for index, day in enumerate(days):
            is_low = index < 30
            rows.append(_trade(day, 30.0 if is_low else 50.0, low if is_low else high))
    report = search_rules(rows, catalog=_score_rules(), grids=GRID, gates=GATES)
    rule = next(row for row in report["rules"] if row["name"] == "R1")
    assert rule["mean_return"] > report["baseline"]["all"]["mean_return"]
    assert rule["passed"] is False
    assert "era_vote" in rule["reasons"]


def test_flat_book_does_not_pass() -> None:
    rows = _era_book(30, 0.01, 0.01)
    report = search_rules(rows, catalog=_score_rules(), grids=GRID, gates=GATES)
    assert report["passing"] == []
    assert all(row["passed"] is False for row in report["rules"])


def test_unstable_neighbor_fails() -> None:
    rows: list[dict] = []
    for start in ("2016-01-04", "2018-01-02", "2020-01-02", "2022-01-03", "2024-01-02"):
        days = _dates(start, 60)
        for index, day in enumerate(days):
            if index < 30:
                rows.append(_trade(day, 35.0, 0.04))
            else:
                rows.append(_trade(day, 37.0, -0.04))
    catalog = [Rule((Clause("signal_score", "<=", 36.0),), name="R1")]
    report = search_rules(rows, catalog=catalog, grids=GRID, gates=GATES)
    rule = next(row for row in report["rules"] if row["rule_id"] == "signal_score<=36")
    assert rule["passed"] is False
    assert "unstable_threshold" in rule["reasons"]


def test_exit_reason_labels_do_not_change_the_rule_and_are_audited() -> None:
    rows = _era_book(30, 0.02, -0.02)
    swapped = []
    for row in rows:
        copied = dict(row)
        copied["exit_reason"] = "trailing_stop" if row["net_return"] > 0 else "time_exit"
        copied["mae"] = -0.08 if copied["exit_reason"] == "time_exit" else -0.01
        swapped.append(copied)
    original = search_rules(rows, catalog=_score_rules(), grids=GRID, gates=GATES)
    relabeled = search_rules(swapped, catalog=_score_rules(), grids=GRID, gates=GATES)
    left = next(row for row in original["rules"] if row["name"] == "R1")
    right = next(row for row in relabeled["rules"] if row["name"] == "R1")
    assert left["mean_return"] == right["mean_return"]
    assert left["oos"]["excess_mean"] == right["oos"]["excess_mean"]
    frame, _notes = normalize_trades(swapped)
    audit = audit_exit_reasons(frame)
    assert "winner_profile_on_trailing_stop" in audit["flags"]
    assert "exit_reason" not in str(left["clauses"])


def test_quality_floor_is_the_effective_minimum_not_a_high_score_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUALITY_MIN_SIGNAL_SCORE", "50")
    monkeypatch.setenv("SCORE_PTS_SMA_MULTIPLIER", "0")
    monkeypatch.setenv("SCORE_PTS_SMA_CAP", "25")
    monkeypatch.setenv("QUALITY_REQUIRE_BREAKOUT_VOLUME", "false")
    report = quality_floor_report(Path("."))
    assert report["configured"] == 50
    assert report["effective"] == 25
    assert report["ceiling"] == 60
    assert report["scanner_uses"] == "get_quality_min_signal_score_effective"
    assert "does not cap high signal_score" in report["note"]


def test_hypotheses_keep_known_features_only() -> None:
    text = """
    Here is an idea.
    {"rules":[
      {"clauses":[{"feature":"signal_score","op":"<=","threshold":34}]},
      {"clauses":[{"feature":"stop_pct","op":"<=","threshold":0.04}]},
      {"feature":"atr_pct","op":"==","threshold":2}
    ]}
    """
    rules = parse_hypotheses(text, allowed={"signal_score", "atr_pct"})
    assert [rule.rule_id for rule in rules] == ["signal_score<=34"]


def test_chunk_directory_round_trip(tmp_path: Path) -> None:
    era = tmp_path / "late_bull"
    era.mkdir()
    payload = {"era": "late_bull", "trades": [_trade(pd.Timestamp("2016-06-01"), 33.0, 0.01)]}
    (era / "chunk_0001.json").write_text(json.dumps(payload), encoding="utf-8")
    (era / "chunk_0001_tickers.json").write_text("[]", encoding="utf-8")
    loaded = load_trade_records(tmp_path)
    assert len(loaded) == 1
    assert loaded[0]["signal_score"] == 33.0


def test_lab_does_not_start_outside_the_window(tmp_path: Path) -> None:
    report = run_night(
        tmp_path,
        config=LabConfig(enabled=True, tabs=("today",)),
        clock=lambda: datetime(2026, 11, 11, 12, 0, tzinfo=CHICAGO),
        promote=False,
        use_llm=False,
    )
    assert report["stop_reason"] == "outside_window"
    assert report["orders_touched"] is False
    assert report["tokens"] == 0
    assert not (tmp_path / ".tab_agent_lab" / "state.json").exists()


def test_night_report_includes_the_setup_phase_at_zero_cost(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SETUP_RULE_SEARCH_LLM", "false")
    monkeypatch.setenv("SETUP_RULE_SEARCH_TRADES_PATH", str(tmp_path / "pool"))
    pool = tmp_path / "pool" / "late_bull"
    pool.mkdir(parents=True)
    (pool / "chunk_0000.json").write_text(
        json.dumps({"era": "late_bull", "trades": _era_book(5, 0.01, -0.01)}),
        encoding="utf-8",
    )
    calls = {"n": 0}

    def _boom(*_args, **_kwargs):
        calls["n"] += 1
        raise AssertionError("the offline phase must not call the model")

    monkeypatch.setattr("core.tab_agent_lab._model_call", _boom)
    report = run_night(
        tmp_path,
        config=LabConfig(enabled=True, tabs=("today",), token_cap=100, usd_per_1k=0.01),
        clock=lambda: datetime(2026, 11, 11, 18, 30, tzinfo=CHICAGO),
        promote=False,
        use_llm=True,
    )
    assert calls["n"] == 0
    assert report["tokens"] == 0
    assert report["spend_usd"] == 0.0
    assert report["orders_touched"] is False
    setup = report["setup_rules"]
    assert setup["cost_usd"] == 0.0
    assert setup["live_scan_changed"] is False
    assert setup["n_trades"] > 0
    text = (tmp_path / ".tab_agent_lab" / "reports" / "2026-11-11.md").read_text(encoding="utf-8")
    assert "Setup rules" in text
    assert "baseline" in text
    assert "Live scan output unchanged" in text
    assert "Orders touched: False" in text


def test_recommended_shadow_rule_is_logged_and_does_not_drop_the_signal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SHADOW_SETUP_FILTER_ENABLED", "true")
    monkeypatch.setenv("SHADOW_SETUP_FILTER_RECOMMENDED", "true")
    folder = tmp_path / "data" / "shadow_setup_filter"
    folder.mkdir(parents=True)
    (folder / "recommended_rules.json").write_text(
        json.dumps(
            {
                "rules": [
                    {
                        "rule_id": "signal_score<=36",
                        "name": "R1",
                        "clauses": [{"feature": "signal_score", "op": "<=", "threshold": 36}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    signals = [
        {"ticker": "LOW", "price": 10.0, "signal_score": 30.0, "sector_etf": "XLK"},
        {"ticker": "HIGH", "price": 10.0, "signal_score": 40.0, "sector_etf": "XLK"},
    ]
    copied = json.loads(json.dumps(signals))
    diagnostics: dict = {"scan_id": "s", "_shadow_breadth_samples": {}, "_shadow_bar_features": {}}
    written = record_final_candidates(signals, diagnostics, skill_dir=tmp_path)
    assert written == 2
    assert signals == copied
    from core.shadow_setup_filter import load_rows

    rows = {row["ticker"]: row for row in load_rows(tmp_path) if row["record_type"] == "signal"}
    assert rows["LOW"]["recommended_keep"] == ["signal_score<=36"]
    assert rows["HIGH"]["recommended_keep"] == []
    assert diagnostics["shadow_setup_filter_recommended_rules"] == 1
