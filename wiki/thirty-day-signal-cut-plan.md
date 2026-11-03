---
source: n/a (operator plan from the 2026-10-08 signal-edge review)
created: 2026-10-08
updated: 2026-10-16
tags: [runbook, p0, signal-edge, promotion]
---

# 30-day signal-cut plan

> 2026-10-09 through 2026-11-07. Lock the live Stage 2 stack, close the top-N question, decide correlation guard once, and harvest the PEAD paper sleeve. No new ranker.

## Progress (2026-10-08)

Week 1's measurement is in `validation_artifacts/cap37_unsorted_vs_live.json`. `capped_rank.verdict` is `top5_not_the_lever`. `promote_sort` and `apply_live` are false. `signal_edge_ledger.json` quotes the arms under `capped_rank`.

On the raw capped book the top-5 cut removes 88 names (2,110 → 2,022; PF mean 1.1624 / worst era 1.0514). The local `.env` has `SIGNAL_TOP_N=3`, and that value was left as-is. The same run scores that cut: 265 names removed (n=1,845; PF mean 1.1532 / worst era 1.0438). Both miss the 1.20 mean. The July buffer-plus-exit p76 row (1.3231 / 1.0343, n=859) remains the live-stack measurement.

The desk was already `OPERATOR_DESK_MODE=shadow`. `OPERATOR_DESK_RUN_SCAN=false` was added, and the slot moved from 8:30 to 11:30 New York so it grades the 9:30 scan instead of starting a second one. Each finished brief appends `validation_artifacts/live_stack_watch.jsonl`. That file stays empty until the next weekday slot.

PEAD paper, from `r7_may_promote` on `.hypothesis_ledger.json` this session: S1 at T+5 is **21 / 40**, expectancy **1.3473**, hit rate **0.5714**. The extension and high-surprise slices have no measured rows. `STRATEGY_PEAD_PRIMARY_ALLOW_LIVE` stays false. No after-hours paper scan was started.

Correlation guard stays off. The October 14 signature is still an operator action.

## Scoreboard this plan starts from

Floors stay profit-factor mean ≥ 1.20 and worst-era ≥ 1.00. Drawdown reference for the signed capped replay is 15.61% at 10% size and 100% gross (`signal_edge_ledger.json`, 2026-10-06).

| Book | Where | n | PF mean | Worst era | What it decides |
|---|---|---:|---:|---:|---|
| Capped replay `stage2_pts52w_cap37` | Ledger, 2026-10-06 | 15,994 | 1.2137 | 1.1054 | Regime v2 may stay live. `plugin_live` is `regime_v2_only`. |
| July stack under `pts_52w` ≤ 37, after the 1% buffer and exit grace | `sweep_cf_rank_under_pts52w_cap37_control_legacy_aug.md` | 859 | 1.3231 | 1.0343 | p76 stays the live trim on that path. p75 fails `late_bull`. |
| Cap only, same July file | stack counterfactual | 8,773 | 1.2603 | 1.0616 | Cap alone already clears. |
| Cap then p76, then daily top 5 by `signal_score` | Same-day run of `compare_cap37_unsorted_vs_live.py` helpers on `control_legacy_aug` chunks, 2026-10-08 | 2,022 | 1.1624 | 1.0514 | Top 5 is not the lever. See below. |
| Uncapped p76 top 10 / p76 all | `validation_artifacts/cap37_unsorted_vs_live.json` `capacity` | 3,854 / 3,952 | 1.0597 / 1.0628 | 0.9123 / 0.9123 | `keep_live_top5`. Wider top-N inside uncapped p76 misses both floors. |
| `control_legacy` in `phase1_progress_signal_gate.json` | June 2026 ranking | — | 1.089 | 0.903 | Old overlay ranking. Not this month’s scoreboard. |

The 2026-10-08 arm, same chunks and week-block intervals as the October 6 compare:

