"""Shadow setup filter: flags, unchanged scan output, and resolver math."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

import signal_scanner
from core.shadow_setup_filter import (
    ShadowSetupThresholds,
    bar_features,
    build_signal_rows,
    evaluate_rules,
    forward_stats,
    load_rows,
    measure_local_overhead,
    record_final_candidates,
    render_report,
    resolve_outcomes,
    sector_breadth_map,
    sma50_probe,
    summarize_rows,
    thresholds_from_config,
)
from tests.conftest import isolate_repo_dotenv

SKILL_DIR = Path(__file__).resolve().parents[1]
_LIMITS = ShadowSetupThresholds()


def _frame_from_last(*, close: float, high: float, sma: float, atr: float) -> pd.DataFrame:
    index = pd.bdate_range("2023-01-02", periods=260)
    return pd.DataFrame(
        {
            "open": close,
            "high": high,
            "low": close - 1.0,
            "close": close,
            "volume": 1_000_000.0,
            "sma_50": sma,
            "atr_14": atr,
        },
        index=index,
    )


def test_rule_flags_and_ranks_do_not_mutate_signals() -> None:
    flags = evaluate_rules(
        signal_score=36.0,
        pct_below_52w_high=0.03,
        sector_breadth50=0.75,
        atr_pct=2.14,
        thresholds=_LIMITS,
    )
    assert flags["r1_keep"] and flags["r1b_keep"] and flags["r2_keep"] and flags["r3_keep"]
    assert flags["r4_keep"] and flags["r3_r4_keep"]

    dropped = evaluate_rules(
        signal_score=36.01,
        pct_below_52w_high=0.029,
        sector_breadth50=0.7501,
        atr_pct=2.139,
        thresholds=_LIMITS,
    )
    assert not any(dropped[key] for key in ("r1_keep", "r1b_keep", "r2_keep", "r3_keep", "r4_keep", "r3_r4_keep"))
    assert all(
        dropped[key] for key in ("r1_evaluable", "r1b_evaluable", "r2_evaluable", "r3_evaluable", "r4_evaluable")
    )
    assert dropped["r3_r4_evaluable"]

    missing = evaluate_rules(
        signal_score=None,
        pct_below_52w_high=None,
        sector_breadth50=None,
        atr_pct=None,
        thresholds=_LIMITS,
    )
    assert missing["r4_evaluable"] is False and missing["r4_keep"] is False
    assert missing["r3_r4_evaluable"] is False and missing["r3_r4_keep"] is False

    # R3 can pass while R4 fails, so the combined flag stays false.
    split = evaluate_rules(
        signal_score=20,
        pct_below_52w_high=0.05,
        sector_breadth50=0.4,
        atr_pct=1.0,
        thresholds=_LIMITS,
    )
    assert split["r3_keep"] is True
    assert split["r4_keep"] is False
    assert split["r3_r4_keep"] is False

    signals = [
        {"ticker": "HIGH", "signal_score": 40, "price": 10, "sector_etf": "XLK", "sector_winning": True},
        {"ticker": "LOW", "signal_score": 10, "price": 11, "sector_etf": "XLK", "sector_winning": False},
        {"ticker": "MID", "signal_score": 10, "price": 12, "sector_etf": "XLK", "sector_winning": True},
    ]
    before = json.dumps(signals, sort_keys=True)
    rows = build_signal_rows(
        signals,
        scan_id="s",
        scan_date="2024-06-03",
        logged_at="2024-06-03T20:00:00+00:00",
        thresholds=_LIMITS,
        breadth={"XLK": {"sector_breadth50": 0.5, "sector_breadth_above": 1, "sector_breadth_n": 2}},
        bar_features_by_ticker={
            "HIGH": {"atr_pct": 3.0, "pct_below_52w_high": 0.02, "close": 10},
            "LOW": {"atr_pct": 2.14, "pct_below_52w_high": 0.04, "close": 11},
            "MID": {"atr_pct": 1.5, "pct_below_52w_high": 0.04, "close": 12},
        },
    )
    assert json.dumps(signals, sort_keys=True) == before
    by_ticker = {row["ticker"]: row for row in rows}
    assert [row["existing_rank"] for row in rows] == [1, 2, 3]
    assert by_ticker["LOW"]["low_score_first_rank"] == 1
    assert by_ticker["MID"]["low_score_first_rank"] == 2
    assert by_ticker["HIGH"]["low_score_first_rank"] == 3
    assert by_ticker["LOW"]["r1_keep"] is True
    assert by_ticker["LOW"]["r4_keep"] is True
    assert by_ticker["LOW"]["r3_r4_keep"] is True
    assert by_ticker["LOW"]["sector_winning"] is False
    assert by_ticker["HIGH"]["r1_keep"] is False
    assert by_ticker["HIGH"]["r2_keep"] is True
    assert by_ticker["MID"]["r4_keep"] is False
    assert by_ticker["MID"]["r3_keep"] is True
    assert by_ticker["MID"]["r3_r4_keep"] is False
    assert by_ticker["LOW"]["entry_price"] == 11


def test_bar_features_and_sector_breadth() -> None:
    frame = _frame_from_last(close=97.0, high=100.0, sma=90.0, atr=2.0758)
    features = bar_features(frame)
    assert features is not None
    assert features["pct_below_52w_high"] == pytest.approx(0.03)
    assert features["distance_above_sma50"] == pytest.approx((97.0 - 90.0) / 90.0, abs=1e-5)
    assert features["atr_pct"] == pytest.approx(2.14, abs=1e-3)
    assert sma50_probe("Aaa", frame) == {"ticker": "AAA", "above_sma50": True}
    flat = frame.copy()
    flat["close"] = 90.0
    assert sma50_probe("BBB", flat)["above_sma50"] is False

    breadth = sector_breadth_map(
        {
            "AAA": {"sector_etf": "XLK", "above_sma50": True},
            "BBB": {"sector_etf": "XLK", "above_sma50": False},
            "CCC": {"sector_etf": "XLK", "above_sma50": True},
            "DDD": {"sector_etf": None, "above_sma50": True},
            "EEE": {"sector_etf": "XLF", "above_sma50": None},
        }
    )
    assert breadth["XLK"]["sector_breadth_n"] == 3
    assert breadth["XLK"]["sector_breadth_above"] == 2
    assert breadth["XLK"]["sector_breadth50"] == pytest.approx(2 / 3)
    assert "XLF" not in breadth


def test_config_defaults(monkeypatch, tmp_path: Path) -> None:
    isolate_repo_dotenv(
        monkeypatch,
        "SHADOW_SETUP_FILTER_ENABLED",
        "SHADOW_SETUP_FILTER_MAX_SIGNAL_SCORE",
        "SHADOW_SETUP_FILTER_MIN_PCT_BELOW_52W",
        "SHADOW_SETUP_FILTER_MAX_SECTOR_BREADTH50",
        "SHADOW_SETUP_FILTER_MIN_ATR_PCT",
        "SHADOW_SETUP_FILTER_FORWARD_HORIZONS",
        "SHADOW_SETUP_FILTER_STOP_PCTS",
    )
    limits = thresholds_from_config(tmp_path)
    assert limits.max_signal_score == 36.0
    assert limits.min_pct_below_52w == 0.03
    assert limits.max_sector_breadth50 == 0.75
    assert limits.min_atr_pct == 2.14
    assert limits.horizons == (5, 10, 20)
    assert limits.stop_pcts == (0.05, 0.08, 0.12)
    monkeypatch.setenv("SHADOW_SETUP_FILTER_MIN_ATR_PCT", "3.5")
    assert thresholds_from_config(tmp_path).min_atr_pct == 3.5


def _price_path() -> pd.DataFrame:
    index = pd.bdate_range("2024-06-03", periods=21)
    closes = [100.0 + day for day in range(21)]
    highs = [101.0 + day for day in range(21)]
    lows = [100.0 - day for day in range(21)]
    return pd.DataFrame(
        {"open": closes, "high": highs, "low": lows, "close": closes, "volume": 1.0},
        index=index,
    )


def test_forward_stats_and_resolver_are_idempotent(monkeypatch, tmp_path: Path) -> None:
    stats = forward_stats(_price_path(), scan_date="2024-06-03", entry_price=100.0)
    assert stats["ret_5d"] == pytest.approx(0.05)
    assert stats["ret_10d"] == pytest.approx(0.10)
    assert stats["ret_20d"] == pytest.approx(0.20)
    assert stats["mfe"] == pytest.approx(0.21)
    assert stats["mae"] == pytest.approx(-0.20)
    assert stats["stop_hit_5"] is True and stats["stop_hit_day_5"] == 5
    assert stats["stop_hit_8"] is True and stats["stop_hit_day_8"] == 8
    assert stats["stop_hit_12"] is True and stats["stop_hit_day_12"] == 12
    assert stats["forward_bars"] == 20

    short = forward_stats(_price_path().iloc[:8], scan_date="2024-06-03", entry_price=100.0)
    assert short["forward_bars"] == 7
    assert short["ret_5d"] == pytest.approx(0.05)
    assert short["ret_10d"] is None and short["ret_20d"] is None
    assert short["stop_hit_5"] is True
    assert short["stop_hit_8"] is False and short["stop_hit_12"] is False
    assert short["mfe"] == pytest.approx(0.08)
    assert short["mae"] == pytest.approx(-0.07)

    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("network")

    monkeypatch.setattr("market_data.get_daily_history", _boom)
    signal = {
        "ticker": "AAA",
        "price": 100.0,
        "signal_score": 30.0,
        "sector_etf": "XLK",
        "sector_winning": True,
        "stop_pct": 0.08,
    }
    diagnostics: dict[str, Any] = {
        "scan_id": "scan-1",
        "_shadow_bar_features": {"AAA": {"atr_pct": 3.0, "pct_below_52w_high": 0.05, "close": 100.0}},
        "_shadow_breadth_samples": {
            "AAA": {"ticker": "AAA", "sector_etf": "XLK", "above_sma50": True},
            "BBB": {"ticker": "BBB", "sector_etf": "XLK", "above_sma50": False},
        },
    }
    copied = json.loads(json.dumps(signal))
    written = record_final_candidates(
        [signal],
        diagnostics,
        skill_dir=tmp_path,
        now=datetime(2024, 6, 3, 20, tzinfo=timezone.utc),
    )
    assert written == 1
    assert signal == copied
    assert diagnostics["shadow_setup_filter_r4_would_keep"] == 1
    assert diagnostics["shadow_setup_filter_r3_r4_would_keep"] == 1

    calls = {"n": 0}

    def _provider(_ticker: str) -> pd.DataFrame:
        calls["n"] += 1
        return _price_path()

    first = resolve_outcomes(
        tmp_path,
        history_provider=_provider,
        now=datetime(2024, 7, 1, tzinfo=timezone.utc),
    )
    second = resolve_outcomes(
        tmp_path,
        history_provider=_provider,
        now=datetime(2024, 7, 2, tzinfo=timezone.utc),
    )
    assert first["written"] == 1
    assert second["written"] == 0
    assert calls["n"] == 2
    outcomes = [row for row in load_rows(tmp_path) if row["record_type"] == "outcome"]
    assert len(outcomes) == 1
    assert outcomes[0]["ret_20d"] == pytest.approx(0.20)

    summary = summarize_rows(load_rows(tmp_path))
    text = render_report(summary)
    assert "R4:" in text and "R3+R4:" in text
    r4 = summary["rules"]["R4"]["horizons"]["20"]
    assert r4["kept"]["n"] == 1
    assert r4["kept"]["mean"] == pytest.approx(0.20)
    assert r4["kept"]["win_pct"] == 100.0
    assert r4["dropped"]["n"] == 0
    assert r4["all"]["n"] == 1


def test_report_kept_versus_dropped(tmp_path: Path) -> None:
    rows = []
    for ticker, score, ret in (("KEEP", 20.0, 0.10), ("DROP", 80.0, -0.10)):
        signal_row = build_signal_rows(
            [
                {
                    "ticker": ticker,
                    "signal_score": score,
                    "price": 100.0,
                    "sector_etf": "XLK",
                    "sector_winning": True,
                }
            ],
            scan_id="s",
            scan_date="2024-06-03",
            logged_at="2024-06-03T20:00:00+00:00",
            thresholds=_LIMITS,
            breadth={"XLK": {"sector_breadth50": 0.5, "sector_breadth_above": 1, "sector_breadth_n": 2}},
            bar_features_by_ticker={ticker: {"atr_pct": 3.0, "pct_below_52w_high": 0.05, "close": 100.0}},
        )[0]
        rows.append(signal_row)
        outcome = {
            "record_type": "outcome",
            "scan_id": "s",
            "scan_date": "2024-06-03",
            "ticker": ticker,
            "entry_price": 100.0,
            "forward_bars": 5,
            "ret_5d": ret,
            "ret_10d": None,
            "ret_20d": None,
        }
        rows.append(outcome)
    summary = summarize_rows(rows, horizons=(5,))
    r1 = summary["rules"]["R1"]
    assert r1["n_kept"] == 1 and r1["n_dropped"] == 1
    assert r1["horizons"]["5"]["kept"]["mean"] == pytest.approx(0.10)
    assert r1["horizons"]["5"]["dropped"]["mean"] == pytest.approx(-0.10)
    assert r1["horizons"]["5"]["all"]["mean"] == pytest.approx(0.0)
    assert r1["horizons"]["5"]["all"]["median"] == pytest.approx(0.0)
    assert r1["horizons"]["5"]["kept"]["win_pct"] == 100.0
    assert r1["horizons"]["5"]["dropped"]["win_pct"] == 0.0
    assert r1["horizons"]["5"]["all"]["win_pct"] == 50.0
    assert summary["rules"]["R4"]["n_kept"] == 2
    assert summary["rules"]["R3+R4"]["n_kept"] == 1


def test_cli_mentions_r4(tmp_path: Path) -> None:
    record_final_candidates(
        [
            {
                "ticker": "AAA",
                "price": 10.0,
                "signal_score": 10.0,
                "sector_etf": "XLK",
                "sector_winning": False,
            }
        ],
        {"scan_id": "cli", "_shadow_bar_features": {"AAA": {"atr_pct": 2.14, "pct_below_52w_high": 0.04}}},
        skill_dir=tmp_path,
        now=datetime(2024, 6, 3, tzinfo=timezone.utc),
    )
    completed = subprocess.run(
        [sys.executable, "scripts/shadow_setup_filter_report.py", "--skill-dir", str(tmp_path)],
        cwd=SKILL_DIR,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "R4: kept=1" in completed.stdout
    assert "R3+R4:" in completed.stdout


def test_default_provider_uses_cache_without_network(monkeypatch, tmp_path: Path) -> None:
    frame = _price_path()
    monkeypatch.setattr("backtest.resolve_price_cache_dir", lambda *_a, **_k: tmp_path)
    monkeypatch.setattr("backtest.read_price_cache_frame", lambda *_a, **_k: frame)
    monkeypatch.setattr("config.get_backtest_price_cache_dir", lambda *_a, **_k: str(tmp_path))

    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("network")

    monkeypatch.setattr("market_data.get_daily_history", _boom)
    record_final_candidates(
        [{"ticker": "AAA", "price": 100.0, "signal_score": 10.0, "sector_etf": "XLK", "sector_winning": True}],
        {
            "scan_id": "cache",
            "_shadow_bar_features": {"AAA": {"atr_pct": 3.0, "pct_below_52w_high": 0.05}},
            "_shadow_breadth_samples": {
                "AAA": {"ticker": "AAA", "sector_etf": "XLK", "above_sma50": True},
                "BBB": {"ticker": "BBB", "sector_etf": "XLK", "above_sma50": False},
            },
        },
        skill_dir=tmp_path,
        now=datetime(2024, 6, 3, 20, tzinfo=timezone.utc),
    )
    result = resolve_outcomes(tmp_path, allow_network=True, now=datetime(2024, 7, 1, tzinfo=timezone.utc))
    assert result["written"] == 1
    assert load_rows(tmp_path)[-1]["ret_5d"] == pytest.approx(0.05)


def test_local_overhead_stays_small() -> None:
    timing = measure_local_overhead()
    assert timing["sma50_probe_ms"] < 1000
    assert timing["record_ms"] < 250
    print(f"shadow_setup_overhead {json.dumps(timing, sort_keys=True)}")


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _public_diagnostics(diagnostics: dict[str, Any]) -> dict[str, Any]:
    skip_exact = {"scan_id"}
    cleaned: dict[str, Any] = {}
    for key, value in diagnostics.items():
        name = str(key)
        if name in skip_exact or name.endswith("_ms"):
            continue
        if name.startswith("shadow_setup_filter") or name.startswith("_shadow_"):
            continue
        cleaned[name] = value
    return cleaned


def test_scan_output_matches_with_filter_on_or_off(monkeypatch, tmp_path: Path) -> None:
    """Final signals are the same JSON with the logger on and off. No network."""

    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("network")

    monkeypatch.setattr("market_data.get_daily_history", _boom)
    monkeypatch.setitem(sys.modules, "notifier", type("N", (), {"send_alert": staticmethod(lambda *_a, **_k: None)}))
    monkeypatch.setitem(
        sys.modules,
        "schwab_auth",
        type("A", (), {"DualSchwabAuth": staticmethod(lambda *a, **k: object())}),
    )
    monkeypatch.setitem(
        sys.modules,
        "data_health",
        type(
            "H",
            (),
            {"assess_scan_session_data_health": staticmethod(lambda *_a, **_k: {"data_quality": "ok", "reasons": []})},
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "sector_strength",
        type(
            "S",
            (),
            {
                "is_market_regime_bullish": staticmethod(
                    lambda *_a, **_k: (True, {"spy_price": 500, "spy_sma_200": 490})
                ),
                "get_winning_sector_etfs": staticmethod(lambda *_a, **_k: {"XLK"}),
                "get_regime_v2_snapshot": staticmethod(lambda *_a, **_k: {"score": 80, "bucket": "neutral"}),
                "regime_v2_is_unavailable": staticmethod(
                    lambda snap: not isinstance(snap, dict) or snap.get("score") is None
                ),
                "get_unresolved_sector_symbols": staticmethod(lambda **_k: []),
                "get_ticker_sector_etf": staticmethod(lambda ticker, skill_dir=None: "XLK"),
            },
        ),
    )
    monkeypatch.setattr(signal_scanner, "_record_quality_snapshot", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "core.options_scoring.apply_options_scoring",
        lambda signals, diagnostics, skill_dir=None: signals,
    )
    monkeypatch.setattr(
        "core.company_name_lookup.attach_company_names",
        lambda rows, skill_dir=None: None,
    )

    specs = {
        "AAA": {
            "score": 30.0,
            "above": True,
            "pct_below": 0.10,
            "atr_pct": 3.0,
            "price": 100.0,
        },
        "BBB": {
            "score": 50.0,
            "above": False,
            "pct_below": 0.01,
            "atr_pct": 1.0,
            "price": 80.0,
        },
    }

    def _stage_a(ticker: str, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        spec = specs[ticker]
        return {
            "ok": True,
            "candidate": {
                "ticker": ticker,
                "stage_a_score": spec["score"],
                "entry_family": "stage2",
                "sector_etf": "XLK",
                "executable": True,
                "data_provider_primary": True,
                "price": spec["price"],
            },
            "_shadow_breadth_probe": {
                "ticker": ticker,
                "sector_etf": "XLK",
                "above_sma50": spec["above"],
            },
        }

    def _stage_b(candidate: dict[str, Any], *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        ticker = str(candidate.get("ticker"))
        spec = specs[ticker]
        return {
            "ok": True,
            "signal": {
                "ticker": ticker,
                "price": spec["price"],
                "signal_score": spec["score"],
                "sector_etf": "XLK",
                "sector_winning": True,
                "sma_50": 90.0,
                "stop_pct": 0.08,
                "entry_family": "stage2",
                "score_components": {"pct_from_52w_high": round(1.0 - spec["pct_below"], 4)},
                "mirofish_conviction": 0,
            },
            "diag": {},
            "shadow_setup_probe": {
                "close": spec["price"],
                "pct_below_52w_high": spec["pct_below"],
                "distance_above_sma50": (spec["price"] - 90.0) / 90.0,
                "atr_pct": spec["atr_pct"],
            },
        }

    monkeypatch.setattr(signal_scanner, "_scan_stage_a_one", _stage_a)
    monkeypatch.setattr(signal_scanner, "_scan_stage_b_enrich", _stage_b)

    quiet = {
        "EVENT_RISK_MODE": "off",
        "RANK_FILTER_V2_MODE": "off",
        "QUALITY_GATES_MODE": "off",
        "CONFLUENCE_GATE_MODE": "off",
        "CORRELATION_GUARD_MODE": "off",
        "SIGNAL_EDGE_SHADOW_MODE": "off",
        "NEUTRAL_RANK_MODE": "off",
        "EXTENSION_SORT_MODE": "off",
        "PEAD_EXTENSION_DROP_MODE": "off",
        "HISTORICAL_RANK_MODE": "off",
        "ANALOG_OVERLAY_MODE": "off",
        "CONFIDENCE_RANK_MODE": "off",
        "OPTIONS_SCORING_MODE": "off",
        "PROB_RANK_MODE": "off",
        "SELF_STUDY_MODE": "off",
        "PRED_MARKET_MODE": "off",
        "META_POLICY_MODE": "off",
        "UNCERTAINTY_MODE": "off",
        "DEBATE_GRADE_MODE": "off",
        "DEBATE_COMMITTEE_MODE": "off",
        "PTS_52W_CAP_MODE": "off",
        "EARLY_STOP_GATE_MODE": "off",
        "SIGNAL_TOP_N": "5",
        "REGIME_V2_MODE": "off",
    }

    def _run(enabled: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        run_dir = tmp_path / enabled
        run_dir.mkdir()
        return signal_scanner.scan_for_signals_detailed(
            skill_dir=run_dir,
            env_overrides={**quiet, "SHADOW_SETUP_FILTER_ENABLED": enabled},
            watchlist_override=["AAA", "BBB"],
        )

    on_signals, on_diag = _run("true")
    off_signals, off_diag = _run("false")
    assert _canon(on_signals) == _canon(off_signals)
    assert on_signals and {row["ticker"] for row in on_signals} == {"AAA", "BBB"}
    assert _canon(_public_diagnostics(on_diag)) == _canon(_public_diagnostics(off_diag))
    assert "_shadow_breadth_samples" not in on_diag
    assert "_shadow_bar_features" not in on_diag
    assert "shadow_setup_filter_enabled" not in off_diag
    assert on_diag["shadow_setup_filter_logged"] == 2
    logged = [row for row in load_rows(tmp_path / "true") if row["record_type"] == "signal"]
    by_ticker = {row["ticker"]: row for row in logged}
    assert by_ticker["AAA"]["r1_keep"] is True
    assert by_ticker["AAA"]["r1b_keep"] is True
    assert by_ticker["AAA"]["r2_keep"] is True
    assert by_ticker["AAA"]["sector_breadth50"] == pytest.approx(0.5)
    assert by_ticker["AAA"]["r3_keep"] is True
    assert by_ticker["AAA"]["r4_keep"] is True
    assert by_ticker["AAA"]["r3_r4_keep"] is True
    assert by_ticker["AAA"]["sector_winning"] is True
    assert by_ticker["BBB"]["r1_keep"] is False
    assert by_ticker["BBB"]["r4_keep"] is False
    assert by_ticker["BBB"]["r3_r4_keep"] is False
    assert by_ticker["BBB"]["entry_price"] == 80.0
    assert not (tmp_path / "false" / "data" / "shadow_setup_filter" / "observations.jsonl").exists()
