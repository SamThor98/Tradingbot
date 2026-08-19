"""Guard: local operator dashboard is HTTPS 8182, not uvicorn :8000."""

from __future__ import annotations

from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
ROOT = SKILL.parent


def test_agents_md_local_dashboard_is_8182_not_8000() -> None:
    text = (SKILL / "AGENTS.md").read_text(encoding="utf-8")
    assert "start_local_dashboard.py" in text
    assert "https://127.0.0.1:8182/" in text
    assert "uvicorn webapp.main:app --reload --port 8000" not in text


def test_orchestrator_rule_forbids_port_8000_for_ui() -> None:
    text = (ROOT / ".cursor" / "rules" / "tradingbot-orchestrator.mdc").read_text(encoding="utf-8")
    assert "8182" in text
    assert "Never start or verify UI on port 8000" in text


def test_frontend_rule_forbids_port_8000_for_ui() -> None:
    text = (ROOT / ".cursor" / "rules" / "webapp-frontend.mdc").read_text(encoding="utf-8")
    assert "https://127.0.0.1:8182/" in text
    assert "Never start or screenshot the local dashboard on port 8000" in text


def test_validation_runbook_uses_operator_dashboard_8182() -> None:
    text = (SKILL / "VALIDATION_RUNBOOK.md").read_text(encoding="utf-8")
    assert "start_local_dashboard.py" in text
    assert "https://127.0.0.1:8182" in text
    assert "uvicorn webapp.main:app --port 8000" not in text


def test_project_conventions_local_dashboard_is_8182() -> None:
    text = (ROOT / ".cursor" / "rules" / "project-conventions.mdc").read_text(encoding="utf-8")
    assert "https://127.0.0.1:8182" in text
    assert "Never verify UI on port 8000" in text
