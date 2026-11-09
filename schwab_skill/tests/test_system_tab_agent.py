"""System calibration agent: Brier, reliability, and bounded confidence cutoffs."""

from __future__ import annotations

import json

import pytest

from config import get_tab_agent_system_enabled
from core.decision_packet import overwrite_packets
from core.system_tab_agent import SystemTabAgent, calibration_report, parse_thresholds
from core.tab_agent_digest import write_daily_digest


def _packet(ticker: str, confidence: float, label: str, ret: float, day: str) -> dict:
    return {
        "ticker": ticker,
        "created_at": f"{day}T15:00:00+00:00",
        "setup_type": "quick_check",
        "refs": {"source": "quick_check", "stated_confidence": confidence},
        "outcome": {"label": label, "realized_return_pct": ret, "horizon_days": 20},
    }


def test_system_agent_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.conftest import isolate_repo_dotenv

    monkeypatch.setenv("TAB_AGENT_SYSTEM_ENABLED", "true")
    monkeypatch.setattr(
        "config._load_env",
        lambda skill_dir=None: {"TAB_AGENT_SYSTEM_ENABLED": "true"},
    )
    isolate_repo_dotenv(monkeypatch, "TAB_AGENT_SYSTEM_ENABLED")
    assert get_tab_agent_system_enabled() is False


def test_thresholds_stay_inside_bounds() -> None:
    high, low = parse_thresholds("HIGH_CONFIDENCE_MIN=0.99\nLOW_CONFIDENCE_MAX=0.01")
    assert high == 0.90
    assert low == 0.15


