# System trust audit — numbers, visuals, strategies, remote intel

Paste this into **Agent mode**. Run it with **Claude Fable (thinking, high)**. If that model is unavailable, use **Claude Opus**. Do not run number-trust, strategy-parity, or chart-binding work on a fast model.

Companion rules: `.cursor/rules/tradingbot-orchestrator.mdc`, `.cursor/rules/signal-edge-priority.mdc`, `.cursor/rules/webapp-frontend.mdc`, `.cursor/rules/webapp-figma-workflow.mdc`, `.cursor/rules/project-conventions.mdc`, `.cursor/rules/validation-release.mdc`.

Companion skills (read the file before the matching conclusion):

- `.cursor/skills/signal-edge-review/SKILL.md` — before any scanner, scoring, exit, or quality-gate conclusion
- `.cursor/skills/backtest-result-review/SKILL.md` — before quoting a profit factor
- `.cursor/skills/session-handoff/SKILL.md` — at close if `schwab_skill/` Python changed

Companion docs: `wiki/frontend-route-contract.md`, `wiki/webapp-dashboard.md`, `wiki/local-ops-box.md`, `schwab_skill/docs/DECISION_DASHBOARD_KPI_SPEC.md`, `schwab_skill/README.md` Recommended Rollout Sequence, `.cursor/prompts/um790-ops-box-implement.md`.

This prompt supersedes `.cursor/prompts/dashboard-professional-facelift.md` wherever they disagree. Contract tests win over both. That facelift prompt says the scan triage table has 8 columns. `schwab_skill/tests/test_scan_triage_table_width_contract.py` requires **9** columns and forbids a spacer column. Obey the test.

---

## 0. Session start (required)

State, then proceed:

1. **Priority:** P0 for scanner, backtest, scoring, exits, and quality gates. P1 for dashboard numbers and visuals. The remote-intel wave is ops on the UM790, not a SaaS rewrite.
2. **Files this wave will touch:** say the wave number. Wave 0 touches nothing. Later waves name files before editing.
3. **Validation you will run:** from `schwab_skill/`, `python -m ruff check` and `python -m pytest -q` on touched tests. Behavior changes: `python scripts/validate_all.py --profile local --strict --skip-backtest`. Visual changes: browser on **https://127.0.0.1:8182/** only. Never port 8000.
4. **Ledger quote:** paste `plugin_live`, `promotion_floors`, and the book role you are using from `schwab_skill/signal_edge_ledger.json`, plus the measured `control_legacy` row from `schwab_skill/phase1_progress_signal_gate.json`. Re-read the files. Do not reuse a quote from this prompt if the files have changed.

Do not start a fix wave in the same turn as an unfinished trace.

---

## 1. Goal (one sentence)

Make every operator-visible number traceable to one source, every chart honest about unit, as-of, and failure, every live or shadow strategy paired with a backtest path and a validator, and the always-on host able to keep scanning and writing intel while the operator is somewhere else.

---

## 2. Model and tools

| Role | Model | Allowed work |
|------|--------|----------------|
| Lead | Claude Fable (thinking, high) | Number provenance, strategy parity, chart binding, promotion-gate language |
| Fallback lead | Claude Opus | Same, only if Fable is unavailable |
| File mapping | explore subagents | Path lists only. The lead re-reads every cited line before treating it as a finding |
| Browser proof | cursor-ide-browser on 8182 | Exercise the changed flow. A screenshot is not verification |

Required, not optional:

- Profit-factor numbers come from a file this session opened, or from a command this session ran. `python scripts/run_hypothesis_cards.py` without `--run` only copies metrics. `--run` only when the active wave explicitly allows one card id.
- No live orders. No Co-Invest execution tools. No plugin mode edits. No commits of `.env`, `tokens_*.enc`, or credentials.
- Parallel explore output is a map. It is not a verdict.

---

## 3. Quote these before any edit

Re-read and quote. The skill text that says `plugin_live: blocked` is an example. The JSON on disk wins.

`schwab_skill/signal_edge_ledger.json` as of the prompt’s authoring pass (2026-10-21), re-verify:

