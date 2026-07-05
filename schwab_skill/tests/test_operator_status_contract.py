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
