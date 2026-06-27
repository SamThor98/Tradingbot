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
OPERATIONS_JS = STATIC / "screens" / "operations.js"


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
def operations_js() -> str:
    return OPERATIONS_JS.read_text(encoding="utf-8")


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
)


@pytest.mark.parametrize("element_id", SCAN_TRANSPARENCY_DOM_IDS)
def test_index_html_scan_transparency_dom_ids(element_id: str, index_html: str) -> None:
    assert f'id="{element_id}"' in index_html or f"id='{element_id}'" in index_html


def test_app_js_wires_scan_transparency_modules(app_js: str) -> None:
    for token in (
        'from "./panels/scanDiagnostics.js"',
        'from "./modules/filterReasons.js"',
        'from "./modules/signalProvenance.js"',
        "renderDiagnostics as _renderDiagnosticsPanel",
        "formatFilterReasons",
        "renderSignalProvenanceChip",
        "renderTradeableVerdict",
        "isScanSignalStageable",
        "applyScanResponseSignals",
        "shortlist_signals",
    ):
        assert token in app_js, f"app.js missing: {token}"


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


def test_staging_guard_in_queue_dialog(app_js: str) -> None:
    assert "function openQueueScanDialog" in app_js
    assert "function confirmQueueScanDialog" in app_js
    assert "Filtered candidates cannot be staged" in app_js or "Cannot stage" in app_js
    assert "queueScanConfirmBtn" in app_js
