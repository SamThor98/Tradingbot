$ErrorActionPreference = "Stop"

$desktop = [Environment]::GetFolderPath("Desktop")
$downloads = Join-Path $env:USERPROFILE "Downloads"
$repo = "C:\Users\stton\OneDrive\Documents\TradingBot"
$outPaths = @(
    (Join-Path $desktop "Alex_Strategy_Backtest_Review.docx"),
    (Join-Path $downloads "Alex_Strategy_Backtest_Review.docx"),
    (Join-Path $repo "Alex_Strategy_Backtest_Review.docx")
)

$word = New-Object -ComObject Word.Application
$word.Visible = $false
$doc = $word.Documents.Add()
$selection = $word.Selection

function Write-Heading([string]$text, [int]$level = 1) {
    $selection.Style = "Heading $level"
    $selection.TypeText($text)
    $selection.TypeParagraph()
}

function Write-Body([string]$text, [bool]$bold = $false) {
    $selection.Style = "Normal"
    $selection.Font.Bold = $bold
    $selection.TypeText($text)
    $selection.Font.Bold = $false
    $selection.TypeParagraph()
}

function Write-Bullet([string]$text) {
    $selection.Style = "List Bullet"
    $selection.TypeText($text)
    $selection.TypeParagraph()
}

function Write-Table([string[]]$headers, [string[][]]$rows) {
    $selection.Style = "Normal"
    $table = $doc.Tables.Add($selection.Range, ($rows.Count + 1), $headers.Count)
    $table.Style = "Table Grid"
    for ($c = 0; $c -lt $headers.Count; $c++) {
        $table.Cell(1, $c + 1).Range.Text = $headers[$c]
        $table.Cell(1, $c + 1).Range.Font.Bold = $true
    }
    for ($r = 0; $r -lt $rows.Count; $r++) {
        for ($c = 0; $c -lt $headers.Count; $c++) {
            $table.Cell($r + 2, $c + 1).Range.Text = $rows[$r][$c]
        }
    }
    $selection.EndKey(6) | Out-Null
    $selection.TypeParagraph()
}

