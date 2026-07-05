"""Contract checks for W2b decision dashboard UI (System / Diagnostics)."""

from __future__ import annotations

from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
STATIC = SKILL_DIR / "webapp" / "static"
INDEX_HTML = STATIC / "index.html"
DECISION_DASHBOARD_JS = STATIC / "panels" / "decisionDashboard.js"
DECISION_CHARTS_JS = STATIC / "panels" / "decisionCharts.js"
APP_JS = STATIC / "app.js"


@pytest.fixture(scope="module")
def index_html() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def decision_dashboard_js() -> str:
    return DECISION_DASHBOARD_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def decision_charts_js() -> str:
    return DECISION_CHARTS_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_js() -> str:
    return APP_JS.read_text(encoding="utf-8")


DECISION_DOM_IDS = (
    "decisionDashboardCard",
    "decisionDashboardStateStrip",
    "decisionDashboardSummaryStrip",
    "decisionSignalEdgeBoard",
    "decisionSignalEdgeSummary",
    "decisionGateTiles",
    "decisionEraPfChart",
    "decisionReliabilityState",
    "decisionPromotionState",
)


@pytest.mark.parametrize("element_id", DECISION_DOM_IDS)
def test_index_html_decision_dashboard_dom_ids(element_id: str, index_html: str) -> None:
    assert f'id="{element_id}"' in index_html or f"id='{element_id}'" in index_html


def test_decision_dashboard_card_data_state(index_html: str) -> None:
    assert 'id="decisionDashboardCard"' in index_html
    assert 'data-state="loading"' in index_html.split('id="decisionDashboardCard"')[1][:220]


def test_decision_dashboard_wires_panel_state(decision_dashboard_js: str) -> None:
    for token in (
        'from "../modules/operationsPanelState.js"',
        "syncDecisionDashboardState",
        "syncDecisionSignalEdgeState",
        "_syncDecisionAsyncState",
        "decisionSignalEdgeSummary",
        "summarizeDecisionGates",
        "renderDecisionGateTiles",
        "renderDecisionPfChart",
        "renderDecisionSummaryStrip",
    ):
        assert token in decision_dashboard_js, f"decisionDashboard.js missing: {token}"


def test_decision_charts_exports(decision_charts_js: str) -> None:
    for token in (
        "export function renderDecisionGateTiles",
        "export function renderDecisionPfChart",
        "export function renderDecisionSummaryStrip",
        "export function summarizeDecisionGates",
        "buildGateTileStates",
        "decision-gate-tile",
        "decision-pf-chart",
    ):
        assert token in decision_charts_js, f"decisionCharts.js missing: {token}"


def test_app_js_refreshes_decision_dashboard(app_js: str) -> None:
    for token in (
        'from "./panels/decisionDashboard.js"',
        "refreshDecisionDashboard",
        "renderDecisionDashboardLoading",
        "renderDecisionDashboardUnavailable",
        "renderDecisionDashboard",
    ):
        assert token in app_js, f"app.js missing: {token}"
