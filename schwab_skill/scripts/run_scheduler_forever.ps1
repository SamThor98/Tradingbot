# Keeps TradingBot scheduler (main.py) running. Single instance via mutex.
# Launched by the 'TradingBot Scheduler' task at logon and every 5 minutes (watchdog);
# extra launches exit immediately because the mutex is held.
#
# main.py opens logs\main_scheduler_<America/Chicago date>.log and the matching
# .err.log itself, and reopens them when the Chicago date changes. This script
# does not pin those files with cmd.exe redirection: a redirect taken at
# process start cannot roll, and a quoting miss on that redirect drops the
# restarted process's output. Heartbeats stay in logs\scheduler_wrapper.log.
$ErrorActionPreference = 'Continue'
$Dir = 'D:\TradingBot\schwab_skill'
$Py  = 'C:\Users\Sam Thoresen\AppData\Local\Programs\Python\Python312\python.exe'
$env:PYTHONUNBUFFERED = '1'
$created = $false
$mutex = New-Object System.Threading.Mutex($true, 'Global\TradingBotSchedulerWrapper', [ref]$created)
if (-not $created) { exit 0 }
New-Item -ItemType Directory -Force (Join-Path $Dir 'logs') | Out-Null
$wlog = Join-Path $Dir 'logs\scheduler_wrapper.log'
function Log($m) { Add-Content $wlog "$(Get-Date -Format s) [wrapper $PID] $m" }
function Get-ChicagoDateString {
  try {
    $tz = [System.TimeZoneInfo]::FindSystemTimeZoneById('Central Standard Time')
    return [System.TimeZoneInfo]::ConvertTimeFromUtc([DateTime]::UtcNow, $tz).ToString('yyyy-MM-dd')
  } catch {
    return (Get-Date -Format 'yyyy-MM-dd')
  }
}
function Get-Sched { Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match '(^|\s|")main\.py(\s|"|$)' -and $_.CommandLine -notmatch 'uvicorn|webapp' } }
Log 'wrapper started'
$lastBeat = Get-Date
while ($true) {
  $existing = Get-Sched
  if ($existing) {
    $target = $existing[0].ProcessId
    $day = Get-ChicagoDateString
    Log "main.py already running (PID $target); watching. Today's scheduler log is logs\main_scheduler_$day.log (written by that process)."
    while (Get-Process -Id $target -ErrorAction SilentlyContinue) {
      Start-Sleep -Seconds 15
      if (((Get-Date) - $lastBeat).TotalMinutes -ge 5) { Log "heartbeat: main.py PID $target alive"; $lastBeat = Get-Date }
    }
    Log "main.py PID $target exited"
  } else {
    $day = Get-ChicagoDateString
    Log "starting main.py; output -> logs\main_scheduler_$day.log and logs\main_scheduler_$day.err.log (rolls at America/Chicago midnight)"
    try {
      $p = Start-Process -FilePath $Py -ArgumentList 'main.py' -WorkingDirectory $Dir -WindowStyle Hidden -PassThru
    } catch {
      Log "failed to start main.py: $($_.Exception.Message)"
      Start-Sleep -Seconds 30
      continue
    }
    while (-not $p.HasExited) {
      Start-Sleep -Seconds 15
      if (((Get-Date) - $lastBeat).TotalMinutes -ge 5) { Log "heartbeat: main.py PID $($p.Id) alive"; $lastBeat = Get-Date }
    }
    Log "main.py exited code $($p.ExitCode)"
  }
  Start-Sleep -Seconds 30
}
