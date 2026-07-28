"""PEAD-primary shadow dual-admit (Stage A) — non-executable beside Stage-2."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from signal_scanner import (  # noqa: E402
    _partition_stage_a_by_entry_family,
    _pead_primary_is_executable,
)
from stage_analysis import (  # noqa: E402
    evaluate_pead_primary_entry,
    tag_entry_family,
)


def _ohlcv(*, price: float = 50.0, avg_vol: float = 500_000.0, n: int = 80) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    close = pd.Series([price] * n, index=idx, dtype=float)
    return pd.DataFrame(
        {
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": [avg_vol] * n,
            "avg_vol_50": [avg_vol] * n,
        },
        index=idx,
    )


def test_tag_entry_family_stage2_only() -> None:
    assert tag_entry_family(stage2_ok=True, pead_ok=False) == "stage2"


def test_tag_entry_family_pead_only() -> None:
    assert tag_entry_family(stage2_ok=False, pead_ok=True) == "pead_primary"


def test_tag_entry_family_overlap_both() -> None:
    assert tag_entry_family(stage2_ok=True, pead_ok=True) == "both"


def test_tag_entry_family_neither() -> None:
    assert tag_entry_family(stage2_ok=False, pead_ok=False) is None


def test_pead_primary_liquidity_floor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PEAD_LOOKBACK_DAYS", "10")
    df = _ohlcv(price=4.0, avg_vol=500_000.0)
    out = evaluate_pead_primary_entry("AAA", df, skill_dir=tmp_path)
    assert out["admitted"] is False
    assert out["liquidity_ok"] is False
    assert out["fail_reason"] == "liquidity_floor"


def test_pead_primary_admit_on_beat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PEAD_LOOKBACK_DAYS", "10")
    import earnings_signal as es

    monkeypatch.setattr(
        es,
        "check_recent_earnings",
        lambda *_a, **_k: {"beat": True, "surprise_pct": 0.08},
    )
    df = _ohlcv(price=25.0, avg_vol=400_000.0)
    out = evaluate_pead_primary_entry("BBB", df, skill_dir=tmp_path)
    assert out["admitted"] is True
    assert out["liquidity_ok"] is True
    assert out["beat_ok"] is True


def test_pead_primary_rejects_no_beat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import earnings_signal as es

    monkeypatch.setattr(
        es,
        "check_recent_earnings",
        lambda *_a, **_k: {"beat": False, "surprise_pct": -0.02},
    )
    df = _ohlcv()
    out = evaluate_pead_primary_entry("CCC", df, skill_dir=tmp_path)
    assert out["admitted"] is False
    assert out["fail_reason"] == "pead_beat_fail"


def test_config_default_off_and_live_coerces(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from config import (
        clear_env_cache,
        get_strategy_pead_primary_effective_mode,
        get_strategy_pead_primary_mode,
    )

    clear_env_cache()
    monkeypatch.delenv("STRATEGY_PEAD_PRIMARY_MODE", raising=False)
    monkeypatch.delenv("STRATEGY_PEAD_PRIMARY_ALLOW_LIVE", raising=False)
    assert get_strategy_pead_primary_mode(tmp_path) == "off"
    assert get_strategy_pead_primary_effective_mode(tmp_path) == "off"

    monkeypatch.setenv("STRATEGY_PEAD_PRIMARY_MODE", "live")
    clear_env_cache()
    assert get_strategy_pead_primary_mode(tmp_path) == "live"
    assert get_strategy_pead_primary_effective_mode(tmp_path) == "shadow"

    monkeypatch.setenv("STRATEGY_PEAD_PRIMARY_ALLOW_LIVE", "true")
    clear_env_cache()
    assert get_strategy_pead_primary_effective_mode(tmp_path) == "live"


def test_partition_stage2_only_executable() -> None:
    cands = [
        {"ticker": "S1", "entry_family": "stage2", "stage_a_score": 90.0},
        {"ticker": "S2", "entry_family": "stage2", "stage_a_score": 70.0},
    ]
    part = _partition_stage_a_by_entry_family(cands, top_n=5, pead_shadow_max=10)
    assert len(part["executable"]) == 2
    assert part["pead_only"] == []
    assert part["pead_primary_admitted"] == 0
    assert part["overlap_with_stage2"] == 0
    assert all(_pead_primary_is_executable(c["entry_family"]) for c in part["executable"])


def test_partition_pead_only_shadow_non_executable() -> None:
    cands = [
        {"ticker": "S1", "entry_family": "stage2", "stage_a_score": 80.0, "edge_score": 40.0},
        {
            "ticker": "P1",
            "entry_family": "pead_primary",
            "stage_a_score": 95.0,
            "edge_score": 90.0,
            "pead_beat": True,
            "pead_surprise_pct": 0.12,
        },
        {
            "ticker": "P2",
            "entry_family": "pead_primary",
            "stage_a_score": 60.0,
            "edge_score": 50.0,
            "pead_beat": True,
            "pead_surprise_pct": 0.05,
        },
    ]
    part = _partition_stage_a_by_entry_family(cands, top_n=2, pead_shadow_max=1)
    exec_tickers = {c["ticker"] for c in part["executable"]}
    assert exec_tickers == {"S1"}
    assert not any(c["ticker"] == "P1" for c in part["executable"])
    assert part["pead_primary_admitted"] == 2
    assert part["overlap_with_stage2"] == 0
    # Soft cap keeps only top PEAD-only shadow name by edge_score.
    assert len(part["pead_primary_shadow_names"]) == 1
    assert part["pead_primary_shadow_names"][0]["ticker"] == "P1"
    assert part["pead_primary_shadow_names"][0]["executable"] is False
    assert part["pead_primary_shadow_names"][0]["capacity_top_n"] is True
    assert part["pead_primary_shadow_truncated"] == 1
    # Combined top-2 by stage_a_score: P1 (95) + S1 (80) → one pead name in top_n.
    assert part["pead_primary_would_rank_top_n"] == 1
    # Capacity top-5 arm still counts both PEAD-only admits (soft list cap is separate).
    assert part["pead_primary_would_rank_capacity_top_n"] == 2
    assert part["pead_primary_capacity_rank_arm"] == "top5_by_edge_score"


def test_partition_overlap_both_stays_executable() -> None:
    cands = [
        {
            "ticker": "BOTH",
            "entry_family": "both",
            "stage_a_score": 88.0,
            "edge_score": 70.0,
            "pead_beat": True,
            "pead_surprise_pct": 0.1,
        },
        {"ticker": "P1", "entry_family": "pead_primary", "stage_a_score": 70.0, "edge_score": 80.0},
    ]
    part = _partition_stage_a_by_entry_family(cands, top_n=5, pead_shadow_max=10)
    assert part["overlap_with_stage2"] == 1
    assert part["pead_primary_admitted"] == 2
    assert {c["ticker"] for c in part["executable"]} == {"BOTH"}
    assert _pead_primary_is_executable("both") is True
    assert _pead_primary_is_executable("pead_primary") is False


def test_partition_cap_truncation_and_sort_tiebreak() -> None:
    cands = [
        {"ticker": "ZB", "entry_family": "pead_primary", "stage_a_score": 50.0, "edge_score": 50.0},
        {"ticker": "ZA", "entry_family": "pead_primary", "stage_a_score": 50.0, "edge_score": 50.0},
        {"ticker": "S1", "entry_family": "stage2", "stage_a_score": 40.0, "edge_score": 99.0},
    ]
    part = _partition_stage_a_by_entry_family(cands, top_n=5, pead_shadow_max=1)
    assert part["pead_primary_shadow_truncated"] == 1
    # Same edge_score → ticker asc: ZA before ZB. Executable remains stage_a ordered.
    assert part["pead_primary_shadow_names"][0]["ticker"] == "ZA"
    assert part["pead_primary_shadow_sort_key"] == "edge_score_desc,ticker_asc"
    assert [c["ticker"] for c in part["executable"]] == ["S1"]

    part0 = _partition_stage_a_by_entry_family(cands, top_n=5, pead_shadow_max=0)
    assert part0["pead_primary_shadow_names"] == []
    assert part0["pead_primary_shadow_truncated"] == 2


def test_partition_capacity_top5_by_edge_score_not_stage_a() -> None:
    """Capacity top-5 prefers edge_score; executable shortlist stays stage_a_score."""
    cands = [
        {"ticker": "S1", "entry_family": "stage2", "stage_a_score": 99.0, "edge_score": 10.0},
        {"ticker": "P_LO", "entry_family": "pead_primary", "stage_a_score": 95.0, "edge_score": 20.0},
        {"ticker": "P_HI", "entry_family": "pead_primary", "stage_a_score": 40.0, "edge_score": 90.0},
        {"ticker": "P_MID", "entry_family": "pead_primary", "stage_a_score": 70.0, "edge_score": 60.0},
    ]
    part = _partition_stage_a_by_entry_family(
        cands, top_n=5, pead_shadow_max=10, pead_capacity_top_n=2
    )
    assert [c["ticker"] for c in part["executable"]] == ["S1"]
    names = part["pead_primary_shadow_names"]
    assert [n["ticker"] for n in names] == ["P_HI", "P_MID", "P_LO"]
    assert names[0]["capacity_top_n"] is True
    assert names[1]["capacity_top_n"] is True
    assert names[2]["capacity_top_n"] is False
    assert part["pead_primary_would_rank_capacity_top_n"] == 2
    assert part["pead_primary_capacity_rank_arm"] == "top2_by_edge_score"
    assert part["pead_primary_shadow_sort_key"] == "edge_score_desc,ticker_asc"


def test_config_shadow_rank_top_n_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from config import clear_env_cache, get_pead_primary_shadow_rank_top_n

    clear_env_cache()
    monkeypatch.delenv("PEAD_PRIMARY_SHADOW_RANK_TOP_N", raising=False)
    assert get_pead_primary_shadow_rank_top_n(tmp_path) == 5
    monkeypatch.setenv("PEAD_PRIMARY_SHADOW_RANK_TOP_N", "8")
    clear_env_cache()
    assert get_pead_primary_shadow_rank_top_n(tmp_path) == 8


def test_pead_primary_lookback_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from config import clear_env_cache, get_pead_primary_lookback_days

    clear_env_cache()
    monkeypatch.setenv("PEAD_LOOKBACK_DAYS", "10")
    monkeypatch.delenv("PEAD_PRIMARY_LOOKBACK_DAYS", raising=False)
    assert get_pead_primary_lookback_days(tmp_path) == 10
    monkeypatch.setenv("PEAD_PRIMARY_LOOKBACK_DAYS", "7")
    clear_env_cache()
    assert get_pead_primary_lookback_days(tmp_path) == 7


def test_parity_invariant_and_saved_rules() -> None:
    from core.pead_primary_shadow_compare import (
        build_pead_primary_shadow_compare_report,
        check_shadow_non_execution_invariant,
    )

    diag = {
        "strategy_pead_primary_mode": "shadow",
        "strategy_pead_primary_effective_mode": "shadow",
        "pead_primary_evaluated": 100,
        "pead_primary_admitted": 3,
        "overlap_with_stage2": 1,
        "pead_primary_would_rank_top_n": 1,
        "pead_primary_would_rank_capacity_top_n": 2,
        "pead_primary_capacity_rank_arm": "top5_by_edge_score",
        "pead_primary_capacity_rank_top_n": 5,
        "pead_primary_shadow_truncated": 0,
        "pead_primary_shadow_sort_key": "edge_score_desc,ticker_asc",
        "pead_primary_shadow_names": [
            {
                "ticker": "P1",
                "entry_family": "pead_primary",
                "edge_score": 90.0,
                "pead_beat": True,
                "pead_surprise_pct": 0.05,
                "capacity_top_n": True,
                "executable": False,
            },
            {
                "ticker": "P2",
                "entry_family": "pead_primary",
                "edge_score": 80.0,
                "pead_beat": True,
                "pead_surprise_pct": 0.04,
                "capacity_top_n": True,
                "executable": False,
            },
            {
                "ticker": "P3",
                "entry_family": "pead_primary",
                "edge_score": 40.0,
                "pead_beat": True,
                "pead_surprise_pct": 0.03,
                "capacity_top_n": False,
                "executable": False,
            },
        ],
        "data_quality": "ok",
    }
    signals = [
        {"ticker": "S1", "entry_family": "stage2"},
        {"ticker": "B1", "entry_family": "both"},
    ]
    inv = check_shadow_non_execution_invariant(signals, diag)
    assert inv["ok"] is True
    report = build_pead_primary_shadow_compare_report(diag, signals=signals)
    assert report["verdict"] == "pass"
    canary = report["canary_sleeve"]
    assert canary["executable"] is False
    assert canary["enablement_ready"] is False
    assert canary["rth_dq_ok"] is True
    assert canary["tickers"] == ["P1", "P2"]
    assert canary["n_names"] == 2

    leaked = [{"ticker": "P1", "entry_family": "pead_primary"}]
    bad = build_pead_primary_shadow_compare_report(diag, signals=leaked)
    assert bad["verdict"] == "fail"


def test_plugin_shadow_evidence_includes_pead(tmp_path: Path) -> None:
    from core.plugin_shadow_evidence import (
        append_plugin_shadow_record,
        build_plugin_shadow_record,
        load_plugin_shadow_records,
        summarize_plugin_shadow_records,
    )

    (tmp_path / "validation_artifacts").mkdir()
    diag = {
        "regime_v2_mode": "off",
        "correlation_guard_mode": "off",
        "strategy_pead_primary_mode": "shadow",
        "strategy_pead_primary_effective_mode": "shadow",
        "pead_primary_evaluated": 50,
        "pead_primary_admitted": 4,
        "overlap_with_stage2": 1,
        "pead_primary_would_rank_top_n": 2,
        "pead_primary_shadow_truncated": 1,
        "pead_primary_shadow_names": [{"ticker": "P1"}],
        "data_quality": "ok",
    }
    rec = build_plugin_shadow_record(diag, scan_at="2026-07-21T00:00:00Z", signals_found=3)
    assert rec is not None
    assert rec["pead_primary"]["admitted"] == 4
    assert rec["pead_primary"]["executable_still_stage2_only"] is True
    assert rec["pead_primary"]["canary_sleeve"]["executable"] is False
    assert "P1" in (rec["pead_primary"]["canary_sleeve"].get("tickers") or [])
    append_plugin_shadow_record(tmp_path, rec)
    summary = summarize_plugin_shadow_records(load_plugin_shadow_records(tmp_path))
    assert summary["pead_primary_shadow_n"] == 1
    assert summary["pead_primary_dq_ok_n"] == 1
