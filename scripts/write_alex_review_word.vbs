Option Explicit
Dim word, doc, selection, desktop, outPath, fso
Set fso = CreateObject("Scripting.FileSystemObject")
desktop = CreateObject("WScript.Shell").SpecialFolders("Desktop")
outPath = desktop & "\Alex_Strategy_Backtest_Review.docx"

On Error Resume Next
Dim p
For Each p In GetObject("winmgmts:").ExecQuery("Select * from Win32_Process Where Name='WINWORD.EXE'")
  p.Terminate
Next
On Error GoTo 0
WScript.Sleep 1000

Set word = CreateObject("Word.Application")
word.Visible = False
word.DisplayAlerts = 0
Set doc = word.Documents.Add()
Set selection = word.Selection

selection.Style = "Title"
selection.TypeText "Alex / Swing Edge v10"
selection.TypeParagraph

selection.Style = "Normal"
selection.ParagraphFormat.Alignment = 1
selection.Font.Size = 14
selection.Font.Bold = True
selection.TypeText "Independent Backtest Review"
selection.TypeParagraph
selection.Font.Size = 10
selection.Font.Bold = False
selection.TypeText "August 13, 2026 | Schwab daily bars | 717-name universe | 5 market eras"
selection.TypeParagraph
selection.ParagraphFormat.Alignment = 0
selection.Font.Size = 11
selection.TypeParagraph

selection.TypeText "Replay of Swing Edge v10 (labeled Alex) on the 717-name universe. This is a research review, not a live-trading recommendation."
selection.TypeParagraph
selection.TypeParagraph

selection.Style = "Heading 1"
selection.TypeText "The Bottom Line"
selection.TypeParagraph
selection.Style = "Normal"
selection.Font.Bold = True
selection.TypeText "The strategy does not clear our robustness gates. Profit-factor mean across five eras is 1.118 (required >= 1.20). Worst-era profit factor is 0.686 (required >= 1.00)."
selection.TypeParagraph
selection.Font.Bold = False
selection.TypeText "Your published 2020-2026 book (PF 1.59, +3,112% baseline) does not hold up when the same rules are run through 2015-2019."
selection.TypeParagraph
selection.TypeParagraph

selection.Style = "Heading 1"
selection.TypeText "What We Tested"
selection.TypeParagraph
selection.Style = "Normal"
selection.TypeText "Rules followed the Swing Edge v10 summary:"
selection.TypeParagraph
selection.Style = "List Bullet"
selection.TypeText "Entry: RSI(14) <= 30 and close >= 5% below SMA20"
selection.TypeParagraph
selection.TypeText "IT excluded; rank by lowest RSI, then SMA drop"
selection.TypeParagraph
selection.TypeText "SPY 10d ROC <= -1.0% blocks all entries"
selection.TypeParagraph
selection.TypeText "SHY 60d ROC <= -1.5% blocks Financials and Real Estate"
selection.TypeParagraph
selection.TypeText "Stop = ATR(14) x 1.5; target = 85% of 2R; RSI >= 50 exit; 7-day time stop"
selection.TypeParagraph
selection.TypeText "Max 12 positions; 1.1% equity risk; loss-streak and drawdown size tiers"
selection.TypeParagraph
selection.TypeText "7-session cooldown after exit"
selection.TypeParagraph
selection.Style = "Normal"
selection.TypeText "Fill/book: next-day open + 0.05% slippage + $1/side; $5,000 start per era; Schwab data; 716/717 names (ASKR missing)."
selection.TypeParagraph
selection.TypeParagraph

selection.Style = "Heading 1"
selection.TypeText "Robustness Gates"
selection.TypeParagraph
selection.Style = "Normal"
selection.Font.Bold = True
selection.TypeText "Gate | Required | Alex result"
selection.TypeParagraph
selection.Font.Bold = False
selection.TypeText "PF mean | >= 1.20 | 1.118 FAIL"
selection.TypeParagraph
selection.TypeText "Worst-era PF | >= 1.00 | 0.686 (volatility chop) FAIL"
selection.TypeParagraph
selection.TypeParagraph

