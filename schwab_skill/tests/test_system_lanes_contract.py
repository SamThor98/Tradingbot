"""Contract checks for System lane navigation (decision facelift)."""

from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "webapp" / "static"
LANES_JS = STATIC / "modules" / "systemLanes.js"
INDEX = STATIC / "index.html"
APP_JS = STATIC / "app.js"


def test_system_lanes_module_exports() -> None:
    text = LANES_JS.read_text(encoding="utf-8")
    for token in (
        "export const SYSTEM_LANES",
        "export const SYSTEM_LANE_PANELS",
        "export function applySystemLane",
        "export function wireSystemLaneNav",
        "export function getSystemLaneFromUrl",
        "export function buildSystemNextDecision",
        '"ready"',
        '"decide"',
        '"calibrate"',
        '"shadow"',
        '"review"',
        '"connect"',
        '"advanced"',
    ):
        assert token in text, f"systemLanes.js missing {token}"


def test_system_next_decision_card_in_index() -> None:
    html = INDEX.read_text(encoding="utf-8")
    assert 'id="systemNextDecision"' in html
    assert 'id="systemNextDecisionTitle"' in html
    assert 'id="systemLaneNav"' in html


def test_system_lane_nav_in_index() -> None:
    html = INDEX.read_text(encoding="utf-8")
    assert 'id="systemLaneNav"' in html
    assert 'data-system-lane="ready"' in html
    assert 'data-system-lane="decide"' in html
    assert 'data-system-lane="connect"' in html
    assert "system-command-strip" in html
    assert "system-two-zone" not in html


def test_app_wires_system_lanes() -> None:
    app = APP_JS.read_text(encoding="utf-8")
    assert "modules/systemLanes.js" in app
    assert "applySystemLane" in app
    assert "wireSystemLaneNav" in app
