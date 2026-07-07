"""Contract tests for operator-facing status strip labels."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "webapp" / "static" / "modules"


def test_status_strip_uses_factual_pill_labels() -> None:
    core = (STATIC / "statusStripCore.js").read_text(encoding="utf-8")
    labels_block = core.split("STATUS_PILL_LABELS")[1].split("};")[0]
    assert "Unavailable" in labels_block
    assert "Degraded" in labels_block
    assert '"Error"' not in labels_block
    assert '"Failed"' not in labels_block


def test_async_state_operator_alert_markup() -> None:
    src = (STATIC / "asyncState.js").read_text(encoding="utf-8")
    assert "operator-alert" in src
    assert "operator-alert__headline" in src
    assert "Data unavailable" in src
    assert "No results yet" in src


def test_research_status_strip_delegates_to_core() -> None:
    src = (STATIC / "researchStatus.js").read_text(encoding="utf-8")
    assert 'from "./statusStripCore.js"' in src
    assert "research-status-pill" in src
    assert "paintStatusStrip" in src
    assert "setResearchPanelStatus" in src
    assert "paintResearchSurface" in src


def test_research_panel_snapshot_module() -> None:
    src = (STATIC / "researchPanelSnapshot.js").read_text(encoding="utf-8")
    assert "paintResearchPanelSnapshot" in src
    assert "research-panel-snapshot__kpis" in src
    assert "Confidence" in src
    assert "OUTPUT" in src
    assert "RESEARCH_SNAPSHOT_DEFAULTS" in src


def test_decision_dashboard_uses_status_pill_labels() -> None:
    src = (ROOT / "webapp" / "static" / "panels" / "decisionDashboard.js").read_text(encoding="utf-8")
    assert 'from "../modules/statusStripCore.js"' in src
    assert "paintStatusStrip" in src
    assert "charAt(0).toUpperCase()" not in src


def test_operations_panel_snapshot_module() -> None:
    src = (STATIC / "operationsPanelSnapshot.js").read_text(encoding="utf-8")
    assert "renderOperationsPanelSnapshot" in src
    assert "operations-panel-snapshot__kpis" in src
    assert "syncOperationsSectionState" in src


def test_build_operator_alert_exported() -> None:
    src = (STATIC / "asyncState.js").read_text(encoding="utf-8")
    assert "export function buildOperatorAlertHtml" in src


def test_pending_board_wires_snapshot() -> None:
    src = (ROOT / "webapp" / "static" / "panels" / "pendingBoard.js").read_text(encoding="utf-8")
    assert "operationsPanelSnapshot.js" in src
    assert "paintPendingSnapshot" in src


def test_scan_diagnostics_wires_snapshot() -> None:
    src = (ROOT / "webapp" / "static" / "panels" / "scanDiagnostics.js").read_text(encoding="utf-8")
    assert "operationsPanelSnapshot.js" in src
    assert "scanSnapshot" in src


def test_report_uses_operator_alert_helper() -> None:
    src = (ROOT / "webapp" / "static" / "panels" / "report.js").read_text(encoding="utf-8")
    assert "buildOperatorAlertHtml" in src
    assert '<div class="async-state async-state--error" role="alert">' not in src


def test_system_panel_contract_module() -> None:
    src = (STATIC / "systemPanelContract.js").read_text(encoding="utf-8")
    assert "paintSystemPanelSnapshot" in src
    assert "paintSystemPanelAlert" in src
    assert "syncSystemSectionState" in src


def test_workflow_kanban_wires_snapshot() -> None:
    src = (ROOT / "webapp" / "static" / "modules" / "workflowKanban.js").read_text(encoding="utf-8")
    assert "workflowSnapshot" in src
    assert "renderOperationsPanelSnapshot" in src


def test_shadow_scoreboard_uses_system_contract() -> None:
    src = (ROOT / "webapp" / "static" / "panels" / "shadowScoreboard.js").read_text(encoding="utf-8")
    assert "systemPanelContract.js" in src
    assert "shadowScoreboardSnapshot" in src
    assert "paintSystemPanelAlert" in src


def test_review_loop_uses_system_contract() -> None:
    src = (ROOT / "webapp" / "static" / "panels" / "reviewLoop.js").read_text(encoding="utf-8")
    assert "systemPanelContract" in src
    assert "reviewLoopSnapshot" in src
    assert "paintSystemPanelAlert" in src


def test_health_ribbon_wires_snapshot() -> None:
    src = (ROOT / "webapp" / "static" / "panels" / "healthRibbon.js").read_text(encoding="utf-8")
    assert "healthSnapshot" in src
    assert "paintSystemPanelSnapshot" in src


def test_status_details_snapshot_in_app() -> None:
    src = (ROOT / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
    assert "statusDetailsSnapshot" in src
    assert "paintSystemPanelSnapshot" in src


def test_command_palette_system_jumps() -> None:
    src = (STATIC / "commandPalette.js").read_text(encoding="utf-8")
    assert "healthRibbon" in src
    assert "shadowScoreboardSection" in src
    assert "reviewLoopSection" in src
    assert "workflowPrimary" in src


def test_portfolio_uses_operator_alert() -> None:
    src = (ROOT / "webapp" / "static" / "panels" / "portfolio.js").read_text(encoding="utf-8")
    assert "buildOperatorAlertHtml" in src
    assert "portfolioSnapshot" in src
    assert "setResearchPanelStatus" in src


def test_research_panels_wire_snapshot() -> None:
    panels = {
        "portfolio.js": "portfolioSnapshot",
        "cockpit.js": "cockpitSnapshot",
        "quickCheck.js": "quickCheckSnapshot",
        "sec.js": "secCompareSnapshot",
    }
    for name, snapshot_id in panels.items():
        src = (ROOT / "webapp" / "static" / "panels" / name).read_text(encoding="utf-8")
        assert snapshot_id in src
        assert "setResearchPanelStatus" in src


def test_index_has_system_snapshots() -> None:
    html = (ROOT / "webapp" / "static" / "index.html").read_text(encoding="utf-8")
    for sid in (
        "workflowSnapshot",
        "healthSnapshot",
        "statusDetailsSnapshot",
        "shadowScoreboardSnapshot",
        "reviewLoopSnapshot",
        "portfolioSnapshot",
        "cockpitSnapshot",
        "quickCheckSnapshot",
        "secCompareSnapshot",
    ):
        assert f'id="{sid}"' in html
