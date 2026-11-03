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
    assert "PARKED_SECTIONS" in src
    assert "researchSummaryLanding" in src
    assert "applyResearchDensity" in src
    assert "applyResearchCheckMode" in src
    # Triage landing belongs to Portfolio, not every Research sub-tab.
    portfolio_block = src.split("portfolio:", 1)[1].split("check:", 1)[0]
    assert "researchSummaryLanding" in portfolio_block
    assert "researchPortfolioPrimary" in portfolio_block
    check_block = src.split("check:", 1)[1].split("});", 1)[0]
    assert "quickCheckSection" in check_block
    assert "researchCheckPanel" in check_block
    assert "eventProbabilitySection" in check_block
    assert "pricingModelSection" in check_block
    assert "sharadarBundleSection" in check_block
    assert "sharadarCompareSection" in check_block
    assert "sectorsSection" not in check_block
    assert "researchAdvancedTools" not in check_block
    assert "researchBacktestWrap" not in src
    assert 'backtest: [' not in src


def test_index_research_tab_order_and_no_diligence_tab() -> None:
    html = INDEX.read_text(encoding="utf-8")
    assert 'data-research-tab-btn="portfolio"' in html
    assert 'data-research-tab-btn="check"' in html
    assert 'data-research-tab-btn="backtest"' not in html
    assert 'data-screen-mode="backtest"' in html
    assert 'id="backtestSummaryLanding"' in html
    assert 'id="backtestLiveMonitor"' in html
    assert 'id="backtestLeaderboard"' in html
    assert 'id="btQueueFilters"' in html
    assert 'data-bt-board-universe="' in html
    assert 'data-bt-board-sample="large"' in html
    assert 'data-research-tab-btn="diligence"' not in html
    # Portfolio appears before Quick check in the tab nav.
    nav_start = html.index('id="researchTabNav"')
    nav_end = html.index("</nav>", nav_start)
    nav = html[nav_start:nav_end]
    assert nav.index('data-research-tab-btn="portfolio"') < nav.index('data-research-tab-btn="check"')
    assert "Open Backtest" in nav
    assert "research-shell-note" not in html
    assert "Altitude" not in html
    assert 'data-research-check-mode-btn="brief"' in html
    assert 'id="researchSummaryAlert"' in html
    # Parked market/ops tools stay in DOM for frozen aliases.
    assert 'id="sectorsSection"' in html
    assert "research-tab-hidden" in html
    assert 'data-research-density-btn="comfortable"' in html
    assert 'id="portfolioSection"' in html
    assert 'id="performanceSection"' in html
    assert 'id="researchCheckPanel"' in html
    assert 'id="pricingModelSection"' in html
    assert 'id="sharadarBundleSection"' in html
    assert 'id="sharadarCompareSection"' in html
    assert 'id="eventProbabilitySection"' in html
    assert 'id="researchPortfolioPrimary"' in html
    assert 'id="researchBacktestWrap"' in html
    assert 'id="btRunCompare"' in html
    assert 'id="btComposerCatalog"' in html
    assert 'id="btUniverseMount"' in html
    assert 'id="btStrategyBoard"' in html


def test_research_layout_is_single_column() -> None:
    """Hidden tab siblings must not leave a vacant second grid track."""
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    editorial = (STATIC / "readability.css").read_text(encoding="utf-8")
    assert "body.ui-screen-research .layout-2" in css
    assert "grid-template-columns: minmax(0, 1fr)" in css
    assert "minmax(0, 1.08fr) minmax(0, 0.92fr)" not in css
    assert "minmax(0, 0.95fr) minmax(0, 1.05fr)" not in css
    assert 'body[data-theme="editorial"].ui-screen-research .layout-2' in editorial
    assert "minmax(0, 1.08fr) minmax(0, 0.92fr)" not in editorial
    assert "minmax(0, 0.95fr) minmax(0, 1.05fr)" not in editorial


