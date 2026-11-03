"""PIT universe + fundamentals: as-of membership, coverage gating, replay delta."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from research.pit_universe import (  # noqa: E402
    coverage_unlocks,
    field_coverage,
    join_pit_fundamentals,
    replay_membership_delta,
    universe_asof,
)
from research.providers.sharadar_pit import SharadarPITAdapter, sp500_members_asof  # noqa: E402
from research.providers.stub_pit import StubPITProvider  # noqa: E402
from research.registry import deferred_pit_feature_names, enabled_feature_names, load_feature_registry  # noqa: E402


def test_asof_2016_excludes_later_listings() -> None:
    stub = StubPITProvider()
    names = universe_asof("2016-06-01", stub)
    assert "AAPL" in names
    assert "MSFT" in names
    assert "SNAP" not in names
    assert "SNOW" not in names
    now = set(stub.wikipedia_now())
    assert "SNAP" in now
    assert "SNOW" in now


def test_sp500_asof_walks_events_backward() -> None:
    current = {"AAPL", "MSFT", "TSLA"}
    events = [
        ("2017-03-02", "added", "SNAP"),
        ("2020-12-21", "added", "TSLA"),
        ("2018-06-01", "removed", "GE"),
    ]
    asof = sp500_members_asof(current, events, "2016-06-01")
    assert "AAPL" in asof
    assert "MSFT" in asof
    assert "TSLA" not in asof  # added 2020
    assert "SNAP" not in asof
    assert "GE" in asof  # removed after asof → still a member on 2016-06-01


def test_sharadar_fail_closed_without_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SHARADAR_API_KEY", raising=False)
    adapter = SharadarPITAdapter(skill_dir=tmp_path)
    assert adapter.universe_asof("2016-06-01") == []
    dq = adapter.data_quality()
    assert dq["status"] == "error"
    assert "missing_sharadar_api_key" in str(dq.get("reason"))


def test_join_fundamentals_does_not_invent_zeros() -> None:
    stub = StubPITProvider()
    candidates = pd.DataFrame(
        {
            "ticker": ["AAPL", "SNAP"],
            "asof_date": ["2016-06-01", "2016-06-01"],
            "signal_score": [70.0, 65.0],
        }
    )
    joined = join_pit_fundamentals(candidates, stub)
    assert pd.notna(joined.loc[joined["ticker"] == "AAPL", "market_cap"]).all()
    snap = joined.loc[joined["ticker"] == "SNAP"]
    assert snap["market_cap"].isna().all()
    assert snap["roe"].isna().all()
    # Never filled with 0
    assert not (snap["market_cap"] == 0).any()


def test_coverage_gate_keeps_deferred_disabled_until_threshold(tmp_path: Path) -> None:
    reg = load_feature_registry(reload=True)
    deferred = deferred_pit_feature_names(reg)
    assert "market_cap" in deferred
    assert "float_shares" in deferred
    assert "institutional_ownership" in deferred
    assert "roe" in deferred
    enabled = enabled_feature_names(reg)
    for name in deferred:
        assert name not in enabled

    low = coverage_unlocks(
        {"market_cap": 0.2, "float_shares": 0.1, "roe": 0.0},
        skill_dir=tmp_path,
        registry=reg,
    )
    assert "market_cap" not in low["unlocked"]
    assert low["registry_enabled_deferred"] == []

    high = coverage_unlocks(
        {"market_cap": 0.95, "float_shares": 0.1, "roe": 0.0},
        skill_dir=tmp_path,
        registry=reg,
    )
    assert "market_cap" in high["unlocked"]
    assert "float_shares" not in high["unlocked"]
    # On-disk registry still disabled
    assert "market_cap" not in enabled_feature_names(load_feature_registry(reload=True))


def test_replay_reports_delta_without_replacing_canonical() -> None:
    stub = StubPITProvider()
    trades = pd.DataFrame(
        {
            "ticker": ["AAPL", "SNAP", "SNOW"],
            "net_return": [0.10, -0.05, 0.20],
        }
    )
    report = replay_membership_delta("2016-06-01", stub, trades=trades)
    assert report["canonical_runner_replaced"] is False
    assert report["n_only_wikipedia_now"] >= 2
    assert report["trade_count_wikipedia_now"] == 3
    assert report["trade_count_pit"] == 1
    assert "Do not silently replace" in report["banner"]


def test_field_coverage_ignores_missing() -> None:
    df = pd.DataFrame({"market_cap": [1.0, None, 2.0], "roe": [None, None, None]})
    cov = field_coverage(df, ["market_cap", "roe"])
    assert cov["market_cap"] == pytest.approx(2.0 / 3.0)
    assert cov["roe"] == pytest.approx(0.0)