- `updated`: `2026-10-08`
- `plugin_live`: `regime_v2_only`
- `promotion_floors`: `pf_mean` 1.2, `worst_era_pf` 1.0
- `plugin_live_reason`: operator signed `REGIME_V2_MODE=live` on 2026-10-06 after the capped `stage2_pts52w_cap37` replay (PF mean 1.2137, worst era 1.1054). `CORRELATION_GUARD_MODE` stays off. `SCAN_LIVE_SORT_KEY` stays `signal_score`.
- Book `bare_pre_cap`: verdict `iterate_with_caution`, `clears_floors` false (1.1624 / 1.0323)
- Book `bare_pts52w_cap37`: verdict `proceed`, `clears_floors` true (1.2137 / 1.1054). `total_return` and `max_dd` in that source file are not portfolio results
- Book `setup_confidence`: `promote` false
- `capped_rank.apply_live`: false. `promote_sort`: false
- Further plugin LIVE stays unsigned

`schwab_skill/phase1_progress_signal_gate.json`:

- Rank only rows with `sample_status` `measured`
- `control_legacy`: `pf_mean_treatment` 1.089, `worst_era_pf_treatment` 0.903, `passes_guardrails` true. That row does **not** clear 1.20 / 1.00
- `breakout_vol_100` is measured and does not pass guardrails
- `unranked_insufficient_sample` rows are not a profit factor. A 0 on those rows was an empty-book placeholder

Three PF stories already exist. Any UI that shows a profit factor must name which one it is:

1. Phase-1 `control_legacy` (1.089 / 0.903 on the gate file)
2. Ledger book `bare_pts52w_cap37` and the capped replay (1.2137 / 1.1054)
3. A single backtest run’s own profit factor

Mixing them without a label is an accuracy bug.

Host lock:

- Canonical tree: `C:\src\TradingBot` on the UM790 NVMe. `D:\TradingBot` is USB. Do not treat the USB checkout as the live bot.
- Dashboard: `python scripts/start_local_dashboard.py` → **https://127.0.0.1:8182/**. Never port 8000.
- Workers: `SCAN_STAGE_A_MAX_WORKERS=6`, `SCAN_STAGE_B_MAX_WORKERS=6`, research `--max-workers 6`. Do not set 16.
- Wi-Fi only. Do not ask for an Ethernet cable.
- Overnight five-era research stays a separate BelowNormal process via `scripts/run_overnight_ops_research.py`. Never inside the dashboard process. Never during 09:30–16:00 America/New_York without that launcher’s `--force`.

---

## 4. What a perfect visual means

A chart, tile, or table cell ships only when all of these hold:

1. The value traces to one field on one API payload, then to one function and one live quote or artifact path.
2. The unit is visible: price, percent, ratio, or profit factor. A ratio `0.23` must not render as `23` or `0.23%`.
3. The as-of time is the data’s timestamp when that differs from page-fetch time.
4. Missing, stale, synthetic, and failed are four different visible states. Missing renders em dash `—` plus a one-line reason. Never a fabricated zero, price, filing, or profit factor.
5. The graphic answers one operator question: what changed, what blocked the scan, how equity moved, or whether a gate passed.
6. It updates on the event that changes the data (local SSE `scan_*` / `trade_*`, or a stated poll) and shows the freshness budget from `schwab_skill/webapp/static/modules/freshness.js`.
7. A contract test fails if the binding or the unit breaks.
8. On 8182, rendered text matches the API JSON for a fixture or the live payload.

Restyling a graphic that fails (1) or (4) is out of scope. Replace it or label the state.

Known suspects to verify in code. Do not assume they are still broken, and do not assume they are fine:

| Suspect | Where to read |
|---------|----------------|
| SEC compare synthetic heatmap when metadata-only | `schwab_skill/webapp/static/panels/sec.js` (`buildFallbackSecCompare` and the disclaimer) |
| Decision-dashboard freshness chip uses client fetch time | `webapp/decision_dashboard_service.py` `generated_at`; `static/panels/decisionDashboard.js` |
| `release_gate_ready` also requires ablation when an artifact exists | `build_decision_dashboard_snapshot` vs `docs/DECISION_DASHBOARD_KPI_SPEC.md` |
| Scan Studio bundled strategy list when `/api/scan-catalog` fails | `static/panels/scanStudio.js` |
| Hero universe default 1500 before `/api/status` | `static/index.html`, `updateHeroKpis` in `static/app.js` |
| Backtest monitor tape vs full run ledger | `core/backtest_trade_tape.py` vs `panels/backtestMonitor.js` |
| Percent vs ratio | `tests/js/percentUnits.test.mjs` and every `%` / P(up) formatter |
| Refresh is mostly manual | `refreshAll` / `SCREEN_REFRESH_JOBS` in `app.js`; cockpit 60s in `panels/cockpit.js`; SaaS `sse_enabled: false` |

Inventory of surfaces (re-verify; do not treat as complete): five screens in `static/app.js` (`operations`, `research`, `intel`, `diagnostics`, `backtest`). Chart and KPI modules live under `static/panels/` and `static/modules/`. Decision dashboard endpoint is `GET /api/decision-dashboard`.

