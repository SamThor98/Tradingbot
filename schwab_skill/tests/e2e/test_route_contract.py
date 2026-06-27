"""Playwright smoke suite for the dashboard route contract.

Covers the three URL surfaces the frontend promises to keep stable
(see ``tests/test_static_router.py`` for the static-source version of
the same contract):

* ``?section=<alias>`` deep links — rewritten to the canonical ``#id``
  hash by ``modules/router.js`` (``applyQuerySectionDeepLink``).
* ``?screen=<mode>`` workspace deep links — activate one of the six
  topbar tabs (operations/research/kronos/diagnostics/settings/cockpit).
* Keyboard shortcuts — Ctrl/Cmd+1..6 screen switch and Ctrl+K palette
  (``modules/shortcuts.js``).

Run with a local dashboard already serving (default port 8000):

    python -m pytest schwab_skill/tests/e2e -q

Override the target with ``E2E_BASE_URL``. The whole module is skipped
when the server is unreachable so the unit-test run stays green without
a dashboard process.
"""

from __future__ import annotations

import os
import re
import urllib.request

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import Page, expect  # noqa: E402

BASE_URL = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8000").rstrip("/")

SCREEN_MODES = ["operations", "research", "diagnostics", "settings"]

# Legacy ?screen= aliases normalize to a topbar tab (see SCREEN_ALIASES in app.js).
SCREEN_MODE_ALIASES = [
    ("kronos", "research"),
    ("cockpit", "research"),
]

# alias -> canonical DOM id (must stay in sync with SECTION_ALIASES in
# modules/router.js; the static test asserts the full map, this suite
# exercises a representative slice end-to-end in a real browser).
ALIAS_CASES = [
    ("backtest", "backtestSection"),
    ("queue", "pendingSection"),
    ("scan", "scanSection"),
    ("forecast", "kronosForecastSection"),
    ("health", "healthRibbon"),
]


def _server_reachable() -> bool:
    try:
        with urllib.request.urlopen(BASE_URL + "/", timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _server_reachable(),
    reason=f"dashboard not reachable at {BASE_URL} (start uvicorn or set E2E_BASE_URL)",
)


@pytest.fixture()
def errors(page: Page) -> list[str]:
    """Collect uncaught page exceptions so every test can assert clean boot."""
    found: list[str] = []
    page.on("pageerror", lambda err: found.append(str(err)))
    return found


@pytest.mark.parametrize("mode", SCREEN_MODES)
def test_screen_param_activates_tab(page: Page, errors: list[str], mode: str) -> None:
    page.goto(f"{BASE_URL}/?screen={mode}")
    tab = page.locator(f'.screen-switch-btn[data-screen-mode="{mode}"]')
    expect(tab).to_have_attribute("aria-selected", "true")
    # Exactly one tab selected at a time.
    expect(page.locator('.screen-switch-btn[aria-selected="true"]')).to_have_count(1)
    assert errors == [], f"uncaught JS errors on ?screen={mode}: {errors}"


@pytest.mark.parametrize(("alias", "expected_tab"), SCREEN_MODE_ALIASES)
def test_screen_alias_activates_normalized_tab(page: Page, alias: str, expected_tab: str) -> None:
    page.goto(f"{BASE_URL}/?screen={alias}")
    tab = page.locator(f'.screen-switch-btn[data-screen-mode="{expected_tab}"]')
    expect(tab).to_have_attribute("aria-selected", "true")


def test_invalid_screen_falls_back_to_operations(page: Page) -> None:
    page.goto(f"{BASE_URL}/?screen=not-a-real-screen")
    tab = page.locator('.screen-switch-btn[data-screen-mode="operations"]')
    expect(tab).to_have_attribute("aria-selected", "true")


