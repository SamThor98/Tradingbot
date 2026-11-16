"""Post-admit drops are recorded without changing who the shortlist keeps."""

from __future__ import annotations

from typing import Any

from signal_scanner import (
    _append_pruned_admits,
    _assemble_stage_a_shortlist,
    _cap_horizon_cross_section,
    _primary_provider_rows,
    _split_primary_provider,
)


def _horizon(ticker: str, plugin_score: float, stage_a_score: float = 1.0) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "entry_family": "horizon",
        "executable": False,
        "stage_a_score": stage_a_score,
        "horizon_plugin_hits": [{"name": "weekly_swing", "raw_score": plugin_score, "triggered": True}],
    }


def _stage2(ticker: str, stage_a_score: float) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "entry_family": "stage2",
        "executable": True,
        "stage_a_score": stage_a_score,
    }


def _assemble(**kwargs: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "executable": [],
        "horizon_only": [],
        "pead_only": [],
        "want_stage2": True,
        "want_pead": False,
        "want_horizon": True,
        "top_n": 0,
        "multiplier": 3.0,
        "cap": 40,
        "nocap_limit": 250,
        "horizon_sidecar_cap": 40,
    }
    defaults.update(kwargs)
    return _assemble_stage_a_shortlist(**defaults)


def test_mixed_horizon_sidecar_records_names_below_cap() -> None:
    """Weekly scan shape: Stage 2 stays, horizon sidecar keeps 40, the tail is named."""
    executable = [_stage2("S0", 80.0), _stage2("S1", 70.0)]
    kept = [_horizon(f"H{i:02d}", plugin_score=99.0) for i in range(40)]
    tail = [
        _horizon("ANF", plugin_score=90.0),
        _horizon("APPF", plugin_score=89.0),
        _horizon("MRK", plugin_score=88.0),
    ]
    out = _assemble(executable=executable, horizon_only=kept + tail, horizon_sidecar_cap=40)

    assert out["dedicated_research"] is False
    assert out["stage_a_pruned"] == 0
    assert [c["ticker"] for c in out["shortlist"] if c["entry_family"] == "stage2"] == ["S0", "S1"]
    assert [c["ticker"] for c in out["horizon_extra"]] == [f"H{i:02d}" for i in range(40)]
    assert len(out["shortlist"]) == 42
    dropped = out["pruned_admits"]
    assert [row["ticker"] for row in dropped] == ["ANF", "APPF", "MRK"]
    for row, score in zip(dropped, (90.0, 89.0, 88.0), strict=True):
        assert row["strategy"] == "weekly_swing"
        assert row["stage"] == "horizon_shortlist"
        assert row["reason"] == "horizon_stage_b_cap"
        assert row["score"] == score
        assert row["sort_key"] == "plugin_raw_score_desc,ticker_asc"

    stored: dict[str, Any] = {}
    _append_pruned_admits(stored, list(out["pruned_admits"]))
    assert stored["pruned_admits_count"] == 3
    assert stored["pruned_admits"][0]["ticker"] == "ANF"


def test_horizon_tie_uses_ticker_and_still_names_the_loser() -> None:
    horizon = [_horizon("ZZZ", 50.0), _horizon("AAA", 50.0)]
    out = _assemble(executable=[], horizon_only=horizon, horizon_sidecar_cap=1)
    assert [c["ticker"] for c in out["horizon_extra"]] == ["AAA"]
    assert out["pruned_admits"][0]["ticker"] == "ZZZ"
    assert out["pruned_admits"][0]["score"] == 50.0


def test_executable_shortlist_records_stage_a_score_tail() -> None:
    executable = [_stage2(f"S{i}", float(100 - i)) for i in range(6)]
    out = _assemble(
        executable=executable,
        horizon_only=[],
        want_horizon=False,
        top_n=1,
        multiplier=1.0,
        cap=40,
    )
    assert [c["ticker"] for c in out["shortlist"]] == ["S0"]
    assert out["stage_a_pruned"] == 5
    tail = out["pruned_admits"]
    assert [row["ticker"] for row in tail] == ["S1", "S2", "S3", "S4", "S5"]
    assert tail[0]["strategy"] == "trend_breakout"
    assert tail[0]["stage"] == "executable_shortlist"
    assert tail[0]["reason"] == "stage_a_shortlist_limit"
    assert tail[0]["score"] == 99.0
    assert tail[0]["sort_key"] == "stage_a_score_desc,ticker_asc"


def test_dedicated_horizon_cap_records_shortlist_cap() -> None:
    horizon = [_horizon(f"H{i:02d}", plugin_score=float(100 - i)) for i in range(45)]
    out = _assemble(
        executable=[],
        horizon_only=horizon,
        want_stage2=False,
        want_horizon=True,
        top_n=20,
        multiplier=3.0,
        cap=40,
        horizon_sidecar_cap=40,
    )
    assert out["dedicated_research"] is True
    assert len(out["shortlist"]) == 40
    assert out["stage_a_pruned"] == 5
    assert [row["ticker"] for row in out["pruned_admits"]] == ["H40", "H41", "H42", "H43", "H44"]
    assert out["pruned_admits"][0]["reason"] == "stage_a_shortlist_cap"
    assert out["pruned_admits"][0]["stage"] == "horizon_shortlist"
    assert out["pruned_admits"][0]["strategy"] == "weekly_swing"


