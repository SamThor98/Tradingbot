"""Contract checks for Operations scan transparency UI (P1 dashboard).

Lightweight source-level assertions — no JS test runner required.
"""

from __future__ import annotations

from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
STATIC = SKILL_DIR / "webapp" / "static"
INDEX_HTML = STATIC / "index.html"
APP_JS = STATIC / "app.js"
FILTER_REASONS_JS = STATIC / "modules" / "filterReasons.js"
SIGNAL_PROVENANCE_JS = STATIC / "modules" / "signalProvenance.js"
SCAN_DIAG_JS = STATIC / "panels" / "scanDiagnostics.js"
SCAN_TABLE_JS = STATIC / "panels" / "scanTable.js"
APPROVE_DIALOG_JS = STATIC / "panels" / "approveDialog.js"
OPERATIONS_JS = STATIC / "screens" / "operations.js"
PENDING_BOARD_JS = STATIC / "panels" / "pendingBoard.js"


@pytest.fixture(scope="module")
def index_html() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_js() -> str:
    return APP_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def filter_reasons_js() -> str:
    return FILTER_REASONS_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def signal_provenance_js() -> str:
    return SIGNAL_PROVENANCE_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def scan_diag_js() -> str:
    return SCAN_DIAG_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def scan_table_js() -> str:
    return SCAN_TABLE_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def approve_dialog_js() -> str:
    return APPROVE_DIALOG_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def operations_js() -> str:
    return OPERATIONS_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def pending_board_js() -> str:
    return PENDING_BOARD_JS.read_text(encoding="utf-8")


SCAN_TRANSPARENCY_DOM_IDS = (
    "scanIntegrityBanner",
    "scanGateModesChip",
    "scanDeltaStrip",
    "scanDiagnosticsPanel",
    "scanBlockers",
    "scanFunnel",
    "scanDiagnostics",
    "nearMissPanel",
    "nearMissTableBody",
    "scanDetailTrust",
    "scanQualifiedMeta",
    "queueScanDialog",
    "queueScanChecklist",
    "queueScanConfirmBtn",
    "pendingStatusStrip",
    "scanLaneSummary",
    "scanDetailLaneSummary",
    "pendingLaneSummary",
    "workflowProgress",
    "workflowStepScan",
    "workflowStepEvaluate",
    "workflowStepApprove",
)


@pytest.mark.parametrize("element_id", SCAN_TRANSPARENCY_DOM_IDS)
def test_index_html_scan_transparency_dom_ids(element_id: str, index_html: str) -> None:
    assert f'id="{element_id}"' in index_html or f"id='{element_id}'" in index_html


def test_app_js_wires_scan_transparency_modules(app_js: str) -> None:
    for token in (
        'from "./panels/scanDiagnostics.js"',
        'from "./panels/scanTable.js"',
        'from "./modules/scanSignals.js"',
        'from "./modules/filterReasons.js"',
        'from "./modules/signalProvenance.js"',
        'from "./modules/operationsStatus.js"',
        'from "./modules/operationsPanelState.js"',
        "syncScanSectionState",
        "syncScanDetailPanelState",
        'from "./modules/kanbanLaneSummaries.js"',
        "updateKanbanLaneSummaries",
        'from "./modules/workflowKanban.js"',
        "updateWorkflowKanban",
        'from "./modules/signalTrustRow.js"',
        "renderSignalTrustRow",
        'from "./panels/decisionDashboard.js"',
        "renderDiagnostics as _renderDiagnosticsPanel",
        "configureScanTable",
        "formatFilterReasons",
        "renderSignalProvenanceChip",
        "isScanSignalStageable",
        "applyScanResponseSignals",
        "shortlist_signals",
    ):
        assert token in app_js, f"app.js missing: {token}"


def test_scan_table_panel_owns_row_rendering(scan_table_js: str, app_js: str) -> None:
    """Row/sort rendering lives in panels/scanTable.js after the extraction
    (docs/FRONTEND_DESIGN_SYSTEM.md "Next Planned Splits"); guard against the
    helpers drifting back inline into app.js."""
    for token in (
        "export function renderScanRows",
        "export function bindScanSortHandlers",
        "export function configureScanTable",
        "export function sortScanSignalsForRender",
        "export function setRankExplainMode",
        "export function applyRankExplainModeSelection",
        'from "../modules/floatTooltip.js"',
        "wireScanRankWhyTooltips",
        "renderTradeableVerdict",
        "renderSignalProvenanceChip",
        "isScanSignalStageable",
        "formatFilterReasons",
        "data-rank-tip",
        "scanFunnelFilterBanner",
        "nearMissTableBody",
    ):
        assert token in scan_table_js, f"scanTable.js missing: {token}"
    for forbidden in (
        "function renderScanRows(",
        "function bindScanSortHandlers(",
        "function sortScanSignalsForRender(",
        "function normalizeScanSignal(",
    ):
        assert forbidden not in app_js, (
            f"{forbidden!r} reappeared inline in app.js — it should live in "
            "panels/scanTable.js / modules/scanSignals.js instead."
        )