def test_backtest_result_surfaces_profit_factor() -> None:
    """Single-run PF is shown with the mean ≥ 1.20 gate; not a multi-era verdict."""
    js = (STATIC / "panels" / "backtest.js").read_text(encoding="utf-8")
    assert "profit_factor_net" in js
    assert "PF_MEAN_GATE" in js
    assert "renderBacktestPfGate" in js
    assert "renderBacktestRunCompare" in js
    assert "btRunCompare" in js
    assert "not LIVE-ready" in js or "not a multi-era" in js


def test_research_ticker_loop_contract() -> None:
    """Scan → Research must run Check; Enter on the ticker box must run Check."""
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    research_js = (STATIC / "screens" / "research.js").read_text(encoding="utf-8")
    portfolio_js = (STATIC / "panels" / "portfolio.js").read_text(encoding="utf-8")
    assert "void quickCheck()" in app
    assert 'applyResearchTab("check")' in app
    assert 'e.key === "Enter"' in research_js
    assert "TICKER_RAIL_IDS" in research_js
    assert "primeTab" in research_js
    assert "research_open_ticker" in portfolio_js
    assert "data-check-sym" in portfolio_js


def test_research_ticker_identity_contract() -> None:
    """Rail changes emit one event; Quick check ignores stale in-flight responses."""
    flow = (STATIC / "modules" / "researchTickerFlow.js").read_text(encoding="utf-8")
    research_js = (STATIC / "screens" / "research.js").read_text(encoding="utf-8")
    quick_check = (STATIC / "panels" / "quickCheck.js").read_text(encoding="utf-8")
    dossier = (STATIC / "panels" / "dossier.js").read_text(encoding="utf-8")
    report = (STATIC / "panels" / "report.js").read_text(encoding="utf-8")
    sec = (STATIC / "panels" / "sec.js").read_text(encoding="utf-8")
    html = INDEX.read_text(encoding="utf-8")

    assert "research_ticker_change" in flow
    assert "function syncResearchTickerRail" in flow
    assert "RESEARCH_TICKER_RAIL_IDS" in flow
    assert "research_ticker_change" in research_js
    assert "scheduleDossierPreflight" in research_js
    assert "PREFLIGHT_DEBOUNCE_MS" in research_js
    assert "_quickCheckGen" in quick_check
    assert "isCurrentGen" in quick_check
    assert "applyQuickCheckTickerStale" in quick_check
    assert "lastQuickCheckTicker" in quick_check
    assert "as of ${asOf}" in quick_check
    assert "_dossierGen" in dossier
    assert "syncDossierExportEnabled" in dossier
    assert "applyDossierTickerStale" in dossier
    assert "_reportGen" in report
    assert "applyReportTickerStale" in report
    assert "_secCompareGen" in sec
    assert "requested_ticker_a" in sec
    assert "applySecCompareTickerStale" in sec
    assert 'id="dossierDownloadJsonBtn"' in html
    assert "disabled" in html.split('id="dossierDownloadJsonBtn"', 1)[1][:80]


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


def test_backtest_one_click_window_queue_contract() -> None:
    """Landing 5y/8y buttons must set dates and queue; local chat chips queue too."""
    html = INDEX.read_text(encoding="utf-8")
    panel = (STATIC / "panels" / "backtest.js").read_text(encoding="utf-8")
    screen = (STATIC / "screens" / "backtest.js").read_text(encoding="utf-8")
    chat = (STATIC / "panels" / "strategyChat.js").read_text(encoding="utf-8")
    assert 'id="btQueueNowSummary"' in html
    assert 'id="btQueueNowBtn"' in html
    assert 'data-bt-queue-years="5"' in html
    assert 'data-bt-queue-years="8"' in html
    assert "Queue last 5 years" in html
    assert "Queue last 8 years" in html
    assert "export async function queueBacktestWindow" in panel
    assert "queueBacktestWindow" in screen
    assert 'querySelectorAll("[data-bt-queue-years]")' in screen
    assert "adaptLocalBacktestChatUi" in chat
    assert "data-bt-queue-universe=" in html
    assert "Chat is not available here" in chat