---

## 5. Waves

End every wave with: findings (file, function, what the operator sees, what the source says), fixes made or `not started`, commands and outcomes, a fresh ledger quote, and the next wave’s entry condition.

```text
Wave 0 trace
  -> Wave 1 number bugs -> Wave 2 visuals
  -> Wave 3 strategy parity -> Wave 4 efficiency
                          -> Wave 5 remote intel
```

### Wave 0 — Trace (read-only)

No edits. No invented profit factors. No orders.

**Number map.** For each KPI, chart, heatmap, and numeric table on Today, Research, Intel, System, and Backtest: UI function and file, API route, server builder, source artifact or quote function, unit, as-of field, refresh trigger. Mark each row `traced`, `unlabeled`, or `false` (rendered value cannot be the source value).

**Strategy matrix.** One row per live or shadow strategy: `trend_breakout`, pullback, regime v2, exit manager, exec quality, event risk, correlation guard, rank score v2. Columns: env var, getter default when env is empty, scanner function, backtest function, dashboard surface, validator script, artifact the validator reads, parity gap.

Primary files: `signal_scanner.py`, `stage_analysis.py`, `backtest.py`, `backtest_intelligence.py`, `strategy_plugins.py`, `core/scoring_rank_v2.py`, `core/horizon_strategies.py`, `execution.py`, `scripts/validate_plugin_modes.py`, `scripts/validate_all.py`.

**Ranked defect list.** Sort by operator harm: a wrong number first, then a number with no unit or no as-of, then a graphic that does not update, then a strategy with no backtest counterpart. Each item cites a line.

Stop. Do not open Wave 1 in the same turn.

### Wave 1 — Number bugs

Entry: Wave 0 defect list exists in the chat.

Fix only rows marked `false` or `unlabeled`. Prefer the formatter and the payload the panel already receives. Add or extend tests beside:

- `schwab_skill/tests/js/percentUnits.test.mjs`
- `schwab_skill/tests/test_decision_dashboard_contract.py`
- `schwab_skill/tests/test_decision_dashboard_api.py`
- `schwab_skill/tests/test_sec_compare_render_contract.py`
- `schwab_skill/tests/test_scan_transparency_contract.py`

Touch `signal_scanner.py`, `backtest.py`, scoring, or exits only when the API itself is the wrong number and the scanner change has a backtest counterpart. Then run the signal-edge review skill on that diff as a fresh read-only pass. The reviewer does not reuse the implementer’s reasoning.

Browser-check every changed surface on 8182, including empty, stale, and error. Compare rendered text to the API JSON.

### Wave 2 — Visuals that earn their place

Entry: Wave 1 tests pass, and 8182 shows the corrected units.

Give scan, pending, health, and decision-gate surfaces an as-of and an update on the event that changes them. Do not add a global poll that re-hits Schwab. Cockpit’s existing 60s refresh is not a template for every panel.

Keep the route contract: vanilla ES modules, `api.get` / `api.post` only, editorial theme (`body[data-theme="editorial"]`, tokens in `readability.css`, no new hex in panel JS), Simple / Standard / Pro still work, frozen `?section=` aliases may gain aliases and may not be removed or repointed. Scan triage stays 9 columns with no spacer.

A graphic with no operator question gets removed or folded into an existing panel. Five states per panel: loading, empty, stale, error, ready. Surface `data_quality` and guardrail errors verbatim.

Verify Today, Research, System, and Backtest in the browser at desktop width. Those screens share `refreshAll`.

### Wave 3 — Strategies verifiable

Entry: Wave 0 strategy matrix exists. Can run after Wave 0 even if Wave 2 is unfinished. Do not mix strategy edits into a visual-only diff.

One card per strategy in the matrix. Each card names the validator under `schwab_skill/scripts/validate_*.py` and the artifact it reads. Close parity gaps with tests, shadow diagnostics (`*_would_filter` or a counter initialized to 0), and fail-closed behavior.

Do not flip `REGIME_V2_MODE`, `EXIT_MANAGER_MODE`, `CORRELATION_GUARD_MODE`, `RANK_SCORE_V2_MODE`, or `PROB_RANK`. Do not edit `scripts/promotion_ledger.jsonl`. Do not edit `.env`. Quality gates stay shadow until a promotion task says otherwise. Promotion floors stay 1.20 / 1.00 until the ledger file changes. Do not start a full-universe multi-era during RTH.

