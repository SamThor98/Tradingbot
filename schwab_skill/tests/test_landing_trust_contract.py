"""Contract: tab landing chips reflect live data sources, not placeholder copy."""

from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
LANDING = STATIC / "modules" / "landingTrust.js"
APP = STATIC / "app.js"
INDEX = STATIC / "index.html"
INTEL = STATIC / "panels" / "positionIntel.js"
HEALTH = STATIC / "panels" / "healthRibbon.js"
FRESHNESS = STATIC / "modules" / "freshness.js"
TICKER = STATIC / "modules" / "researchTickerFlow.js"


def test_landing_trust_module_owns_chip_resolution() -> None:
    src = LANDING.read_text(encoding="utf-8")
    assert "export function resolveLandingFrameState" in src
    assert "export function dataQualityTone" in src
    assert "export function scanQualityFromDiagnostics" in src
    assert "Schwab connected" in src
    assert "Regime / auth OK" not in src
    assert 'mode === "intel"' in src
    assert 'mode === "operations"' in src
    assert 'mode === "research"' in src
    assert 'mode === "diagnostics"' in src
    assert 'mode === "backtest"' in src
    assert "data_quality" in src
    assert "/api/position-intel" in src
    assert "/api/pending-trades" in src
    assert "/api/scan" in src
    assert 's === "—"' in src
    assert "Empty-safe string" in src


def test_app_delegates_landing_chips_and_scan_blockers() -> None:
    src = APP.read_text(encoding="utf-8")
    assert 'from "./modules/landingTrust.js"' in src
    assert "resolveLandingTrust" in src
    assert "Regime / auth OK" not in src
    assert "Book / risk tools" not in src
    assert "Quick check ready" not in src
    assert "Stack check" not in src
    assert "Last scan data is" in src
    assert "chip.title" in src
    assert "intel_summary_refresh" in src
    assert "looksLikeTimingKey" in src
    assert "scan_stage_" in src


def test_intel_freshness_and_ticker_rail() -> None:
    intel = INTEL.read_text(encoding="utf-8")
    freshness = FRESHNESS.read_text(encoding="utf-8")
    ticker = TICKER.read_text(encoding="utf-8")
    html = INDEX.read_text(encoding="utf-8")
    assert "applyFreshness" in intel
    assert 'surface: "intel"' in intel
    assert "stampIntelState" in intel
    assert "intel_summary_refresh" in intel
    assert "intelFresh" in html
    assert 'data-mode-eyebrow="intel"' in html
    assert 'data-nav-screens="intel"' in html
    assert "intel: 300" in freshness
    assert '"intelTickerInput"' in ticker


def test_health_ribbon_wires_api_error_rate() -> None:
    src = HEALTH.read_text(encoding="utf-8")
    trust = LANDING.read_text(encoding="utf-8")
    assert "errRate" in src
    assert "API ${apiLabel}" in src
    assert "typeof errRate === \"number\"" in src
    assert "selectScanBlockerBanner" in src
    assert "scan_stage_" in trust
    assert "df_empty" in trust


def test_timing_metrics_are_not_ranked_as_scan_blockers() -> None:
    from webapp.main import _diagnostics_summary, _is_actionable_scan_blocker_key

    assert not _is_actionable_scan_blocker_key("scan_stage_a_ms")
    assert not _is_actionable_scan_blocker_key("data_provider_fallback_count")
    assert not _is_actionable_scan_blocker_key("stage2_fail")
    assert not _is_actionable_scan_blocker_key("breakout_not_confirmed")
    assert _is_actionable_scan_blocker_key("df_empty")
    summary = _diagnostics_summary(
        {
            "scan_stage_a_ms": 18422,
            "stage2_fail": 40,
            "df_empty": 2,
            "watchlist_size": 1500,
            "data_quality": "ok",
        },
        [],
    )
    keys = [row["key"] for row in summary["top_blockers"]]
    assert "scan_stage_a_ms" not in keys
    assert "stage2_fail" not in keys
    assert "df_empty" in keys


def test_scan_table_renders_provenance_in_ticker_cell() -> None:
    table = (STATIC / "panels" / "scanTable.js").read_text(encoding="utf-8")
    assert "scan-ticker-prov" in table
    assert "renderSignalProvenanceChip(" in table
    assert "void renderSignalProvenanceChip" not in table
