"""Contract: scan triage table width layout must not regress.

Invariant (Operations → Qualified / Near-miss):
- Columns: Ticker · Gate · Strategy · Vol · Price · Rank · Stop · Actions
- Exactly 8 columns. NO spacer col/th/td (that parks Actions mid-row)
- Ticker absorbs leftover width (width: auto); Actions stay fixed at 13.75rem flush right

This has regressed via spacer reintroduction. Fail CI if it returns.
"""

from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "webapp" / "static"
INDEX = STATIC / "index.html"
SCAN_TABLE_JS = STATIC / "panels" / "scanTable.js"
STYLES = STATIC / "styles.css"
READABILITY = STATIC / "readability.css"

FORBIDDEN_SPACER_TOKENS = (
    "scan-triage-col-spacer",
    "scan-th-spacer",
    "scan-col-spacer",
)


def test_no_scan_triage_spacer_column() -> None:
    """Spacer column must never return — it leaves a dead gap after Actions."""
    for path in (INDEX, SCAN_TABLE_JS, STYLES, READABILITY):
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN_SPACER_TOKENS:
            assert token not in text, f"{path.name} must not contain {token!r}"


def test_triage_colspan_is_eight() -> None:
    js = SCAN_TABLE_JS.read_text(encoding="utf-8")
    html = INDEX.read_text(encoding="utf-8")
    assert "const TRIAGE_COLSPAN = 8;" in js
    assert 'colspan="8"' in html
    assert 'colspan="9"' not in html
    assert html.count("scan-triage-col-actions") == 2
    assert html.count("scan-candidates-table--triage") == 2


def test_ticker_absorbs_leftover_actions_fixed() -> None:
    """Ticker is width:auto; Actions are fixed 13.75rem in both CSS layers."""
    styles = STYLES.read_text(encoding="utf-8")
    editorial = READABILITY.read_text(encoding="utf-8")

    assert "SCAN TRIAGE WIDTH INVARIANT" in styles
    assert "Ticker absorbs leftover" in styles or "Ticker (col 1) absorbs leftover" in styles

    ticker_block_start = styles.index(
        "#scanSection .scan-candidates-table--triage .scan-triage-col-ticker"
    )
    ticker_block = styles[ticker_block_start : ticker_block_start + 280]
    assert "width: auto;" in ticker_block
    assert "max-width:" not in ticker_block.split("}")[0]

    assert "width: 13.75rem;" in styles
    assert "max-width: 13.75rem;" in styles

    # Editorial overrides must not re-fix Ticker (that forces a spacer again).
    ed_ticker_start = editorial.index(
        'body[data-theme="editorial"] #scanSection .scan-candidates-table--triage .scan-triage-col-ticker'
    )
    ed_rule_start = editorial.rfind(
        'body[data-theme="editorial"] #scanSection .scan-candidates-table--triage th:nth-child(1)',
        0,
        ed_ticker_start + 1,
    )
    ed_ticker_block = editorial[ed_rule_start : ed_rule_start + 550]
    assert "width: auto !important;" in ed_ticker_block
    assert "max-width: none !important;" in ed_ticker_block

    assert "width: 13.75rem !important;" in editorial
    assert "max-width: 13.75rem !important;" in editorial


def test_rich_ticker_and_stop_helpers_present() -> None:
    js = SCAN_TABLE_JS.read_text(encoding="utf-8")
    assert "function formatTickerMetaLine" in js
    assert "function renderStopCell" in js
    assert "function renderStrategyCell" in js
    assert "function renderVolCell" in js
    assert "function buildRowWhyText" in js
    assert "scan-row-why" in js
    assert "ADVISORY_STOP_PCT" in js
    assert "company_name" in js
    assert "const TRIAGE_COLSPAN = 8;" in js