| Arm | n | PF mean | Worst era | Week-block interval |
|---|---:|---:|---:|---|
| `pts_52w` ≤ 37 | 8,791 | 1.2584 | 1.0525 | 1.093–1.371 |
| Cap, then p76 | 2,110 | 1.1531 | 1.0484 | 0.990–1.333 |
| Cap, then p76, then top 10 | 2,093 | 1.1678 | 1.0484 | 1.015–1.340 |
| Cap, then p76, then top 5 | 2,022 | 1.1624 | 1.0514 | 1.017–1.347 |

Top 5 removes 88 trades from the capped p76 book (2,110 → 2,022). Those three arms sit on top of each other. None of them is the July buffer-path result. The July 1.323 / 1.034 figure is p76 **after** the 1% breakout buffer and exit grace. That path stays the live stack. The October “unsorted cap beats live top 5” line compared the cap to p76 **without** the cap (`current_live_p76_top5` n=3,482, PF 1.0276 / 0.9368). It is the wrong contrast for a production change.

## Live stack for the whole month

Leave these as they are signed:

- 1% breakout buffer
- Exit grace 15/40
- Rank-v2 p76
- `pts_52w` ≤ 37
- `SCAN_LIVE_SORT_KEY=signal_score`
- `SIGNAL_TOP_N` default 5
- `REGIME_V2_MODE=live` (ledger seq 20, 2026-10-06)
- `STRATEGY_PEAD_PRIMARY_ALLOW_LIVE=false`

## Closed for this month

These already have a written verdict. A session that reopens one of them is off this plan.

| Track | Verdict to keep |
|---|---|
| `SIGNAL_TOP_N` 10 or “all p76” | `keep_live_top5` in the October 6 capacity block |
| Sort-key swap to historical rank, analog, or setup confidence | `keep_shadow` / `keep_shadow_single_variable_explains_it` |
| Hard quality gates, early-stop cap, VCP-exit skip, purged net-return rank | Rollout stages 2e, 2l |
| Pasting the Stage 2 1% buffer onto PEAD | Rejected. Worst era broke offline. |

## Week 1 — 2026-10-09 to 2026-10-16 — write the verdict down

**Exit.** The cap-then-p76-then-top-5 table above lives in `validation_artifacts/`, produced by the existing compare script rather than a chat log. Live flags are unchanged.

1. Extend `scripts/compare_cap37_unsorted_vs_live.py` so one run writes the four capped arms (cap, cap+p76, cap+p76+top10, cap+p76+top5) next to the arms it already writes. `promote_sort` stays false. The script still must not set `SCAN_LIVE_SORT_KEY`, `RANK_FILTER_V2_MODE`, or `SIGNAL_TOP_N`.
2. Run from `schwab_skill/`:

```bash
python scripts/compare_cap37_unsorted_vs_live.py
python -m ruff check scripts/compare_cap37_unsorted_vs_live.py
python -m pytest -q tests/test_cap37_unsorted_shadow.py
```

Extend `tests/test_cap37_unsorted_shadow.py` for the new arms. Assert the keys exist and `promote_sort` is false. Do not assert a freshly recomputed profit factor inside the unit test; the artifact run is the measurement.

3. Quote the new artifact in `signal_edge_ledger.json` under a `capacity_note` that states two facts: uncapped wider-than-5 books stay `keep_live_top5`, and on the capped raw book the top-5 cut removes 88 names and still misses the 1.20 mean. Point at the July buffer-path p76 row as the live-stack measurement.
4. Correlation guard, Wednesday 2026-10-14. Regime v2 was signed Monday 2026-10-06. One full market week has then passed. From `schwab_skill/`:

```bash
python scripts/validate_observability_gates.py
python scripts/promotion_ledger.py tail --n 5
```

Read `correlation_guard_mode`, `correlation_guard_would_demote`, and `correlation_guard_demoted` on scans since 2026-10-06. The operator signs exactly one outcome:

- Stable shadow diagnostics and a reason the operator will say out loud → `python scripts/promotion_ledger.py append --target CORRELATION_GUARD_MODE=live --reason "<why>"`
- Thin scans, a jump in demotions, or any `data_quality` other than `ok` on the sessions being counted → append `--target CORRELATION_GUARD_MODE=off` with that reason