If this wave changes scanner or backtest code, run signal-edge review, then from `schwab_skill/`:

```text
python -m ruff check .
python -m pytest -q
python scripts/validate_all.py --profile local --strict --skip-backtest
```

Quote the ledger again in the summary. A result card’s `promote` field stays false until the operator signs the promotion ledger.

### Wave 4 — Efficiency, after correctness

Entry: Wave 3 parity gaps that change who passes Stage A are either fixed or explicitly deferred. Do not “optimize” a wrong signal.

Profile Stage A/B and the dashboard’s chatty calls. Change only measured hot paths. Worker caps stay in `core/hardware_profile.py` (6/6/6 on this UM790). A faster scan that changes who passes Stage A is a strategy change and goes back to Wave 3.

### Wave 5 — Intel from anywhere

Entry: Wave 3 has named which process produces scan output. Can be an audit with no install.

“The server is running” means three processes on the NVMe box, not a laptop lid:

| Process | Role | If it is down |
|---------|------|----------------|
| `scripts/start_local_dashboard.py` on 8182 | Operator UI and on-demand scan API | No remote or local dashboard |
| `python main.py` minute schedule | 9:30 ET scan, Discord digests | Dashboard-only does **not** scan at 9:30 |
| `scripts/run_overnight_ops_research.py` via Task Scheduler, weekday 16:20 America/Chicago | Post-close five-era artifact | Decision dashboard keeps showing the previous artifact |

Confirm Task Scheduler points at `C:\src\TradingBot`, not `D:\`. Read `wiki/local-ops-box.md` and `scripts/install_ops_box_tasks.ps1` before adding a second scheduler.

Remote read path today is Discord (`discord_operator.py`) plus loopback 8182. SaaS on Render is not the live loop: free tier spins down, the filesystem is ephemeral, scans are request-driven, and SSE is off.

Do not publish 8182 on `0.0.0.0` to the public internet. Recommend **Tailscale** (no public port) so the same 8182 dashboard opens from another location. **Stop and wait for an explicit yes** before installing a tunnel, creating an account, or changing firewall rules.

Unattended outputs that must exist when the operator is away: last scan time, `data_quality` verbatim, the signal list or the blocker that zeroed it, and the overnight artifact the decision dashboard already knows how to read.

---

## 6. Hard no

- No live orders without ticker, side, qty, and order type in the same user message.
- No plugin LIVE promotion and no `.env` mode flips.
- No fabricated prices, filings, or backtests.
- No softening of `data_quality`, guardrails, or kill-switch banners.
- No port 8000. No scan workers at 16. No five-era job inside the dashboard process.
- No React, Tailwind, or new-framework rewrite.
- No frozen `?section=` alias removals or repoints.
- No committing `.env`, tokens, or credentials.
- Where this prompt and the facelift prompt disagree, this prompt and the contract tests win.

---

## 7. Wave report shape

```text
## Wave N — title

Ledger: plugin_live=... floors=... book=... (quoted this session)
Phase-1 control_legacy: pf_mean_treatment=... worst_era_pf_treatment=...

### Findings
- [false|unlabeled|traced] Operator sees X. Source is file:function field Y. Unit. As-of.

### Fixes
not started | files changed

### Commands
command — outcome, or not run

### Entry for next wave
one sentence
```

---

## 8. Copy-paste kickoff

```text
Run Wave 0 of .cursor/prompts/system-trust-audit.md. Read-only. Do not edit files.

Model: Claude Fable (thinking, high). If unavailable, Claude Opus. Do not use a fast model for verdicts.

Re-read and quote schwab_skill/signal_edge_ledger.json (plugin_live, promotion_floors, the book you cite) and the measured control_legacy row in schwab_skill/phase1_progress_signal_gate.json. The skill sentence "plugin_live: blocked" does not override the JSON.

Deliver:
1) Number map for Today, Research, Intel, System, Backtest (UI function, API, builder, source, unit, as-of, refresh). Mark traced / unlabeled / false.
2) Strategy matrix for trend_breakout, pullback, regime v2, exit manager, exec quality, event risk, correlation guard, rank score v2.
3) Ranked defect list with file:line. Wrong numbers first.

Verify the suspect table in the prompt. Do not assume those bugs still exist.
Dashboard reference is https://127.0.0.1:8182/ only. Canonical host is C:\src\TradingBot. D:\TradingBot is USB.
No orders. No plugin mode flips. No profit factor that you did not just read from a file.
Stop before Wave 1.
```

---

*Created 2026-10-21. Authoring-pass ledger quote is a starting point; every wave re-reads the JSON.*
