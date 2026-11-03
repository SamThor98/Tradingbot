"""Generate the Alex / Swing Edge v10 independent backtest review Word doc."""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "Alex_Strategy_Backtest_Review.docx"
OUTPUT_DOWNLOADS = Path.home() / "Downloads" / "Alex_Strategy_Backtest_Review.docx"


def add_heading(doc: Document, text: str, level: int = 1) -> None:
    doc.add_heading(text, level=level)


def add_para(doc: Document, text: str, *, bold: bool = False) -> None:
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.bold = bold


def add_bullets(doc: Document, items: list[str]) -> None:
    for item in items:
        doc.add_paragraph(item, style="List Bullet")


def add_simple_table(doc: Document, headers: list[str], rows: list[list[str]]) -> None:
    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
    table.style = "Table Grid"
    for i, h in enumerate(headers):
        table.rows[0].cells[i].text = h
        for p in table.rows[0].cells[i].paragraphs:
            for r in p.runs:
                r.bold = True
    for r_idx, row in enumerate(rows):
        for c_idx, val in enumerate(row):
            table.rows[r_idx + 1].cells[c_idx].text = str(val)
    doc.add_paragraph()


def build_document() -> Document:
    doc = Document()

    title = doc.add_heading("Alex / Swing Edge v10", 0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle = doc.add_paragraph("Independent Backtest Review")
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if subtitle.runs:
        subtitle.runs[0].font.size = Pt(14)
        subtitle.runs[0].font.color.rgb = RGBColor(80, 80, 80)

    date_p = doc.add_paragraph(
        "August 13, 2026  |  Schwab daily bars  |  717-name universe  |  5 market eras"
    )
    date_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if date_p.runs:
        date_p.runs[0].font.size = Pt(10)
        date_p.runs[0].font.color.rgb = RGBColor(120, 120, 120)

    add_para(
        doc,
        "Replay of Swing Edge v10 (labeled Alex) on the 717-name universe you provided. "
        "This is a research review, not a live-trading recommendation.",
    )

    add_heading(doc, "The Bottom Line")
    add_para(
        doc,
        "The strategy does not clear our robustness gates. Profit-factor mean across five "
        "eras is 1.118 (we require at least 1.20). The weakest era profit factor is 0.686 "
        "(we require at least 1.00).",
        bold=True,
    )
    add_para(
        doc,
        "Your published 2020-2026 book (profit factor 1.59, +3,112% baseline) does not hold "
        "up when the same rules are run through 2015-2019. The two eras before the screening "
        "window are the ones that fail.",
    )

    add_heading(doc, "What We Tested")
    add_para(doc, "Rules followed the Swing Edge v10 summary:")
    add_bullets(
        doc,
        [
            "Entry: RSI(14) at or below 30, and close at least 5% below the 20-day SMA",
            "Information Technology excluded; rank by lowest RSI, then SMA drop",
            "SPY 10-day rate of change at or below -1.0% blocks all new entries",
            "SHY 60-day rate of change at or below -1.5% blocks Financials and Real Estate",
            "Stop = ATR(14) x 1.5; target = 85% of 2R; RSI 50 exit; 7-day time stop",
            "Max 12 positions; 1.1% equity risk; loss-streak and drawdown size tiers",
            "7-session cooldown after exit",
        ],
    )
    add_para(doc, "Fill and book conventions:")
    add_bullets(
        doc,
        [
            "Next-day open fill, plus 0.05% entry slippage and $1 commission per side (your realistic case, not same-bar close)",
            "$5,000 starting equity per era (eras are independent; not one continuous 2015-2026 compound)",
            "Schwab daily bars; 716 of 717 names loaded (ASKR had no Schwab history)",
            "Same five market eras we use for our other strategies, not only 2020-2026",
        ],
    )

    add_heading(doc, "Robustness Gates")
    add_simple_table(
        doc,
        ["Gate", "Required", "Alex result"],
        [
            ["Equal-weight mean of per-era profit factor", "1.20 or higher", "1.118  FAIL"],
            ["Worst-era profit factor", "1.00 or higher", "0.686 (volatility chop)  FAIL"],
        ],
    )

    add_heading(doc, "Per-Era Results")
    add_simple_table(
        doc,
        [
            "Era",
            "Window",
            "Trades",
            "PF (net)",
            "Win rate",
            "Return",
            "Max DD",
            "Ending equity",
        ],
        [
            ["Late bull", "2015-2017", "1,058", "0.699", "41.2%", "-37.7%", "-44.6%", "$3,115"],
            ["Volatility chop", "2018-2019", "656", "0.686", "40.6%", "-31.9%", "-35.5%", "$3,407"],
            ["Crash / recovery", "2020-2021", "498", "1.893", "53.0%", "+87.0%", "-17.1%", "$9,348"],
            ["Bear / rates", "2022-2023", "536", "1.016", "51.3%", "+10.3%", "-19.7%", "$5,515"],
            ["Recent / current", "2024-Aug 2026", "917", "1.295", "52.1%", "+125.2%", "-14.7%", "$11,259"],
        ],
    )
    add_para(
        doc,
        "Time stops were the most common exit in every era (example: 520 of 917 trades in 2024-2026).",
    )

    add_heading(doc, "How to Read This vs the Original Summary")
    add_para(
        doc,
        "The published numbers used one 2020-2026 window that was also used to screen the "
        "717 names (at least 5 historical signals, win rate at least 50%, profit factor at "
        "least 1.10, and positive total P&L). That makes 2020-2026 in-sample for ticker selection.",
    )
    add_bullets(
        doc,
        [
            "2020-2021 and 2024+ look fine (profit factor 1.89 and 1.30). That is the window the universe was chosen on.",
            "2015-2019 fails (profit factor 0.70 and 0.69, about -32% to -38%). Those years sit outside the screening window.",
            "2022-2023 is roughly break-even (profit factor 1.02).",
        ],
    )
    add_para(
        doc,
        "So the strategy can look strong after 2020 and still fail a multi-era robustness test. "
        "Survivorship bias also remains: delisted names are absent from the list.",
    )
    add_para(
        doc,
        "Returns above are not comparable to the single-path +3,112%: we reset $5,000 at the "
        "start of each era and fill at next-day open.",
    )

    add_heading(doc, "What We Are Not Claiming")
    add_bullets(
        doc,
        [
            "This is not a live trading recommendation.",
            "Alex was not wired into our live Stage 2 scanner.",
            "We did not re-tune RSI, SMA gap, hold, or the 717 list after seeing these results.",
        ],
    )

    add_heading(doc, "Questions")
    add_bullets(
        doc,
        [
            "Was 2015-2019 ever run on this same 717 list, or only 2020-2026?",
            "Are you OK treating next-day open as the honest fill, or do you want a same-close baseline side-by-side?",
            "Should ASKR be a different ticker in the Materials list?",
        ],
    )
    add_para(
        doc,
        "Happy to share the era JSON or rerun a variant if you want to compare notes.",
    )

    add_heading(doc, "Run Details")
    add_para(
        doc,
        "Run id: alex_native_717. Artifact: schwab_skill/validation_artifacts/"
        "multi_era_backtest_schwab_only_alex_native_717.json. Completed August 13, 2026.",
    )

    return doc


def main() -> None:
    doc = build_document()
    for path in (OUTPUT, OUTPUT_DOWNLOADS):
        path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(path)
        print(f"Wrote {path} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