try {
    $selection.Style = "Title"
    $selection.TypeText("Alex / Swing Edge v10")
    $selection.TypeParagraph()

    $selection.Style = "Normal"
    $selection.ParagraphFormat.Alignment = 1
    $selection.Font.Size = 14
    $selection.TypeText("Independent Backtest Review")
    $selection.TypeParagraph()
    $selection.Font.Size = 10
    $selection.TypeText("August 13, 2026  |  Schwab daily bars  |  717-name universe  |  5 market eras")
    $selection.TypeParagraph()
    $selection.ParagraphFormat.Alignment = 0
    $selection.Font.Size = 11
    $selection.TypeParagraph()

    Write-Body "Replay of Swing Edge v10 (labeled Alex) on the 717-name universe. This is a research review, not a live-trading recommendation."

    Write-Heading "The Bottom Line"
    Write-Body "The strategy does not clear our robustness gates. Profit-factor mean across five eras is 1.118 (we require at least 1.20). The weakest era profit factor is 0.686 (we require at least 1.00)." $true
    Write-Body "Your published 2020-2026 book (profit factor 1.59, +3,112% baseline) does not hold up when the same rules are run through 2015-2019. The two eras before the screening window are the ones that fail."

    Write-Heading "What We Tested"
    Write-Body "Rules followed the Swing Edge v10 summary:"
    Write-Bullet "Entry: RSI(14) at or below 30, and close at least 5% below the 20-day SMA"
    Write-Bullet "Information Technology excluded; rank by lowest RSI, then SMA drop"
    Write-Bullet "SPY 10-day rate of change at or below -1.0% blocks all new entries"
    Write-Bullet "SHY 60-day rate of change at or below -1.5% blocks Financials and Real Estate"
    Write-Bullet "Stop = ATR(14) x 1.5; target = 85% of 2R; RSI 50 exit; 7-day time stop"
    Write-Bullet "Max 12 positions; 1.1% equity risk; loss-streak and drawdown size tiers"
    Write-Bullet "7-session cooldown after exit"
    Write-Body "Fill and book conventions:"
    Write-Bullet "Next-day open fill, plus 0.05% entry slippage and `$1 commission per side"
    Write-Bullet "`$5,000 starting equity per era (eras are independent)"
    Write-Bullet "Schwab daily bars; 716 of 717 names loaded (ASKR had no Schwab history)"
    Write-Bullet "Same five market eras used for our other strategies, not only 2020-2026"

    Write-Heading "Robustness Gates"
    Write-Table @(
        "Gate", "Required", "Alex result"
    ) @(
        @("Equal-weight mean of per-era profit factor", "1.20 or higher", "1.118  FAIL"),
        @("Worst-era profit factor", "1.00 or higher", "0.686 (volatility chop)  FAIL")
    )

    Write-Heading "Per-Era Results"
    Write-Table @(
        "Era", "Window", "Trades", "PF (net)", "Win rate", "Return", "Max DD", "Ending equity"
    ) @(
        @("Late bull", "2015-2017", "1,058", "0.699", "41.2%", "-37.7%", "-44.6%", "`$3,115"),
        @("Volatility chop", "2018-2019", "656", "0.686", "40.6%", "-31.9%", "-35.5%", "`$3,407"),
        @("Crash / recovery", "2020-2021", "498", "1.893", "53.0%", "+87.0%", "-17.1%", "`$9,348"),
        @("Bear / rates", "2022-2023", "536", "1.016", "51.3%", "+10.3%", "-19.7%", "`$5,515"),
        @("Recent / current", "2024-Aug 2026", "917", "1.295", "52.1%", "+125.2%", "-14.7%", "`$11,259")
    )
    Write-Body "Time stops were the most common exit in every era (example: 520 of 917 trades in 2024-2026)."

    Write-Heading "How to Read This vs the Original Summary"
    Write-Body "The published numbers used one 2020-2026 window that was also used to screen the 717 names (at least 5 historical signals, win rate at least 50%, profit factor at least 1.10, and positive total P and L). That makes 2020-2026 in-sample for ticker selection."
    Write-Bullet "2020-2021 and 2024+ look fine (profit factor 1.89 and 1.30). That is the window the universe was chosen on."
    Write-Bullet "2015-2019 fails (profit factor 0.70 and 0.69, about -32% to -38%). Those years sit outside the screening window."
    Write-Bullet "2022-2023 is roughly break-even (profit factor 1.02)."
    Write-Body "So the strategy can look strong after 2020 and still fail a multi-era robustness test. Survivorship bias also remains: delisted names are absent from the list."
    Write-Body "Returns above are not comparable to the single-path +3,112%: we reset `$5,000 at the start of each era and fill at next-day open."

    Write-Heading "What We Are Not Claiming"
    Write-Bullet "This is not a live trading recommendation."
    Write-Bullet "Alex was not wired into our live Stage 2 scanner."
    Write-Bullet "We did not re-tune RSI, SMA gap, hold, or the 717 list after seeing these results."

    Write-Heading "Questions"
    Write-Bullet "Was 2015-2019 ever run on this same 717 list, or only 2020-2026?"
    Write-Bullet "Are you OK treating next-day open as the honest fill, or do you want a same-close baseline side-by-side?"
    Write-Bullet "Should ASKR be a different ticker in the Materials list?"
    Write-Body "Happy to share the era JSON or rerun a variant if you want to compare notes."

    Write-Heading "Run Details"
    Write-Body "Run id: alex_native_717. Artifact: schwab_skill/validation_artifacts/multi_era_backtest_schwab_only_alex_native_717.json. Completed August 13, 2026."

    $primary = $outPaths[0]
    if (Test-Path $primary) {
        Remove-Item -Force $primary
    }
    # 16 = wdFormatXMLDocument (.docx)
    $doc.SaveAs2($primary, 16)
    Write-Output "Wrote $primary"
    foreach ($path in $outPaths[1..($outPaths.Count - 1)]) {
        Copy-Item -Force $primary $path
        Write-Output "Copied $path"
    }
}
finally {
    $doc.Close($false)
    $word.Quit()
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($selection) | Out-Null
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($doc) | Out-Null
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($word) | Out-Null
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