The agent prepares the reason. The operator runs `append`.

## Week 2 — 2026-10-17 to 2026-10-23 — watch the live book

**Exit.** One jsonl row per regular-hours session. No env edits.

Each regular-hours day, with the dashboard on `https://127.0.0.1:8182`:

```bash
python scripts/run_operator_desk.py
python scripts/run_pead_canary_paper_session.py
```

Log date, `data_quality`, signal count, `correlation_guard_would_demote`, `correlation_guard_demoted`, and whether the PEAD paper script harvested names. The operator desk already prints `data_quality`. Store the rows in `validation_artifacts/live_stack_watch.jsonl` (append-only; the desk writes this file when it finishes a brief). A session with `data_quality` other than `ok` is logged and excluded from the correlation-guard count. It does not trigger a filter change.

Skip `--queue-backtest` unless the worker is idle and the operator asked for a replay. This month does not start a new multi-era audit.

## Week 3 — 2026-10-24 to 2026-10-30 — PEAD paper count

**Exit.** A one-page count of S1 hypothesis-ledger rows: entries harvested, rows with a T+5 outcome, expectancy on the full sleeve and on the two slices the canary card already reports (not extended, surprise above 15%). `STRATEGY_PEAD_PRIMARY_ALLOW_LIVE` still false.

The bar already on [[pead]] is N ≥ 40 at T+5 with positive expectancy before any executable discussion. Twenty sessions cannot be forced to hit 40. If the count is short on October 30, record the shortfall and keep harvesting in week 4. Do not lower the bar.

Commands already wired:

```bash
python scripts/apply_pead_canary_paper_env.py
python scripts/run_pead_canary_paper_session.py
```

Stage 2 stays the only executable book. The 1% breakout buffer stays off the PEAD sleeve.

## Week 4 — 2026-10-31 to 2026-11-07 — close the month

**Exit.** This checklist is filled in on the wiki page (or a dated addendum under the same filename). No unsigned flag flips.

| Item | Done when |
|---|---|
| Top-N question | Artifact from week 1 is cited. `SIGNAL_TOP_N` still 5. `SCAN_LIVE_SORT_KEY` still `signal_score`. Rank-v2 still p76. |
| Correlation guard | Ledger seq after 20 is either `CORRELATION_GUARD_MODE=live` or `=off`, with a reason that names the scans. |
| Live watch | jsonl has one row per regular-hours session that actually ran. Missing days are listed. |
| PEAD | T+5 N and expectancy written down. `ALLOW_LIVE` false. If N ≥ 40 and expectancy is positive, the next action is a design review, scheduled after November 7, not an order path. |
| Parked tracks | Still at the verdicts in the table above. |
| Validators, only if week 1 edited Python | `python -m ruff check .` and `python -m pytest -q` on the compare script, then `python scripts/validate_all.py --profile local --strict --skip-backtest`. |

## What “improved” means on November 7

The live stack is the same one the July sweep and the October 6 replay already cleared. The month is a success when three ambiguities are gone: top-N stays 5 for a cited reason, correlation guard is signed live or signed off, and the PEAD paper sleeve has a T+5 count against N ≥ 40. A higher profit factor is not a deliverable of this window. The frozen sample cannot grow new independent weeks, and trades opened in week 2 do not finish a 40-day hold before November 7.

## Related Pages

- [[signal-ranking]] — top-N and the live sort key this plan freezes
- [[signal-quality-rollout]] — parked stages this plan refuses to reopen
- [[promotion-playbook]] — the only signature this month is allowed to append
- [[pead]] — paper canary and the N ≥ 40 bar
- [[operator-desk]] — daily brief for the live-watch log
- [[plugin-modes]] — correlation guard and regime v2
- [[settled-lessons]] — keep / sharpen / revert without applying settings
- [[autonomous-investment-network]] — Card research may run; new rankers and extra live plugins wait on this window

---

*Last compiled: 2026-10-16*