@pytest.mark.parametrize(("alias", "dom_id"), ALIAS_CASES)
def test_section_alias_rewrites_to_hash(page: Page, alias: str, dom_id: str) -> None:
    page.goto(f"{BASE_URL}/?section={alias}")
    # applyQuerySectionDeepLink runs during boot: the friendly alias is
    # replaced (history.replaceState) by the canonical #id hash and the
    # ?section param must not linger in the address bar.
    page.wait_for_url(f"**/*#{dom_id}", timeout=10_000)
    assert "section=" not in page.url, f"?section param leaked into URL: {page.url}"
    assert page.locator(f"#{dom_id}").count() == 1


def test_hash_change_reopens_collapsed_details(page: Page) -> None:
    page.goto(f"{BASE_URL}/?screen=research")
    target = page.locator("#sectorsSection")
    expect(target).to_have_count(1)
    # Force-close the disclosure, then navigate to it by hash: the router's
    # hashchange handler must reopen it (deep links into collapsed sections
    # would otherwise scroll to hidden whitespace).
    page.evaluate(
        """() => {
          const el = document.getElementById('sectorsSection');
          el.open = false;
          window.location.hash = '';
          window.location.hash = 'sectorsSection';
        }"""
    )
    expect(target).to_have_js_property("open", True)


@pytest.mark.parametrize(
    ("key", "mode"),
    [("Control+2", "research"), ("Control+4", "diagnostics"), ("Control+1", "operations")],
)
def test_keyboard_screen_shortcuts(page: Page, key: str, mode: str) -> None:
    page.goto(f"{BASE_URL}/")
    # Wait for the tabs to be wired before sending keys.
    expect(page.locator('.screen-switch-btn[aria-selected="true"]')).to_have_count(1)
    page.keyboard.press(key)
    tab = page.locator(f'.screen-switch-btn[data-screen-mode="{mode}"]')
    expect(tab).to_have_attribute("aria-selected", "true")
    # Shortcut also writes the screen into the URL (shareable deep link).
    page.wait_for_url(f"**/*screen={mode}*", timeout=5_000)


def test_ctrl_k_toggles_command_palette(page: Page) -> None:
    page.goto(f"{BASE_URL}/")
    dialog = page.locator("#cmdPaletteDialog")
    expect(dialog).to_have_count(1)
    page.keyboard.press("Control+k")
    expect(dialog).to_have_class(re.compile(r"\bopen\b"))
    page.keyboard.press("Control+k")
    expect(dialog).not_to_have_class(re.compile(r"\bopen\b"))


def test_simple_redirects_to_display_preset(page: Page) -> None:
    """/simple retired 2026-06-10: it 302s into the dashboard's simple
    display preset and the ?display param must not linger in the URL."""
    page.goto(f"{BASE_URL}/simple")
    expect(page.locator("body")).to_have_class(re.compile(r"\bui-simple\b"))
    assert "display=" not in page.url, f"?display param leaked into URL: {page.url}"


def test_cockpit_redirects_to_screen(page: Page) -> None:
    """/cockpit folded into Research portfolio context on the main dashboard."""
    page.goto(f"{BASE_URL}/cockpit")
    tab = page.locator('.screen-switch-btn[data-screen-mode="research"]')
    expect(tab).to_have_attribute("aria-selected", "true")
    expect(page.locator("#cockpitSection")).to_be_visible()
    expect(page.locator("#scanSection")).to_be_hidden()
    expect(page.locator("#cockpitDrawer")).not_to_have_class(re.compile(r"\bopen\b"))
    # Closed drawer must stay off-screen (card-enter animation must not pin transform).
    offscreen = page.evaluate(
        """() => {
          const d = document.getElementById('cockpitDrawer');
          if (!d) return false;
          const r = d.getBoundingClientRect();
          return r.left >= window.innerWidth - 1;
        }"""
    )
    assert offscreen, "cockpit drawer should be translated off-screen when closed"


def test_boot_has_no_uncaught_errors(page: Page, errors: list[str]) -> None:
    page.goto(f"{BASE_URL}/")
    expect(page.locator('.screen-switch-btn[aria-selected="true"]')).to_have_count(1)
    page.wait_for_timeout(1_500)  # let lazy module wiring settle
    assert errors == [], f"uncaught JS errors during boot: {errors}"