selection.Style = "Heading 1"
selection.TypeText "Per-Era Results"
selection.TypeParagraph
selection.Style = "Normal"
selection.Font.Bold = True
selection.TypeText "Era | Window | Trades | PF net | Win rate | Return | Max DD | Ending equity"
selection.TypeParagraph
selection.Font.Bold = False
selection.TypeText "Late bull | 2015-2017 | 1,058 | 0.699 | 41.2% | -37.7% | -44.6% | $3,115"
selection.TypeParagraph
selection.TypeText "Volatility chop | 2018-2019 | 656 | 0.686 | 40.6% | -31.9% | -35.5% | $3,407"
selection.TypeParagraph
selection.TypeText "Crash / recovery | 2020-2021 | 498 | 1.893 | 53.0% | +87.0% | -17.1% | $9,348"
selection.TypeParagraph
selection.TypeText "Bear / rates | 2022-2023 | 536 | 1.016 | 51.3% | +10.3% | -19.7% | $5,515"
selection.TypeParagraph
selection.TypeText "Recent / current | 2024-Aug 2026 | 917 | 1.295 | 52.1% | +125.2% | -14.7% | $11,259"
selection.TypeParagraph
selection.TypeParagraph
selection.TypeText "Time stops were the most common exit in every era (example: 520 of 917 trades in 2024-2026)."
selection.TypeParagraph
selection.TypeParagraph

selection.Style = "Heading 1"
selection.TypeText "How to Read This vs the Original Summary"
selection.TypeParagraph
selection.Style = "Normal"
selection.TypeText "The published numbers used one 2020-2026 window that was also used to screen the 717 names (>=5 signals, WR >=50%, PF >=1.10, positive P&L). That makes 2020-2026 in-sample for ticker selection."
selection.TypeParagraph
selection.Style = "List Bullet"
selection.TypeText "2020-2021 and 2024+ look fine (PF 1.89 and 1.30)."
selection.TypeParagraph
selection.TypeText "2015-2019 fails (PF 0.70 and 0.69, about -32% to -38%)."
selection.TypeParagraph
selection.TypeText "2022-2023 is roughly break-even (PF 1.02)."
selection.TypeParagraph
selection.Style = "Normal"
selection.TypeText "Returns are not comparable to the single-path +3,112%: we reset $5,000 each era and fill at next-day open. Survivorship bias remains."
selection.TypeParagraph
selection.TypeParagraph

selection.Style = "Heading 1"
selection.TypeText "What We Are Not Claiming"
selection.TypeParagraph
selection.Style = "List Bullet"
selection.TypeText "Not a live trading recommendation."
selection.TypeParagraph
selection.TypeText "Alex was not wired into the live Stage 2 scanner."
selection.TypeParagraph
selection.TypeText "No re-tuning of RSI/SMA/hold/universe after seeing results."
selection.TypeParagraph
selection.TypeParagraph

selection.Style = "Heading 1"
selection.TypeText "Questions"
selection.TypeParagraph
selection.Style = "List Bullet"
selection.TypeText "Was 2015-2019 ever run on this same 717 list, or only 2020-2026?"
selection.TypeParagraph
selection.TypeText "OK with next-day open as honest fill, or want same-close baseline too?"
selection.TypeParagraph
selection.TypeText "Should ASKR be a different ticker in Materials?"
selection.TypeParagraph
selection.TypeParagraph

selection.Style = "Heading 1"
selection.TypeText "Run Details"
selection.TypeParagraph
selection.Style = "Normal"
selection.TypeText "Run id: alex_native_717. Artifact: schwab_skill/validation_artifacts/multi_era_backtest_schwab_only_alex_native_717.json. Completed August 13, 2026."
selection.TypeParagraph

If fso.FileExists(outPath) Then fso.DeleteFile outPath, True
' 16 = wdFormatXMLDocument
doc.SaveAs2 outPath, 16
doc.Close False
word.Quit

WScript.Echo "WROTE " & outPath
WScript.Echo "SIZE " & fso.GetFile(outPath).Size
