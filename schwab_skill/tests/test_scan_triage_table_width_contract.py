"""Contract: scan triage table width layout must not regress.

Invariant (Operations → Qualified / Near-miss):
- Exactly 5 columns (Ticker, Gate, Price, Rank, Actions)
- NO trailing spacer col/th/td (that parks Actions mid-row)
- Rank absorbs leftover width (width: auto); Actions stay fixed at 13.75rem

This has regressed twice via spacer reintroduction. Fail CI if it returns.
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


def test_triage_colspan_is_five() -> None:
    js = SCAN_TABLE_JS.read_text(encoding="utf-8")
    html = INDEX.read_text(encoding="utf-8")
    assert "const TRIAGE_COLSPAN = 5;" in js
    assert 'colspan="5"' in html
    assert 'colspan="6"' not in html
    # Empty-state and near-miss shells must stay on the 5-col contract.
    assert html.count("scan-triage-col-actions") == 2
    assert html.count("scan-candidates-table--triage") == 2


def test_rank_absorbs_leftover_actions_fixed() -> None:
    """Rank is width:auto; Actions are fixed 13.75rem in both CSS layers."""
    styles = STYLES.read_text(encoding="utf-8")
    editorial = READABILITY.read_text(encoding="utf-8")

    assert "SCAN TRIAGE WIDTH INVARIANT" in styles
    assert "Rank absorbs leftover" in styles or "Rank (col 4) absorbs leftover" in styles

    # Production styles: Rank auto, Actions pinned.
    rank_block_start = styles.index(
        "#scanSection .scan-candidates-table--triage .scan-triage-col-rank"
    )
    rank_block = styles[rank_block_start : rank_block_start + 280]
    assert "width: auto;" in rank_block
    assert "max-width:" not in rank_block.split("}")[0]

    assert "width: 13.75rem;" in styles
    assert "max-width: 13.75rem;" in styles

    # Editorial overrides must not re-fix Rank (that forces a spacer again).
    ed_rank_start = editorial.index(
        'body[data-theme="editorial"] #scanSection .scan-candidates-table--triage .scan-triage-col-rank'
    )
    # Look at the rule that includes this selector (walk back to prior rule start).
    ed_rule_start = editorial.rfind(
        'body[data-theme="editorial"] #scanSection .scan-candidates-table--triage th:nth-child(4)',
        0,
        ed_rank_start + 1,
    )
    ed_rank_block = editorial[ed_rule_start : ed_rule_start + 450]
    assert "width: auto !important;" in ed_rank_block
    assert "max-width: none !important;" in ed_rank_block
    assert "width: 5rem" not in ed_rank_block

    assert "width: 13.75rem !important;" in editorial
    assert "max-width: 13.75rem !important;" in editorial
