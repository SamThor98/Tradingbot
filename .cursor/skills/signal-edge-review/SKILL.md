---
name: signal-edge-review
description: >-
  Read-only review of TradingBot scanner, backtest, scoring, exit, and
  quality-gate diffs against profit-factor promotion gates and data-quality
  policy. Use after changing signal_scanner.py, backtest.py, scoring, exit
  logic, or quality gates, and when the user asks for a signal-edge review.
---

# Signal-edge review

Review the diff. Do not edit files in this pass. Do not invent prices, filings,
or backtest numbers.

When this same conversation wrote the diff, delegate the review to a fresh
read-only pass (Task, explore) and pass it the checklist below. The reviewer
reads the diff and the files. It does not reuse the implementer's reasoning.

## Read first

- `schwab_skill/signal_edge_ledger.json` for the current verdict (`plugin_live` stays blocked until that file changes)
- `schwab_skill/phase1_progress_signal_gate.json` for measured signal-gate rows only (ignore `unranked_insufficient_sample`)
- `schwab_skill/README.md` Recommended Rollout Sequence
- The diff for `signal_scanner.py`, `stage_analysis.py`, `backtest.py`,
  `core/scoring_rank_v2.py`, exit logic, and quality-gate config

Promotion targets, until that ledger says otherwise:

- Profit-factor mean ≥ 1.20 across the multi-era backtest
- Worst-era profit factor ≥ 1.00

Plugin modes `REGIME_V2_MODE`, `EXIT_MANAGER_MODE`, and
`CORRELATION_GUARD_MODE` stay OFF or SHADOW while the ledger says
`plugin_live: blocked`. A book can clear the numeric floors and still
not authorize a LIVE promotion.

## Checklist

- New filters log diagnostics (`*_would_filter` or a counter initialized to 0)
  before any hard reject. `QUALITY_GATES` stays shadow until a promotion task
  says otherwise.
- Scanner logic changes have a matching backtest path.
- Config went through a `config.py` getter. No new env var with only a literal.
- `data_quality` and guardrail errors are still surfaced. Fail-closed behavior
  for `DATA_QUALITY_EXEC_POLICY` and `RISK_FAIL_CLOSED_ON_DATA_OUTAGE` is intact.
- A profit-factor claim appears only next to a command this session actually ran.

## Report

```markdown
## Signal-edge review

- Scope: files reviewed
- Ledger: verdict quoted from signal_edge_ledger.json (`plugin_live`, and the book role you used)
- Validators: command and outcome, or "not run"
- Blocking: findings that should be fixed before calling the change done
- Notes: non-blocking observations
```

Blocking means a skipped shadow stage, a profit-factor number with no run, a
scanner change with no backtest counterpart, or a fail-closed gate that was
softened. An empty blocking list is a valid result.
