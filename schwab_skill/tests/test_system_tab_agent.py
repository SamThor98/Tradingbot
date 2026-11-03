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


def test_low_confidence_is_cash_and_high_confidence_uses_the_return(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAB_AGENT_SYSTEM_ENABLED", "true")
    overwrite_packets(
        tmp_path,
        [
            _packet("AAPL", 0.85, "win", 8.0, "2026-06-02"),
            _packet("MSFT", 0.20, "win", 20.0, "2026-06-03"),
        ],
    )
    agent = SystemTabAgent()
    live = agent.registry(tmp_path).live_version("system", "confidence_wording")
    book = next(row for row in agent.score(tmp_path)["versions"] if row["version_id"] == live["id"])
    assert book["n"] == 2
    assert book["avg_realized_return_pct"] == 4.0
    assert book["high_confidence_min"] == 0.70
    assert book["low_confidence_max"] == 0.40


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
