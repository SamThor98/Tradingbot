"""Source-level contract for Research shell IA (Wave R-Shell)."""

from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
TABS_JS = STATIC / "modules" / "researchTabs.js"
INDEX = STATIC / "index.html"


def test_research_tabs_default_portfolio() -> None:
    src = TABS_JS.read_text(encoding="utf-8")
    assert 'DEFAULT_RESEARCH_TAB = "portfolio"' in src
    assert 'diligence: "check"' in src
    assert "reportSectionCard" in src
    assert "secCompareSection" in src
    assert "CHECK_DEEP_SECTIONS" in src
    assert "applyResearchDensity" in src
    assert "applyResearchCheckMode" in src


def test_correlation_heatmap_shading_contract() -> None:
    """Positive corr = red intensity; negative = forest; must be editorial-scoped."""
    css = (STATIC / "readability.css").read_text(encoding="utf-8")
    risk_js = (STATIC / "panels" / "portfolioRisk.js").read_text(encoding="utf-8")
    assert 'body[data-theme="editorial"] .risk-corr-cell[data-corr-bucket="5"]' in css
    assert 'body[data-theme="editorial"] .risk-corr-cell[data-corr-bucket="-5"]' in css
    assert "color-mix(in srgb, var(--ol-bad)" in css
    assert "color-mix(in srgb, var(--ol-ok)" in css
    assert '["risk-sec-metrics", "Correlation"]' in risk_js
    assert "risk-corr-scale" in risk_js
    assert "function corrBucket" in risk_js


def test_manual_risk_reloads_when_book_changes() -> None:
    """Risk cache must key on the manual book so newly added tickers appear."""
    risk_js = (STATIC / "panels" / "portfolioRisk.js").read_text(encoding="utf-8")
    manual_js = (STATIC / "panels" / "portfolioManual.js").read_text(encoding="utf-8")
    assert "function manualBookFingerprint" in risk_js
    assert "cached._bookKey === bookKey" in risk_js
    assert "persistManualEditor" in risk_js
    assert "renderHoldingsStrip" in risk_js
    assert "export function persistManualEditor" in manual_js
    assert 'input.addEventListener("input", persistEditor)' in manual_js


def test_index_research_tab_order_and_no_diligence_tab() -> None:
    html = INDEX.read_text(encoding="utf-8")
    assert 'data-research-tab-btn="portfolio"' in html
    assert 'data-research-tab-btn="check"' in html
    assert 'data-research-tab-btn="backtest"' in html
    assert 'data-research-tab-btn="diligence"' not in html
    # Portfolio appears before Quick check in the tab nav.
    nav_start = html.index('id="researchTabNav"')
    nav_end = html.index("</nav>", nav_start)
    nav = html[nav_start:nav_end]
    assert nav.index('data-research-tab-btn="portfolio"') < nav.index(
        'data-research-tab-btn="check"'
    )
    assert nav.index('data-research-tab-btn="check"') < nav.index(
        'data-research-tab-btn="backtest"'
    )
    assert 'data-research-density-btn="comfortable"' in html
    assert 'data-research-check-mode-btn="brief"' in html
    assert 'id="researchSummaryAlert"' in html
