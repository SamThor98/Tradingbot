"""Write Alex backtest review as a classic Word .doc (RTF).

RTF opens in every desktop Word install; avoids OneDrive/python-docx quirks.
"""

from __future__ import annotations

from pathlib import Path


def rtf_escape(text: str) -> str:
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if ch in {"\\", "{", "}"}:
            out.append("\\" + ch)
        elif code < 128:
            out.append(ch)
        else:
            out.append(f"\\u{code}?")
    return "".join(out)


def para(text: str, *, bold: bool = False, size: int = 22, space_after: int = 120) -> str:
    flags = r"\b " if bold else ""
    return rf"{{\pard\sa{space_after}\f0\fs{size} {flags}{rtf_escape(text)}\par}}"


def heading(text: str, level: int = 1) -> str:
    size = 32 if level == 1 else 26
    return rf"{{\pard\sb240\sa120\b\f0\fs{size} {rtf_escape(text)}\par}}"


def bullet(text: str) -> str:
    return rf"{{\pard\li360\sa60\f0\fs22 \bullet  {rtf_escape(text)}\par}}"


def table(headers: list[str], rows: list[list[str]]) -> str:
    # Simple tab-separated pseudo-table: most reliable in RTF without cell hell.
    lines = ["\t".join(headers)] + ["\t".join(r) for r in rows]
    parts = [rf"{{\pard\sa60\b\f0\fs18 {rtf_escape(lines[0])}\par}}"]
    for line in lines[1:]:
        parts.append(rf"{{\pard\sa40\f0\fs18 {rtf_escape(line)}\par}}")
    return "".join(parts)


def build_rtf() -> str:
    chunks = [
        r"{\rtf1\ansi\deff0",
        r"{\fonttbl{\f0 Calibri;}}",
        r"\f0\fs22",
        para("Alex / Swing Edge v10", bold=True, size=40, space_after=60),
        para("Independent Backtest Review", bold=True, size=28, space_after=60),
        para(
            "August 13, 2026  |  Schwab daily bars  |  717-name universe  |  5 market eras",
            size=18,
            space_after=200,
        ),
        para(
            "Replay of Swing Edge v10 (labeled Alex) on the 717-name universe. "
            "This is a research review, not a live-trading recommendation."
        ),
        heading("The Bottom Line"),
        para(
            "The strategy does not clear our robustness gates. Profit-factor mean across five "
            "eras is 1.118 (we require at least 1.20). The weakest era profit factor is 0.686 "
            "(we require at least 1.00).",
            bold=True,
        ),
        para(
            "Your published 2020-2026 book (profit factor 1.59, +3,112% baseline) does not hold "
            "up when the same rules are run through 2015-2019. The two eras before the screening "
            "window are the ones that fail."
        ),
        heading("What We Tested"),
        para("Rules followed the Swing Edge v10 summary:"),
        bullet("Entry: RSI(14) at or below 30, and close at least 5% below the 20-day SMA"),
        bullet("Information Technology excluded; rank by lowest RSI, then SMA drop"),
        bullet("SPY 10-day rate of change at or below -1.0% blocks all new entries"),
        bullet("SHY 60-day rate of change at or below -1.5% blocks Financials and Real Estate"),
        bullet("Stop = ATR(14) x 1.5; target = 85% of 2R; RSI 50 exit; 7-day time stop"),
        bullet("Max 12 positions; 1.1% equity risk; loss-streak and drawdown size tiers"),
        bullet("7-session cooldown after exit"),
        para("Fill and book conventions:"),
        bullet("Next-day open fill, plus 0.05% entry slippage and $1 commission per side"),
        bullet("$5,000 starting equity per era (eras are independent)"),
        bullet("Schwab daily bars; 716 of 717 names loaded (ASKR had no Schwab history)"),
        bullet("Same five market eras used for our other strategies, not only 2020-2026"),
        heading("Robustness Gates"),
        table(
            ["Gate", "Required", "Alex result"],
            [
                ["Equal-weight mean of per-era profit factor", "1.20 or higher", "1.118 FAIL"],
                ["Worst-era profit factor", "1.00 or higher", "0.686 (volatility chop) FAIL"],
            ],
        ),
        heading("Per-Era Results"),
        table(
            ["Era", "Window", "Trades", "PF net", "Win rate", "Return", "Max DD", "Ending equity"],
            [
                ["Late bull", "2015-2017", "1058", "0.699", "41.2%", "-37.7%", "-44.6%", "$3115"],
                ["Volatility chop", "2018-2019", "656", "0.686", "40.6%", "-31.9%", "-35.5%", "$3407"],
                ["Crash / recovery", "2020-2021", "498", "1.893", "53.0%", "+87.0%", "-17.1%", "$9348"],
                ["Bear / rates", "2022-2023", "536", "1.016", "51.3%", "+10.3%", "-19.7%", "$5515"],
                ["Recent / current", "2024-Aug 2026", "917", "1.295", "52.1%", "+125.2%", "-14.7%", "$11259"],
            ],
        ),
        para(
            "Time stops were the most common exit in every era "
            "(example: 520 of 917 trades in 2024-2026)."
        ),
        heading("How to Read This vs the Original Summary"),
        para(
            "The published numbers used one 2020-2026 window that was also used to screen the "
            "717 names (at least 5 historical signals, win rate at least 50%, profit factor at "
            "least 1.10, and positive total P&L). That makes 2020-2026 in-sample for ticker selection."
        ),
        bullet(
            "2020-2021 and 2024+ look fine (profit factor 1.89 and 1.30). "
            "That is the window the universe was chosen on."
        ),
        bullet(
            "2015-2019 fails (profit factor 0.70 and 0.69, about -32% to -38%). "
            "Those years sit outside the screening window."
        ),
        bullet("2022-2023 is roughly break-even (profit factor 1.02)."),
        para(
            "So the strategy can look strong after 2020 and still fail a multi-era robustness test. "
            "Survivorship bias also remains: delisted names are absent from the list."
        ),
        para(
            "Returns above are not comparable to the single-path +3,112%: we reset $5,000 at the "
            "start of each era and fill at next-day open."
        ),
        heading("What We Are Not Claiming"),
        bullet("This is not a live trading recommendation."),
        bullet("Alex was not wired into our live Stage 2 scanner."),
        bullet("We did not re-tune RSI, SMA gap, hold, or the 717 list after seeing these results."),
        heading("Questions"),
        bullet("Was 2015-2019 ever run on this same 717 list, or only 2020-2026?"),
        bullet(
            "Are you OK treating next-day open as the honest fill, "
            "or do you want a same-close baseline side-by-side?"
        ),
        bullet("Should ASKR be a different ticker in the Materials list?"),
        para("Happy to share the era JSON or rerun a variant if you want to compare notes."),
        heading("Run Details"),
        para(
            "Run id: alex_native_717. Artifact: schwab_skill/validation_artifacts/"
            "multi_era_backtest_schwab_only_alex_native_717.json. Completed August 13, 2026."
        ),
        "}",
    ]
    return "".join(chunks)


def main() -> None:
    payload = build_rtf().encode("ascii", errors="replace")
    targets = [
        Path.home() / "Desktop" / "Alex_Strategy_Backtest_Review.doc",
        Path.home() / "Downloads" / "Alex_Strategy_Backtest_Review.doc",
        Path(r"C:\Users\stton\OneDrive\Documents\TradingBot") / "Alex_Strategy_Backtest_Review.doc",
    ]
    for path in targets:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        print(f"Wrote {path} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
