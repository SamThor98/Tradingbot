"""Static-asset checks for ``webapp/static/modules/router.js``.

In-process JS unit tests live in ``tests/js/`` (Node's built-in runner, see
``tests/test_js_unit.py``); the checks here are complementary source-level
contract checks against the file itself:

* The exported alias map matches the friendly-name -> DOM-id contract
  used by docs / marketing emails (``?section=backtest`` etc.). If
  someone renames a section without updating the alias map, the test
  flags it.
* ``app.js`` no longer carries its own copy of the helpers — that's
  the whole point of #7 in the website improvement plan; if the
  helpers leak back inline we want to know about it.
* The router module is referenced by ``app.js`` (catches accidental
  delete of the import block).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
ROUTER_JS = STATIC / "modules" / "router.js"
APP_JS = STATIC / "app.js"


@pytest.fixture(scope="module")
def router_source() -> str:
    return ROUTER_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_source() -> str:
    return APP_JS.read_text(encoding="utf-8")


def _parse_alias_map(source: str) -> dict[str, str]:
    """Pull the literal SECTION_ALIASES object out of router.js.

    Tiny regex-based extractor: the object body is a flat key/value
    map of plain string literals, so a full JS parser would be
    overkill. If a future edit introduces nested objects or computed
    keys, this helper will deliberately fail so the test gets updated
    alongside the schema.
    """
    match = re.search(
        r"export const SECTION_ALIASES = Object\.freeze\(\{(.*?)\}\)",
        source,
        re.DOTALL,
    )
    assert match, "SECTION_ALIASES literal block not found in router.js"
    body = match.group(1)
    # Support bare keys (backtest) and quoted hyphenated keys ("book-calendar").
    pairs = re.findall(r'(?:["\']([\w-]+)["\']|(\w+))\s*:\s*"([^"]+)"', body)
    assert pairs, "SECTION_ALIASES body parsed to zero entries"
    return {(a or b): c for a, b, c in pairs}


# Friendly-name -> DOM-id contract. These are the URL-safe shortcuts we
# hand out in onboarding emails / docs / Stripe success pages and must
# stay backwards compatible. Updating the dashboard sections also means
# updating this map AND this test.
EXPECTED_ALIASES = {
    "backtest": "backtestSection",
    "backtests": "backtestSection",
    "lab": "backtestSummaryLanding",
    "leaderboard": "backtestLeaderboard",
    "btmonitor": "backtestLiveMonitor",
    "backtest-monitor": "backtestLiveMonitor",
    "pending": "pendingSection",
    "queue": "pendingSection",
    "approvals": "pendingSection",
    "trades": "pendingSection",
    "scan": "scanSection",
    "candidates": "scanSection",
    "scanstudio": "scanStudioPanel",
    "scan-studio": "scanStudioPanel",
    "desk": "operatorAgentsSection",
    "operator-desk": "operatorAgentsSection",
    "grok": "grokReviewSection",
    "grok-review": "grokReviewSection",
    "todayagent": "todayTabAgent",
    "today-agent": "todayTabAgent",
    "debateagent": "debateTabAgent",
    "debate-agent": "debateTabAgent",
    "tabagents": "tabAgentsSection",
    "tab-agents": "tabAgentsSection",
    "peadcanary": "peadCanarySection",
    "canary": "peadCanarySection",
    "pead-canary": "peadCanarySection",
    "lessons": "settledLessonsSection",
    "settled-lessons": "settledLessonsSection",
    "workflow": "workflowPrimary",
    "connect": "onboardingSection",
    "onboarding": "onboardingSection",
    "setup": "onboardingSection",
    "settings": "settingsWorkspaceIntro",
    "diagnostics": "diagnosticsWorkspaceIntro",
    "intel": "positionIntelSection",
    "positionintel": "positionIntelSection",
    "health": "healthRibbon",
    "operations": "operationsWorkspaceIntro",
    "research": "researchWorkspaceIntro",
    "cockpit": "cockpitWorkspaceIntro",
    "sectors": "sectorsSection",
    "movers": "moversSection",
    "sec": "secCompareSection",
    "seccompare": "secCompareSection",
    "event-risk": "eventProbabilitySection",
    "eventrisk": "eventProbabilitySection",
    "event-probability": "eventProbabilitySection",
    "liquid": "liquidPositioningSection",
    "liquid-positioning": "liquidPositioningSection",
    "positioning": "liquidPositioningSection",
    "pricing": "pricingModelSection",
    "pricing-model": "pricingModelSection",
    "companyfacts": "pricingModelSection",
    "sharadar": "sharadarBundleSection",
    "sharadar-bundle": "sharadarBundleSection",
    "pit": "sharadarBundleSection",
    "sharadar-compare": "sharadarCompareSection",
    "bundle-compare": "sharadarCompareSection",
    "calibration": "calibrationSection",
    "shadow": "shadowScoreboardSection",
    "shadowscoreboard": "shadowScoreboardSection",
    "shadow-workbench": "shadowScoreboardSection",
    "workbench": "shadowScoreboardSection",
    "decide": "systemDecisionPanel",
    "promote": "systemDecisionPanel",
    "decision": "systemDecisionPanel",
    "bookwidth": "decisionBookWidth",
    "book-width": "decisionBookWidth",
    "ready": "systemSummaryLanding",
    "review": "reviewLoopSection",
    "reviewloop": "reviewLoopSection",
    "advanced": "statusDetailsPanel",
    "status": "statusDetailsPanel",
    "portfolio": "portfolioSection",
    "risk": "portfolioPanelRisk",
    "portfoliorisk": "portfolioPanelRisk",
    "book": "portfolioPanelBook",
    "book-calendar": "book-calendar",
    "book-tax": "book-tax",
    "book-journal": "book-journal",
}


def _parse_screen_sections(source: str) -> dict[str, list[str]]:
    match = re.search(r"const SCREEN_SECTIONS = Object\.freeze\(\{(.*?)\n\}\);", source, re.DOTALL)
    assert match, "SCREEN_SECTIONS literal block not found in app.js"
    out: dict[str, list[str]] = {}
    for screen, body in re.findall(r"(\w+):\s*\[(.*?)\]", match.group(1), re.DOTALL):
        out[screen] = re.findall(r'"([\w-]+)"', body)
    assert out, "SCREEN_SECTIONS body parsed to zero entries"
    return out


def test_router_alias_map_matches_contract(router_source: str) -> None:
    assert _parse_alias_map(router_source) == EXPECTED_ALIASES


def test_alias_targets_resolve_to_a_screen(app_source: str, router_source: str) -> None:
    """Every alias target must be reachable from a cold deep link.

    ``inferScreenFromHash`` reads ``SECTION_TO_SCREEN``, derived from
    ``SCREEN_SECTIONS``. A section missing there loads on the default
    Today screen, where the research/system CSS hides it — the deep link
    silently lands on a blank page. Sub-tab anchors (``book-calendar``)
    and the deprecated ``*WorkspaceIntro`` shims are exempt: they are
    nested inside a section that is itself mapped.
    """
    mapped = {section for ids in _parse_screen_sections(app_source).values() for section in ids}
    targets = set(_parse_alias_map(router_source).values())
    exempt = {t for t in targets if t.endswith("WorkspaceIntro") or t.startswith("book-")}
    missing = sorted(targets - mapped - exempt)
    assert not missing, f"alias targets absent from SCREEN_SECTIONS in app.js: {missing}"


def test_router_exposes_public_surface(router_source: str) -> None:
    """The four pieces app.js depends on must be exported."""
    for name in (
        "export function handleRouteHash",
        "export function applyQuerySectionDeepLink",
        "export function clearOAuthQueryParams",
        "export function installRouter",
        "export function resolveSectionAlias",
        "export const SECTION_ALIASES",
    ):
        assert name in router_source, f"router.js missing public export: {name!r}"


def test_app_js_uses_router_module(app_source: str) -> None:
    assert "./modules/router.js" in app_source, (
        "app.js no longer imports the router module; the routing helpers "
        "are supposed to live in modules/router.js after #7 of the "
        "website improvement plan."
    )
    # Boot sequence should invoke installRouter — either directly or via
    # a wrapper like safeInit("installRouter", installRouter). Accept both
    # so the contract is about behaviour, not literal call syntax.
    boot_invokes_router = "installRouter()" in app_source or 'safeInit("installRouter", installRouter' in app_source
    assert boot_invokes_router, "app.js boot sequence should call installRouter() from router.js"


def test_app_js_checks_runtime_contract(app_source: str) -> None:
    assert "/api/runtime-contract" in app_source, (
        "app.js should query /api/runtime-contract during boot so mode/transport "
        "mismatches are surfaced before users run scans."
    )
    assert "validateRuntimeContract(" in app_source


def test_app_js_sse_uses_builder_for_api_key_compat(app_source: str) -> None:
    assert "function buildSseUrl()" in app_source
    assert "new EventSource(buildSseUrl())" in app_source


def test_app_js_does_not_redefine_router_helpers(app_source: str) -> None:
    """Guardrail against the helpers drifting back into app.js."""
    forbidden = [
        # The whole point of the extraction: these definitions must not
        # come back inline in app.js.
        "function handleRouteHash(",
        "function applyQuerySectionDeepLink(",
        "function openAncestorDetails(",
    ]
    for needle in forbidden:
        assert needle not in app_source, f"{needle!r} reappeared in app.js — should live in modules/router.js instead."


def test_app_js_no_longer_inlines_oauth_query_cleanup(app_source: str) -> None:
    """The OAuth callback cleanup was the third inline bit of router-ish
    logic. After #7 it should go through ``clearOAuthQueryParams`` so
    every history.replaceState call lives in one module."""
    assert "clearOAuthQueryParams(" in app_source
    # The old multi-line replaceState block had this exact tail; a
    # regression would re-introduce it.
    assert 'window.history.replaceState({}, "", u.pathname + (u.search ? u.search : ""))' not in app_source, (
        "old inline OAuth replaceState block re-introduced in app.js"
    )


def test_scan_studio_sits_on_today_after_the_shortlist() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    today = html.find('id="dashboardToday"')
    studio = html.find('id="scanStudioPanel"')
    workflow = html.find('id="workflowPrimary"')
    progress = html.find('id="workflowProgress"')
    kanban_scan = html.find('id="scanSection"')
    tools = html.find('id="todayTools"')
    assert today != -1 and studio != -1 and workflow != -1 and tools != -1
    assert workflow < progress < kanban_scan < tools < studio, (
        "Scan studio stays on Today, after the shortlist, not above the kanban"
    )
    assert html.count('id="scanStudioPanel"') == 1
    assert "scan-studio-title" in html[studio:studio + 600]
    pending = html.find('id="pendingSection"')
    history = html.find('id="pendingHistoryFold"')
    assert pending != -1 and history != -1
    assert "deleteAllTradesBtn" not in html[pending:history]
    assert 'id="deleteAllTradesBtn"' in html[history:history + 800]
    assert 'id="pendingLaneToggle"' in html
    editorial = (STATIC / "readability.css").read_text(encoding="utf-8")
    assert "today-decision-desk" in editorial
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert re.search(
        r"operations:\s*\[\s*[^\]]*[\"']scanStudioPanel[\"']",
        app,
        re.S,
    ), "operations SCREEN_SECTIONS must include scanStudioPanel"
