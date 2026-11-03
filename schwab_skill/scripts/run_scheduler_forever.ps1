# Keeps TradingBot scheduler (main.py) running. Single instance via mutex.
$ErrorActionPreference = 'Continue'
$Dir = 'D:\TradingBot\schwab_skill'
$Py  = 'C:\Users\Sam Thoresen\AppData\Local\Programs\Python\Python312\python.exe'
$created = $false
$mutex = New-Object System.Threading.Mutex($true, 'Global\TradingBotSchedulerWrapper', [ref]$created)
if (-not $created) { exit 0 }
New-Item -ItemType Directory -Force (Join-Path $Dir 'logs') | Out-Null
function Get-Sched { Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match '(^|\s|")main\.py(\s|"|$)' -and $_.CommandLine -notmatch 'uvicorn|webapp' } }
while ($true) {
  $d = Get-Date -Format 'yyyy-MM-dd'
  $wlog = Join-Path $Dir "logs\scheduler_wrapper.log"
  $existing = Get-Sched
  if ($existing) {
    Add-Content $wlog "$(Get-Date -Format s) main.py already running (PID $($existing[0].ProcessId)); waiting"
    try { Wait-Process -Id $existing[0].ProcessId -ErrorAction SilentlyContinue } catch {}
  } else {
    $out = Join-Path $Dir "logs\main_scheduler_$d.log"; $err = Join-Path $Dir "logs\main_scheduler_$d.err.log"
    Add-Content $wlog "$(Get-Date -Format s) starting main.py"
    $p = Start-Process -FilePath 'cmd.exe' -ArgumentList "/c `"`"$Py`" main.py >> `"$out`" 2>> `"$err`"`"" -WorkingDirectory $Dir -WindowStyle Hidden -PassThru
    $p.WaitForExit()
    Add-Content $wlog "$(Get-Date -Format s) main.py exited code $($p.ExitCode)"
  }
  Start-Sleep -Seconds 30
}
