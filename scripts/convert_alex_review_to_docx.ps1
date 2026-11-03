$ErrorActionPreference = "Stop"
$src = Join-Path $env:USERPROFILE "Desktop\Alex_Strategy_Backtest_Review.doc"
$dst = Join-Path $env:USERPROFILE "Desktop\Alex_Strategy_Backtest_Review.docx"
$dst2 = Join-Path $env:USERPROFILE "Downloads\Alex_Strategy_Backtest_Review.docx"
$dst3 = "C:\Users\stton\OneDrive\Documents\TradingBot\Alex_Strategy_Backtest_Review.docx"

Get-Process WINWORD -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 1

$word = New-Object -ComObject Word.Application
$word.Visible = $false
$word.DisplayAlerts = 0
try {
    $doc = $word.Documents.Open($src, $false, $true)
    if (Test-Path $dst) { Remove-Item -Force $dst }
    $doc.SaveAs2($dst, 16)
    $doc.Close($false)
    Copy-Item -Force $dst $dst2
    Copy-Item -Force $dst $dst3
    Write-Output "DOCX_OK $dst"
    Write-Output "DOCX_OK $dst2"
    Write-Output "DOCX_OK $dst3"
}
finally {
    $word.Quit()
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($word) | Out-Null
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