def test_show_all_nocap_records_overflow_without_trimming_inside_the_ceiling() -> None:
    horizon = [_horizon(f"H{i:02d}", plugin_score=float(80 - i)) for i in range(4)]
    out = _assemble(
        executable=[],
        horizon_only=horizon,
        want_stage2=False,
        top_n=0,
        nocap_limit=250,
        cap=40,
    )
    assert len(out["shortlist"]) == 4
    assert out["pruned_admits"] == []
    assert out["stage_a_pruned"] == 0


def test_cross_section_cap_records_the_dropped_sleeve() -> None:
    rows = []
    for i in range(12):
        rows.append(
            {
                "ticker": f"T{i:02d}",
                "entry_family": "horizon",
                "horizon_hits": ["momentum_12_1"],
                "horizon_plugin_hits": [
                    {
                        "name": "momentum_12_1",
                        "raw_score": float(i),
                        "triggered": True,
                        "meta": {"formation_return_pct": float(i)},
                    }
                ],
            }
        )
    capped, dropped = _cap_horizon_cross_section(rows)
    assert [row["ticker"] for row in capped] == [f"T{i:02d}" for i in range(2, 12)]
    assert [row["ticker"] for row in dropped] == ["T00", "T01"]
    assert dropped[0]["strategy"] == "momentum_12_1"
    assert dropped[0]["stage"] == "horizon_cross_section"
    assert dropped[0]["reason"] == "cross_section_keep"
    assert dropped[0]["sort_key"] == "formation_return_pct"
    assert dropped[0]["score"] == 0.0


def test_primary_provider_filter_records_non_primary_and_keeps_order() -> None:
    candidates = [
        {"ticker": "AAA", "entry_family": "stage2", "stage_a_score": 10, "data_provider_primary": True},
        {"ticker": "BBB", "entry_family": "horizon", "stage_a_score": 40, "data_provider_primary": False},
        {"ticker": "CCC", "entry_family": "stage2", "stage_a_score": 30, "data_provider_primary": True},
    ]
    kept, dropped = _split_primary_provider(candidates)
    assert [row["ticker"] for row in kept] == ["AAA", "CCC"]
    rows = _primary_provider_rows(dropped)
    assert rows == [
        {
            "ticker": "BBB",
            "strategy": "horizon",
            "stage": "primary_provider",
            "reason": "non_primary_provider",
            "score": 40.0,
            "sort_key": "stage_a_score_desc,ticker_asc",
        }
    ]


def test_pruned_admit_log_caps_payload_and_counts_the_rest() -> None:
    diagnostics: dict[str, Any] = {}
    rows = [
        {
            "ticker": f"T{i:03d}",
            "strategy": "weekly_swing",
            "stage": "horizon_shortlist",
            "reason": "horizon_stage_b_cap",
            "score": float(i),
            "sort_key": "plugin_raw_score_desc,ticker_asc",
        }
        for i in range(5)
    ]
    _append_pruned_admits(diagnostics, rows, limit=2)
    assert [row["ticker"] for row in diagnostics["pruned_admits"]] == ["T000", "T001"]
    assert diagnostics["pruned_admits_omitted"] == 3
    assert diagnostics["pruned_admits_count"] == 5


def test_strategy_filter_records_removed_signal_without_changing_the_kept_book(tmp_path, monkeypatch) -> None:
    from core import scan_service

    signals = [
        {
            "ticker": "AAPL",
            "entry_family": "stage2",
            "signal_score": 40.0,
            "strategy_attribution": {"top_live": "trend_breakout"},
        },
        {
            "ticker": "MSFT",
            "entry_family": "stage2",
            "signal_score": 70.0,
            "strategy_attribution": {"top_live": "trend_breakout"},
            "strategy_plugins": [{"name": "pullback", "triggered": True, "mode": "shadow", "raw_score": 50.0}],
        },
    ]

    def _fake_scan(**_kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        return list(signals), {"watchlist_source": "universe_sp1500"}

    monkeypatch.setattr(scan_service, "scan_for_signals_detailed", _fake_scan)
    out = scan_service.run_scan(
        skill_dir=tmp_path,
        universe_preset="sp1500",
        strategy_ids=["pullback"],
        scan_timeframe="weekly",
    )
    assert [row["ticker"] for row in out.signals] == ["MSFT"]
    assert out.diagnostics["strategy_id_filter_dropped"] == 1
    dropped = out.diagnostics["pruned_admits"]
    assert len(dropped) == 1
    assert dropped[0]["ticker"] == "AAPL"
    assert dropped[0]["strategy"] == "trend_breakout"
    assert dropped[0]["stage"] == "strategy_id_filter"
    assert dropped[0]["reason"] == "strategy_not_selected"
    assert dropped[0]["score"] == 40.0
    assert dropped[0]["sort_key"] == "signal_score"