def test_filter_reasons_exports(filter_reasons_js: str) -> None:
    for token in (
        "export function formatFilterReasons",
        "export function formatScanStatusBadge",
        "export function formatNearMissSummary",
        "export function formatGateModeLabel",
        "REASON_LABELS",
        "filtered_quality_gates",
    ):
        assert token in filter_reasons_js, f"filterReasons.js missing: {token}"


def test_signal_provenance_exports(signal_provenance_js: str) -> None:
    for token in (
        "export function isScanSignalStageable",
        "export function provenanceFromSignal",
        "export function renderSignalProvenanceChip",
        "export function renderTradeableVerdict",
        "prov-chip",
    ):
        assert token in signal_provenance_js, f"signalProvenance.js missing: {token}"


def test_scan_diagnostics_integrity_banner(scan_diag_js: str) -> None:
    for token in (
        "export function renderScanIntegrityBanner",
        "export function renderScanGateModesToolbar",
        "export function buildScanIntegrityLine",
        "formatGateModeLabel",
        "data_quality",
    ):
        assert token in scan_diag_js, f"scanDiagnostics.js missing: {token}"


def test_operations_screen_staging_guard(operations_js: str) -> None:
    assert 'from "../modules/signalProvenance.js"' in operations_js
    assert "isScanSignalStageable" in operations_js
    assert "scanDetailStageBtn" in operations_js


def test_approve_dialog_panel_guardrails(approve_dialog_js: str, app_js: str) -> None:
    """The approve dialog (a live-order safety surface) lives in
    panels/approveDialog.js after the extraction. Its trade-safety guardrails
    must stay intact: typed-ticker confirmation, risk acknowledgement,
    server-side preflight, and the filtered-signal block."""
    for token in (
        "export async function openApproveDialog",
        "export function syncApproveDialogGuardrails",
        "export async function approveTradeById",
        "export function configureApproveDialog",
        "/preflight",
        "confirm_live=true",
        "typed_ticker",
        "otp_code",
        "approveRiskAck",
        "approveTickerInput",
        "isScanSignalStageable",
        "Ticker mismatch",
        "Risk acknowledgement required",
    ):
        assert token in approve_dialog_js, f"approveDialog.js missing: {token}"
    for forbidden in (
        "function openApproveDialog(",
        "function syncApproveDialogGuardrails(",
        "function approveTradeById(",
        "function formatPreflightChecklistHtml(",
    ):
        assert forbidden not in app_js, (
            f"{forbidden!r} reappeared inline in app.js — it should live in "
            "panels/approveDialog.js instead."
        )


def test_staging_guard_in_queue_dialog(app_js: str) -> None:
    assert "function openQueueScanDialog" in app_js
    assert "function confirmQueueScanDialog" in app_js
    assert "Filtered candidates cannot be staged" in app_js or "Cannot stage" in app_js
    assert "queueScanConfirmBtn" in app_js


def test_pending_board_provenance_and_status(pending_board_js: str) -> None:
    for token in (
        'from "../modules/operationsStatus.js"',
        'from "../modules/kanbanLaneSummaries.js"',
        'from "../modules/signalTrustRow.js"',
        "renderSignalTrustRow",
        "isScanSignalStageable",
        '"pendingStatusStrip"',
        "setOperationsStatusStrip",
        'board.dataset.state',
    ):
        assert token in pending_board_js, f"pendingBoard.js missing: {token}"


def test_scan_diagnostics_uses_operations_status(scan_diag_js: str) -> None:
    for token in (
        'from "../modules/operationsStatus.js"',
        'from "../modules/operationsPanelState.js"',
        "syncScanSectionState",
        "setOperationsStatusStrip",
    ):
        assert token in scan_diag_js, f"scanDiagnostics.js missing: {token}"


WORKFLOW_KANBAN_JS = STATIC / "modules" / "workflowKanban.js"


@pytest.fixture(scope="module")
def workflow_kanban_js() -> str:
    return WORKFLOW_KANBAN_JS.read_text(encoding="utf-8")


def test_index_html_workflow_primary_dom(index_html: str) -> None:
    assert 'id="workflowPrimary"' in index_html
    assert 'id="workflowPrimary" class="workflow-primary" data-state="empty"' in index_html
    assert 'id="workflowStatusStrip"' in index_html


def test_workflow_kanban_exports(workflow_kanban_js: str) -> None:
    for token in (
        "export function updateWorkflowKanban",
        "workflowStepScan",
        "workflowStepEvaluate",
        "workflowStepApprove",
        "scanSection",
        "scanDetailPanel",
        "pendingSection",
        "setPanelState",
        "workflowPrimary",
        "workflowFocus",
    ):
        assert token in workflow_kanban_js, f"workflowKanban.js missing: {token}"
