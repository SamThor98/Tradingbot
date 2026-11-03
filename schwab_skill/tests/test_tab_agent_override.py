"""Manual tab-agent override: verdict files and the CLI. No orders."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from core.tab_agent_override import board, record_verdict
from core.tab_agent_review import read_verdict
from core.today_tab_agent import TodayTabAgent


def _cli():
    path = Path(__file__).resolve().parents[1] / "scripts" / "tab_agent.py"
    spec = importlib.util.spec_from_file_location("tab_agent_cli", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _shadow(tmp_path: Path, key: str, suffix: str) -> tuple[str, str]:
    agent = TodayTabAgent()
    live = agent.registry(tmp_path).live_version("today", key)
    created = agent.propose(
        tmp_path,
        llm=lambda _system, _user: json.dumps(
            {"text": live["text"] + suffix, "rationale": "Operator sample may be small."}
        ),
        prompt_key=key,
    )
    return str(live["id"]), str(created["created"][0]["id"])


def test_approve_writes_sam_verdict_without_replacing_live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAB_AGENT_TODAY_ENABLED", "true")
    live_id, shadow_id = _shadow(tmp_path, "institutional_trend", " Stay in cash when the tape is weak.")
    cli = _cli()
    code = cli.main(["approve", "--tab", "today", "--version", shadow_id, "--skill-dir", str(tmp_path), "--notes", "looks fine"])
    assert code == 0
    verdict = read_verdict(tmp_path, "today", shadow_id)
    assert verdict is not None
    assert verdict["verdict"] == "approve"
    assert verdict["reviewer"] == "sam"
    assert verdict["notes"] == "looks fine"
    assert TodayTabAgent().registry(tmp_path).live_version("today", "institutional_trend")["id"] == live_id
    status = cli.main(["status", "--tab", "today", "--skill-dir", str(tmp_path)])
    assert status == 0


def test_reject_retires_shadow_and_promote_then_rollback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAB_AGENT_TODAY_ENABLED", "true")
    live_id, shadow_id = _shadow(tmp_path, "grok_research", " Never suggest a short.")
    cli = _cli()
    assert cli.main(["reject", "--tab", "today", "--version", shadow_id, "--skill-dir", str(tmp_path)]) == 0
    verdict = read_verdict(tmp_path, "today", shadow_id)
    assert verdict is not None and verdict["verdict"] == "reject" and verdict["reviewer"] == "sam"
    assert TodayTabAgent().registry(tmp_path).get("today", shadow_id)["status"] == "rejected"
    assert TodayTabAgent().registry(tmp_path).live_version("today", "grok_research")["id"] == live_id

    _live_id, other_id = _shadow(tmp_path, "grok_research", " Cash is a scored outcome.")
    assert cli.main(["promote", "--tab", "today", "--version", other_id, "--skill-dir", str(tmp_path)]) == 0
    assert TodayTabAgent().registry(tmp_path).live_version("today", "grok_research")["id"] == other_id
    assert read_verdict(tmp_path, "today", other_id)["reviewer"] == "sam"
    assert cli.main(["rollback", "--tab", "today", "--version", live_id, "--skill-dir", str(tmp_path)]) == 0
    assert TodayTabAgent().registry(tmp_path).live_version("today", "grok_research")["id"] == live_id


def test_ambiguous_candidate_asks_for_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    monkeypatch.setenv("TAB_AGENT_TODAY_ENABLED", "true")
    _shadow(tmp_path, "institutional_trend", " One shadow.")
    _shadow(tmp_path, "grok_research", " Another shadow.")
    code = _cli().main(["approve", "--tab", "today", "--skill-dir", str(tmp_path)])
    assert code == 2
    assert "Pass --version" in capsys.readouterr().err


def test_board_lists_pending_reviews_and_digest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAB_AGENT_TODAY_ENABLED", "true")
    monkeypatch.setenv("TAB_AGENT_DEBATE_ENABLED", "true")
    _live, shadow_id = _shadow(tmp_path, "institutional_trend", " Quality over count.")
    digest_dir = tmp_path / ".tab_agent_digest"
    digest_dir.mkdir()
    (digest_dir / "2026-11-03.json").write_text(
        json.dumps({"day": "2026-11-03", "tabs": [{"tab_id": "today", "live": [], "events": []}], "errors": []}),
        encoding="utf-8",
    )
    payload = board(tmp_path)
    assert payload["orders_touched"] is False
    assert payload["digest"]["day"] == "2026-11-03"
    today = next(tab for tab in payload["tabs"] if tab["tab_id"] == "today")
    assert any(row["id"] == shadow_id for row in today["pending_reviews"])
    record_verdict(tmp_path, "today", shadow_id, "approve")
    again = board(tmp_path)
    today = next(tab for tab in again["tabs"] if tab["tab_id"] == "today")
    assert all(row["id"] != shadow_id for row in today["pending_reviews"])


def test_api_approve_uses_sam_verdict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAB_AGENT_TODAY_ENABLED", "true")
    _live, shadow_id = _shadow(tmp_path, "institutional_trend", " Dashboard approve.")
    from fastapi.testclient import TestClient

    import webapp.main as web_main

    monkeypatch.setattr(web_main, "SKILL_DIR", tmp_path)
    client = TestClient(web_main.app)
    listed = client.get("/api/tab-agents")
    assert listed.status_code == 200
    body = listed.json()
    assert body["ok"] is True
    assert body["data"]["orders_touched"] is False
    approved = client.post("/api/tab-agents/today/approve", json={"version_id": shadow_id, "notes": "from the panel"})
    assert approved.status_code == 200
    assert approved.json()["ok"] is True
    verdict = read_verdict(tmp_path, "today", shadow_id)
    assert verdict is not None
    assert verdict["reviewer"] == "sam"
    assert verdict["notes"] == "from the panel"
