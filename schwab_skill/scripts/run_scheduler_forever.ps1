# Keeps TradingBot scheduler (main.py) running. Single instance via mutex.
# Launched by the 'TradingBot Scheduler' task at logon and every 5 minutes (watchdog);
# extra launches exit immediately because the mutex is held.
$ErrorActionPreference = 'Continue'
$Dir = 'D:\TradingBot\schwab_skill'
$Py  = 'C:\Users\Sam Thoresen\AppData\Local\Programs\Python\Python312\python.exe'
$created = $false
$mutex = New-Object System.Threading.Mutex($true, 'Global\TradingBotSchedulerWrapper', [ref]$created)
if (-not $created) { exit 0 }
New-Item -ItemType Directory -Force (Join-Path $Dir 'logs') | Out-Null
$wlog = Join-Path $Dir 'logs\scheduler_wrapper.log'
function Log($m) { Add-Content $wlog "$(Get-Date -Format s) [wrapper $PID] $m" }
function Get-Sched { Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match '(^|\s|")main\.py(\s|"|$)' -and $_.CommandLine -notmatch 'uvicorn|webapp' } }
Log 'wrapper started'
$lastBeat = Get-Date
while ($true) {
  $existing = Get-Sched
  if ($existing) {
    $target = $existing[0].ProcessId
    Log "main.py already running (PID $target); watching"
    while (Get-Process -Id $target -ErrorAction SilentlyContinue) {
      Start-Sleep -Seconds 15
      if (((Get-Date) - $lastBeat).TotalMinutes -ge 5) { Log "heartbeat: main.py PID $target alive"; $lastBeat = Get-Date }
    }
    Log "main.py PID $target exited"
  } else {
    $d = Get-Date -Format 'yyyy-MM-dd'
    $out = Join-Path $Dir "logs\main_scheduler_$d.log"; $err = Join-Path $Dir "logs\main_scheduler_$d.err.log"
    Log 'starting main.py'
    $p = Start-Process -FilePath 'cmd.exe' -ArgumentList "/c `"`"$Py`" main.py >> `"$out`" 2>> `"$err`"`"" -WorkingDirectory $Dir -WindowStyle Hidden -PassThru
    while (-not $p.HasExited) {
      Start-Sleep -Seconds 15
      if (((Get-Date) - $lastBeat).TotalMinutes -ge 5) { Log "heartbeat: main.py (cmd PID $($p.Id)) alive"; $lastBeat = Get-Date }
    }
    Log "main.py exited code $($p.ExitCode)"
  }
  Start-Sleep -Seconds 30
}
