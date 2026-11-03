---
source: schwab_skill/operator_desk.py
created: 2026-10-06
updated: 2026-10-06
tags: [operations, dashboard, discord, backtest]
---

# Operator Desk

> Advisory agents that call the dashboard scan, grade, and backtest tools, then post a short brief to Discord.

## What it does

The desk is a set of agents, not a new signal:

- Health checks `GET /api/health`.
- Scan starts `POST /api/scan` on the full S&P 1500 with every runnable scan-studio sleeve. That request sets `SIGNAL_UNIVERSE_MODE=broad` and turns the quality prefilter off for that scan only. The saved focus target stays in place for other scans.
- Filter keeps names with `signal_score` >= 50 and drops stale, conflict, or blocked rows. This is a display filter. It does not change execution.
- Grade runs the existing shadow debate grade. It does not change rank.
- Backtest reads `GET /api/backtest-runs` and, when asked, queues a trailing 365-day SP1500 replay. A stored profit factor is one window, not the multi-era promotion test.
- Equity, when a ticker is passed, calls `GET /api/check/{ticker}` and keeps that verdict separate from the grade.
- Discord posts a short brief through the existing webhook. Each name lists every sleeve it passed, such as Stage 2 breakout and 10-month SMA. No order is sent.
- A full-universe scan can run longer than 20 minutes. The desk waits for `OPERATOR_DESK_SCAN_TIMEOUT_SEC` (default 5400). If the scan is still running when that wait ends, the brief says so and does not call the book empty. A dashboard watchdog mark is not treated as a finished scan while the worker can still complete.

## Schedule

`OPERATOR_DESK_MODE` defaults to `off`. `shadow` runs the weekday desk at 8:30 America/New_York, reads the latest backtest, and can post Discord. `live` also queues a replay when the worker is idle. Neither mode places orders. The local dashboard clock and `main.py` share one slot file so the morning run posts once.

## Gate

The brief quotes `phase1_progress_signal_gate.json` for `control_legacy` and says whether that row clears PF mean >= 1.20 and worst-era PF >= 1.00.

## Related Pages

- [[frontend-route-contract]] — `?section=desk` opens `#operatorAgentsSection`
- [[discord-integration]] — webhook used for the brief
- [[backtest]] — replay the desk can queue
- [[signal-scanner]] — scan the desk starts

---

*Last compiled: 2026-10-06*