def test_low_confidence_is_scored_on_the_missed_return(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAB_AGENT_SYSTEM_ENABLED", "true")
    overwrite_packets(
        tmp_path,
        [
            _packet("AAPL", 0.85, "win", 8.0, "2026-06-02"),
            _packet("MSFT", 0.20, "win", 20.0, "2026-06-03"),
            _packet("IBM", 0.15, "loss", -10.0, "2026-06-04"),
            _packet("MID", 0.55, "win", 50.0, "2026-06-05"),
            _packet("NEW", 0.10, "pending", 0.0, "2026-06-06"),
        ],
    )
    agent = SystemTabAgent()
    live = agent.registry(tmp_path).live_version("system", "confidence_wording")
    book = next(row for row in agent.score(tmp_path)["versions"] if row["version_id"] == live["id"])
    # High takes +8. Low miss of +20 scores -20. Low avoid of -10 scores +10.
    # The middle band and the pending packet are not samples.
    assert book["scored"] is True
    assert book["n"] == 3
    assert book["n_longs"] == 1
    assert book["win_rate"] == 1.0
    assert book["n_abstain"] == 1
    assert book["n_immature"] == 1
    assert book["avg_realized_return_pct"] == -0.6667
    assert book["n_skips"] == 2
    assert book["max_drawdown"] == 0.0
    assert book["high_confidence_min"] == 0.70
    assert book["low_confidence_max"] == 0.40
    assert book["scoring"] == "confidence_separation"


def test_skip_penalties_do_not_compound_into_the_drawdown(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Takes move the equity curve. Skips stay in cash, so a missed winner is not a position."""
    monkeypatch.setenv("TAB_AGENT_SYSTEM_ENABLED", "true")
    overwrite_packets(
        tmp_path,
        [
            _packet("T1", 0.90, "win", 5.0, "2026-06-01"),
            _packet("T2", 0.90, "win", 5.0, "2026-06-02"),
            _packet("T3", 0.90, "loss", -6.0, "2026-06-03"),
            _packet("T4", 0.90, "win", 4.0, "2026-06-04"),
            _packet("S1", 0.20, "win", 25.0, "2026-06-05"),
            _packet("S2", 0.20, "win", 25.0, "2026-06-06"),
            _packet("S3", 0.20, "loss", -20.0, "2026-06-07"),
            _packet("S4", 0.20, "loss", -20.0, "2026-06-08"),
        ],
    )
    agent = SystemTabAgent()
    live = agent.registry(tmp_path).live_version("system", "confidence_wording")
    book = next(row for row in agent.score(tmp_path)["versions"] if row["version_id"] == live["id"])
    assert book["n"] == 8
    assert book["n_longs"] == 4
    assert book["n_skips"] == 4
    assert book["win_rate"] == 0.75
    assert book["max_drawdown"] > -0.10
    assert book["max_drawdown"] < 0
    assert book["sortino"] is not None
    assert book["drawdown_basis"] == "cash_on_skip"


def test_all_skips_have_no_drawdown_and_no_longs(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAB_AGENT_SYSTEM_ENABLED", "true")
    overwrite_packets(tmp_path, [_packet(f"S{i}", 0.20, "win", 12.0, f"2026-06-{i + 1:02d}") for i in range(4)])
    agent = SystemTabAgent()
    live = agent.registry(tmp_path).live_version("system", "confidence_wording")
    book = next(row for row in agent.score(tmp_path)["versions"] if row["version_id"] == live["id"])
    assert book["n"] == 4
    assert book["n_longs"] == 0
    assert book["n_skips"] == 4
    assert book["win_rate"] is None
    assert book["max_drawdown"] == 0.0
    assert book["sortino"] == -1.0


def test_no_matured_packets_are_not_scored(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAB_AGENT_SYSTEM_ENABLED", "true")
    overwrite_packets(tmp_path, [_packet("AAPL", 0.10, "pending", 0.0, "2026-06-02")])
    agent = SystemTabAgent()
    live = agent.registry(tmp_path).live_version("system", "confidence_wording")
    book = next(row for row in agent.score(tmp_path)["versions"] if row["version_id"] == live["id"])
    assert book["scored"] is False
    assert book["not_scored_reason"] == "no_matured_packets"
    assert book["n"] == 0
    assert book["primary_score"] is None
    assert book["sortino"] is None
    payload = write_daily_digest(tmp_path)
    system = next(tab for tab in payload["tabs"] if tab["tab_id"] == "system")
    assert system["live"][0]["scored"] is False
    text = (tmp_path / ".tab_agent_digest" / f"{payload['day']}.md").read_text(encoding="utf-8")
    assert "not-scored" in text


def test_confidence_thresholds_use_the_skill_dir(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from config import get_tab_agent_confidence_high_min

    seen: list[object] = []
    real = get_tab_agent_confidence_high_min

    def _wrap(skill_dir: object = None) -> float:
        seen.append(skill_dir)
        return real(skill_dir)

    monkeypatch.setenv("TAB_AGENT_SYSTEM_ENABLED", "true")
    monkeypatch.setattr("config.get_tab_agent_confidence_high_min", _wrap)
    SystemTabAgent().score(tmp_path)
    assert tmp_path in seen


def test_overconfident_losses_are_flagged_in_the_digest(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAB_AGENT_SYSTEM_ENABLED", "false")
    packets = [_packet(f"T{i}", 0.85, "loss", -3.0, "2026-06-02") for i in range(6)]
    overwrite_packets(tmp_path, packets)
    report = calibration_report(packets)
    assert report["by_tab"]["quick_check"]["miscalibrated"] is True
    assert report["by_tab"]["quick_check"]["brier"] > 0.5
    payload = write_daily_digest(tmp_path)
    text = (tmp_path / ".tab_agent_digest" / f"{payload['day']}.md").read_text(encoding="utf-8")
    assert "Calibration:" in text
    assert "miscalibrated=True" in text
    system = next(tab for tab in payload["tabs"] if tab["tab_id"] == "system")
    assert system["calibration"]["by_tab"]["quick_check"]["miscalibrated"] is True


def test_propose_writes_a_review_request(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAB_AGENT_SYSTEM_ENABLED", "true")
    agent = SystemTabAgent()
    live = agent.registry(tmp_path).live_version("system", "confidence_wording")
    revised = live["text"].replace("HIGH_CONFIDENCE_MIN=0.70", "HIGH_CONFIDENCE_MIN=0.80")
    created = agent.propose(
        tmp_path,
        llm=lambda _s, _u: json.dumps({"text": revised, "rationale": "Tighten the bar. Sample may be small."}),
        prompt_key="confidence_wording",
    )
    version_id = created["created"][0]["id"]
    body = json.loads((tmp_path / ".tab_agent_reviews" / f"system-{version_id}.json").read_text(encoding="utf-8"))
    assert "HIGH_CONFIDENCE_MIN=0.80" in body["diff"]
