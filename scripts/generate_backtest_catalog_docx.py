"""Generate a clean, plain-English Word summary of TradingBot backtests.

Matches the style of TradingBot_System_Overview.docx: prose + bullets,
minimal tables, no fancy shading, ASCII-safe punctuation.
"""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "schwab_skill" / "docs" / "Backtest_Catalog.docx"
# Easy-to-find copy next to the system overview
OUTPUT_ROOT = ROOT / "TradingBot_Backtest_Summary.docx"


def add_heading(doc: Document, text: str, level: int = 1) -> None:
    doc.add_heading(text, level=level)


def add_para(doc: Document, text: str, *, bold: bool = False, size: int | None = None) -> None:
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.bold = bold
    if size is not None:
        run.font.size = Pt(size)


def add_bullets(doc: Document, items: list[str]) -> None:
    for item in items:
        doc.add_paragraph(item, style="List Bullet")


def add_simple_table(doc: Document, headers: list[str], rows: list[list[str]]) -> None:
    """Plain Word table - no custom shading (avoids broken / ugly output)."""
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

    # Title
    title = doc.add_heading("TradingBot Backtest Summary", 0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle = doc.add_paragraph(
        "A plain-English read of what the historical tests showed"
    )
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if subtitle.runs:
        subtitle.runs[0].font.size = Pt(14)
        subtitle.runs[0].font.color.rgb = RGBColor(80, 80, 80)

    date_p = doc.add_paragraph("July 17, 2026  |  Schwab-only data  |  ~16,400 trades  |  5 market eras")
    date_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if date_p.runs:
        date_p.runs[0].font.size = Pt(10)
        date_p.runs[0].font.color.rgb = RGBColor(120, 120, 120)

    doc.add_paragraph()

    # --- Bottom line ---
    add_heading(doc, "The Bottom Line", 1)
    add_para(
        doc,
        "We replayed the live scanner rules on about ten years of Schwab history, "
        "across five different market climates (bull, chop, crash recovery, bear market, "
        "and recent tape). Costs (slippage and commissions) are included.",
    )
    add_para(
        doc,
        "Verdict: with the live pts_52w cap (max 37), the bare signal clears the promotion "
        "floors (PF mean 1.214, worst era 1.105). The operating stack is 1% breakout buffer + "
        "exit grace 15/40 + pts_52w cap + rank-v2 p76. Regime v2 and Correlation Guard stay "
        "in shadow until multi-session evidence is stable.",
        bold=True,
    )

    add_heading(doc, "Scoreboard", 2)
    add_simple_table(
        doc,
        ["Question", "Answer"],
        [
            ["Does the bare signal clear the promotion bar?", "Yes with pts_52w<=37 (PF 1.214 / worst 1.105)"],
            ["Does buffer + exit grace clear it under the cap?", "Yes (PF 1.28 / worst 1.06)"],
            ["Does rank trim help under the cap?", "Yes at p76 (PF 1.32 / worst 1.03); p75 fails worst era"],
            ["Weakest climate (post-cap bare)?", "2022-23 bear / rising rates (PF 1.105)"],
            ["Strongest climate?", "2020-21 crash recovery"],
            ["Do light overlays explain the pre-cap edge?", "No (nearly identical to bare)"],
        ],
    )

    add_heading(doc, "What profit factor means", 2)
    add_para(
        doc,
        "Profit factor (PF) is dollars won divided by dollars lost, after costs. "
        "1.00 is breakeven. 1.20 means winners made 20% more than losers lost.",
    )
    add_para(
        doc,
        "Promotion gates: average PF across eras at least 1.20, and every era at least 1.00. "
        "That second rule stops one great era from hiding a bad one.",
    )

    # --- How tested ---
    add_heading(doc, "How We Tested", 1)
    add_para(
        doc,
        "Same Stage 2 / VCP rules as live scanning. Five eras so the strategy has to work "
        "in more than one market mood:",
    )
    add_bullets(
        doc,
        [
            "Late bull (2015-2017) - strong uptrend years",
            "Volatility chop (2018-2019) - sideways / choppy",
            "Crash recovery (2020-2021) - crash then rebound",
            "Bear / rates (2022-2023) - bear market and rising rates",
            "Recent / current (2024-now) - closest to today's tape",
        ],
    )
    add_para(
        doc,
        "For go / no-go we use average PF and worst-era PF. Portfolio return and drawdown "
        "matter for how an account would feel, but they are not the promotion gates.",
    )

    # --- Raw signal ---
    add_heading(doc, "Raw Signal (Bare Baseline)", 1)
    add_para(
        doc,
        "Pre-cap bare Stage 2 / VCP sat below the 1.20 floor. Live pts_52w<=37 raised bare "
        "edge enough to clear promotion gates; that cap is now part of Stage A.",
    )
    add_simple_table(
        doc,
        ["Run", "Trades", "Avg PF", "Worst era", "Verdict"],
        [
            ["Bare pre-cap (historical)", "16,423", "1.162", "1.032", "Iterate with caution"],
            ["Bare post-cap pts_52w<=37", "15,994", "1.214", "1.105", "Proceed"],
            ["Control (light overlays, pre-cap)", "16,433", "1.164", "1.032", "Overlays neutral"],
        ],
    )
    add_para(
        doc,
        "Pre-cap overlays added almost nothing (+0.001 PF). The post-cap bare run is the "
        "current promotion baseline.",
    )

    add_heading(doc, "Bare signal by era", 2)
    add_simple_table(
        doc,
        ["Era", "Trades", "PF", "Win rate", "Takeaway"],
        [
            ["Crash recovery", "2,771", "1.43", "55%", "Best"],
            ["Late bull", "4,608", "1.25", "52%", "Solid"],
            ["Volatility chop", "2,549", "1.05", "53%", "Thin"],
            ["Recent / current", "3,870", "1.05", "47%", "Thin"],
            ["Bear / rates", "2,625", "1.03", "48%", "Weakest"],
        ],
    )

    # --- Live stack ---
    add_heading(doc, "What We Run Live (Promoted Stack)", 1)
    add_para(
        doc,
        "Offline we tested clearer entries, longer holds before trailing, and keeping only "
        "higher-ranked names. The combo below clears the promotion gates.",
    )
    add_simple_table(
        doc,
        ["Setup", "Kept", "Avg PF", "Worst era", "Gates"],
        [
            ["Legacy baseline", "100%", "1.17", "1.05", "Fail"],
            ["Exit grace alone", "100%", "1.17", "1.03", "Fail"],
            ["Exit grace + 1% breakout buffer (pre-cap)", "42%", "1.21", "1.04", "Pass"],
            ["Above + rank p75 (pre-cap)", "25%*", "1.25", "1.12", "Pass"],
            ["Buffer + grace under pts_52w<=37", "41%*", "1.28", "1.06", "Pass"],
            ["Above + rank p76 under cap", "24%*", "1.32", "1.03", "Pass"],
            ["Above + rank p75 under cap", "25%*", "1.30", "0.95", "Fail worst era"],
        ],
    )
    add_para(
        doc,
        "* Rank retention is versus the buffer-filtered set. Cap rows use the pts_52w<=37 subset.",
        size=9,
    )

    add_heading(doc, "Live settings today", 2)
    add_bullets(
        doc,
        [
            "pts_52w cap live at max 37 (Stage A)",
            "1% breakout buffer at entry (skip weak / marginal breakouts)",
            "Exit grace: no soft trailing exits for the first 15 days; max hold 40 days",
            "Rank-v2 p76 trim live (p75 failed worst-era under the cap)",
            "Regime v2 and Correlation Guard stay shadow; PROB_RANK stays shadow",
        ],
    )

    add_heading(doc, "Stack PF by era (exit grace + 1% buffer)", 2)
    add_simple_table(
        doc,
        ["Era", "PF"],
        [
            ["Crash recovery", "1.48"],
            ["Late bull", "1.26"],
            ["Bear / rates", "1.16"],
            ["Volatility chop", "1.12"],
            ["Recent / current", "1.04"],
        ],
    )

    # --- Kept vs rejected ---
    add_heading(doc, "What We Tried: Kept vs Rejected", 1)

    add_heading(doc, "Breakout buffer size", 2)
    add_para(
        doc,
        "Tighter buffers raise average PF but eventually fail the worst-era floor. "
        "1.0% is the robust pick.",
    )
    add_simple_table(
        doc,
        ["Buffer", "Kept", "Avg PF", "Worst era", "Decision"],
        [
            ["1.0% (chosen)", "42%", "1.21", "1.04", "Pass"],
            ["1.2%", "35%", "1.20", "0.99", "Fail"],
            ["1.5%", "28%", "1.21", "0.98", "Fail"],
            ["2.0%", "19%", "1.26", "0.96", "Fail"],
        ],
    )

    add_heading(doc, "Exit timing", 2)
    add_simple_table(
        doc,
        ["Grace / max hold", "Avg PF", "Worst era", "Decision"],
        [
            ["15 / 40 days (chosen)", "1.21", "1.04", "Pass"],
            ["15 / 30 days", "1.21", "1.04", "Also pass*"],
            ["10 / 40 days", "1.11", "0.94", "Fail"],
        ],
    )
    add_para(
        doc,
        "* 15/30 also passed offline; live policy stays 15/40 for more room to run.",
        size=9,
    )

    add_heading(doc, "Rank trim on the stack", 2)
    add_para(
        doc,
        "Pre-cap, p75 was the plateau peak. After pts_52w<=37 went live, p75 failed the "
        "worst-era floor (late bull 0.95). A capped re-sweep selected p76.",
    )
    add_simple_table(
        doc,
        ["Keep top (under cap)", "Retention", "Avg PF", "Worst era", "Decision"],
        [
            ["p74", "26%", "1.28", "0.96", "Fail"],
            ["p75", "25%", "1.30", "0.95", "Fail"],
            ["p76 (chosen)", "24%", "1.32", "1.03", "Pass"],
            ["p78", "22%", "1.26", "1.03", "Pass"],
            ["p80", "20%", "1.29", "1.03", "Pass"],
        ],
    )

    add_heading(doc, "Hard filters we rejected", 2)
    add_para(
        doc,
        "These Stage A hard gates either thinned the sample too much, failed an era, "
        "or looked good on average while hiding fragile eras:",
    )
    add_bullets(
        doc,
        [
            "Require 1.5x breakout volume - reject (worst era below 1.0)",
            "Hard stack of 1.2x volume + 1% buffer - reject (crash-era regression)",
            "Wait 2 bars to confirm breakout - reject (PF 0.83, loses money)",
            "Require PEAD + advisory confluence - hard block (almost no trades)",
            "Require either PEAD or advisory - shadow only (thin eras + regressions)",
            "PEAD as a soft score booster - keep (useful context, not a gate)",
        ],
    )

    add_heading(doc, "Why early exits hurt", 2)
    add_bullets(
        doc,
        [
            "About 22% of baseline trades stop out within 20 days - the main drag on PF.",
            "Trades held 21-40 days show much stronger PF (~3.4 to 4.3) - supports longer grace / hold.",
        ],
    )

    # --- Drawdowns ---
    add_heading(doc, "A Note on Scary Drawdowns", 1)
    add_para(
        doc,
        "Older reports showed -94% to -99% drawdowns. Those were an accounting artifact: "
        "the old math compounded each trade as if you bet the entire account every time.",
    )
    add_para(
        doc,
        "With a realistic portfolio simulator ($100k start, max 10 positions, about 0.75% "
        "risk per trade), sample drawdowns look more like -12% to -24%, while profit factor "
        "stays the same.",
    )
    add_para(
        doc,
        "Rule of thumb: use PF for promotion decisions; use portfolio return and drawdown "
        "to understand how an account would feel.",
        bold=True,
    )

    # --- Timeline ---
    add_heading(doc, "Decision Timeline", 1)
    add_bullets(
        doc,
        [
            "Apr 2026 - Fixed portfolio math (stop trusting fictional -99% drawdowns)",
            "Jun 2026 - Validated exit-grace plumbing; rejected 2-bar confirm",
            "Jun 30 - Locked the two canonical baselines (~16k trades each)",
            "Jul 7-9 - Entry buffer preferred; confluence hard gates fail",
            "Jul 13 - Sweeps: keep 1% buffer; rank p75 is the plateau peak (pre-cap)",
            "Jul 16 - Stack clears gates; rank-v2 p75 promoted live (pre-cap evidence)",
            "Jul 17 - Re-audit still iterate with caution on pre-cap bare",
            "Jul 18 - pts_52w<=37 live; bare clears gates (proceed); stack CF under cap",
            "Jul 19 - Pullback full-universe fails worst-era (0.959) - not a 1.50 candidate",
            "Jul 20 - Demote unsafe p75 under cap; re-LIVE rank p76; ledger seq 16-19",
        ],
    )

    # --- Open / takeaways ---
    add_heading(doc, "What Is Still Open", 1)
    add_bullets(
        doc,
        [
            "Bare PF floors are cleared; Regime v2 / Correlation Guard LIVE still need shadow evidence + one-plugin promotion.",
            "Keep collecting live stack evidence (entry filter rates, rank retention ~20-30%, data_quality=ok).",
            "PF 1.50 dual-track: keep EARLY_STOP shadow; park pead full-universe until complete; pullback full-universe rejected.",
            "Do not turn hard breakout-volume or confluence gates back on without a fresh five-era pass.",
            "Do not set PROB_RANK_MODE=live (KEEP SHADOW).",
        ],
    )

    add_heading(doc, "Key Takeaways", 1)
    add_bullets(
        doc,
        [
            "Bare signal with pts_52w<=37 clears PF floors - proceed, not halt.",
            "Live stack is buffer + exit grace + pts_52w cap + rank-v2 p76 (not p75 under the cap).",
            "Pre-cap overlays were neutral - do not expect new plugins to invent edge.",
            "Hard confluence and volume gates failed robustness - leave them off.",
            "Next: collect shadow evidence for Regime v2 / Correlation Guard; stretch PF 1.50 is dual-track research only.",
        ],
    )

    # Disclaimer / footer
    add_heading(doc, "Important Notes", 1)
    add_bullets(
        doc,
        [
            "Past backtest performance does not guarantee future results.",
            "This summary is an operator briefing, not financial advice.",
            "Full technical detail and artifact paths: schwab_skill/docs/BACKTEST_CATALOG.md",
        ],
    )

    footer = doc.add_paragraph(
        "Generated from validation_artifacts as of 2026-07-20. "
        "Regenerate with: python scripts/generate_backtest_catalog_docx.py"
    )
    if footer.runs:
        footer.runs[0].font.size = Pt(9)
        footer.runs[0].font.color.rgb = RGBColor(120, 120, 120)

    return doc


def main() -> None:
    doc = build_document()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(OUTPUT)
    doc.save(OUTPUT_ROOT)
    print(f"Wrote {OUTPUT}")
    print(f"Wrote {OUTPUT_ROOT}")


if __name__ == "__main__":
    main()
