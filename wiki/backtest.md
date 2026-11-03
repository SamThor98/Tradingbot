---
source: schwab_skill/backtest.py
created: 2026-04-17
updated: 2026-10-10
tags: [backtest, validation, scanner, portfolio-sim]
---

# Backtest Harness

> The historical replay loop that validates strategy changes before they
> reach live trading. Shares its scanner rules with the live pipeline so
> backtest PnL stays a faithful proxy for live behaviour.

## What it does

`schwab_skill/backtest.py` walks a watchlist over a configurable date
range, replays the same Stage 2 / VCP / quality-gate logic the live
[[signal-scanner]] uses, and produces a per-trade ledger with
return/risk diagnostics. Output lands in `.backtest_results.json`
(skill root) and is consumed by validation scripts.

## Live-parity guarantees

The backtest deliberately reuses several pieces from the live engine
rather than rewriting them — this is the *only* way to make backtest
results predictive of live PnL:

- **Stage 2 / VCP checks** come from `stage_analysis` (same module
  Stage A uses live).
- **Quality gates** come from `signal_scanner._evaluate_quality_gates`
  (same code path that filters live signals).
- **Sector climate filter** uses the same SPY > 200 SMA gate.
- **Adaptive stops** come from `config.get_adaptive_stop_*` so live and
  backtest exit at the same multiples.
- **Plugin overlays** (event risk, exec quality, meta-policy) are
  applied via [[backtest-intelligence-overlay]] so shadow-mode plugins
  can be A/B'd historically before promotion.

## Data sources

- **Schwab** (primary, when `SCHWAB_ONLY_DATA=true`).
- **yfinance** fallback for ad-hoc local runs (3 retries with backoff
  on rate-limits).
- **Polygon** is reachable through the same retry path used live.

Bars are normalised to ``open/high/low/close/volume`` lower-case
columns and de-duplicated before any indicator computation.

## Costs model

Defaults that mirror the live execution profile:

| Constant | Default | Purpose |
| --- | --- | --- |
| `DEFAULT_SLIPPAGE_BPS_PER_SIDE` | 15 bps | Each side of a round trip |
| `DEFAULT_FEE_PER_SHARE` | $0.005 | Per-share commission |
| `DEFAULT_MIN_FEE_PER_ORDER` | $1.00 | Floor per order |
| `DEFAULT_MAX_ADV_PARTICIPATION` | 2% | Liquidity cap |

Override via env vars (see [[scanner-tunables]]).

## Portfolio equity simulator

Per-trade `net_return` from `_net_return_after_costs` is a per-share %
return normalised to ~$10K target notional. The legacy aggregator
chained those returns as `(1+r).cumprod()`, which is mathematically
equivalent to "100% of equity into trade 1, then 100% of resulting
equity into trade 2, ..." and produced fictional -94% to -99%
drawdowns on the Schwab universe.

`backtest._simulate_portfolio_equity` replays the trade list through a
shared equity book with a hard concurrency cap and risk-based (or
fixed-%) sizing. Profit factor is **unchanged** (it is sizing-invariant
under equal-weight); `total_return_net_pct`, `max_drawdown_net_pct`,
and `cagr_net_pct` are now real, deployable numbers.

| Env var | Default | Purpose |
| --- | --- | --- |
| `BACKTEST_PORTFOLIO_ENABLED` | `true` | Master switch. `false` = legacy aggregator (only for repro). |
| `BACKTEST_PORTFOLIO_STARTING_EQUITY` | `100000` | Notional starting capital. |
| `BACKTEST_PORTFOLIO_MAX_POSITIONS` | `10` | Concurrent-position cap. Trades arriving when the book is full are dropped and counted under `portfolio_capacity_filtered`. |
| `BACKTEST_POSITION_SIZE_PCT` | `0.05` | Fallback fixed allocation per trade (used when no stop distance available). |
| `BACKTEST_RISK_PER_TRADE_PCT` | `0.0075` | Per-trade equity risk for stop-distance sizing (Minervini/O'Neil convention). Set `0` to force fixed-%. |

Each backtest result now exposes a `portfolio_summary` block with
`avg_concurrent`, `peak_concurrent`, `capacity_filtered`,
`risk_sized_count`, `fixed_sized_count`, and `ending_equity`.

The Schwab-only multi-era runner
(`scripts/run_multi_era_backtest_schwab_only.py`) consumes the same
helper inside `_aggregate_era`. Chunks now persist `entry_date` and
`stop_pct` so the simulator can compute accurate concurrency and
risk-based sizing across the full universe; pre-patch chunks fall back
to instantaneous closes and fixed-% sizing.

See `validation_artifacts/phase0_sizing_audit_*.md` for the
before/after comparison that confirmed the legacy DDs were aggregator
artifacts, not strategy risk.

## Validation entry points

- `scripts/validate_backtest.py` — small ticker count, used by
  `validate_all --profile ci`.
- `scripts/validate_pf_robustness.py` — multi-window, multi-universe
  PF regression test feeding [[promotion-playbook]].
- `scripts/run_multi_era_backtest_schwab_only.py` — long-horizon era
  sweep, chunked + crash-resumable.
- `scripts/phase2_edge_audit.py` — defaults to the complete augmented
  `stage2_only_aug` and `control_legacy_aug` five-era runs. The older
  non-augmented `stage2_only` run contains only one era and must not be used
  as the default promotion baseline.

The 2026-07-17 re-audit (same augmented baselines) returned
`iterate_with_caution` (PF mean 1.162, worst-era PF 1.032). `bear_rates` was
the weakest PF era. The promoted breakout-buffer plus exit-grace
counterfactual clears gates (PF mean 1.212, worst-era PF 1.037); optional
rank-v2 p75 trim reaches PF mean 1.249 / worst-era 1.120.

**Polished catalog of all runs, sweeps, and decisions:**
`schwab_skill/docs/BACKTEST_CATALOG.md` (compiled from `validation_artifacts/`).

## Research redesign (design only)

Rank-v2 p75 improves PF at ~25% retention, but does not explain *why* those
trades are better. The planned research platform replaces further hard filters
with continuous features and a probabilistic ranker evaluated side-by-side on
this same harness:

- Canonical: `schwab_skill/docs/PROBABILISTIC_RANKING_RESEARCH_ARCHITECTURE.md`
- Wiki digest: [[probabilistic-ranking-research-architecture]]

No implementation until that design is approved. Legacy Stage-2 / stack /
rank-v2 remain the control path for dual-run evaluation.

## Dashboard landing (2026-09-14)

The local dashboard (`https://127.0.0.1:8182/`) has a fifth top tab **Backtest**
(`?screen=backtest`, Ctrl/Cmd+5). Frozen aliases: `?section=backtest` /
`backtests` still target `#backtestSection`; `?section=lab` opens
`#backtestSummaryLanding`; `?section=leaderboard` opens `#backtestLeaderboard`;
`?section=btmonitor` opens `#backtestLiveMonitor` (live monitor stays collapsed
until that alias or hash). Optional `?run=<id>` loads that job into the briefing.

Operator-facing names are **display-only**. Campaign `theory_name` strings stay
`wknd w36 …` in storage so the weekend campaign can detect them. The UI shows
catalog names (S&P 500, Stage 2 / VCP breakout) with no underscores.

The page is results-first, then queue. Ranking is a **research loop**, not a promotion scoreboard:

1. **Start a replay** on `#backtestSummaryLanding` — **Queue last 5 years** and
   **Queue last 8 years** set the date window and POST `/api/backtest-runs`
   immediately (2 years is secondary). A summary line shows the current index
   and strategy. Saved short/custom windows are **not** used for those buttons.
   `#btQueueNowBtn` is the 5-year control. Form date presets still only fill
   dates. With a run selected, **Queue copy of this spec**. Extra jobs wait in a FIFO
   (one local worker; cap 8 waiting). Running replays cannot be cancelled.
   Strategy chat send is SaaS-only; locally the Shortcuts tab queues named 5y/8y setups.
2. **Leaderboard** on `#backtestLeaderboard` — clusters (same sleeve + index +
   window) ranked by **trust, then robustness, then PF**: core large-n
   (`sp1500` / `sp500` / `nasdaq100` with ≥100 trades) first. Among those,
   `robustness_score` is the min finite PF across the other core indexes at the
   same window (and across 2y/5y/8y when those runs exist), so a sleeve that
   only prints on one book does not occupy the podium. Knob clones collapse to
   `variant_count`. Default filter is large-n (100+). One filter bar narrows
   window, index, and sample. Qualified still needs a parseable window and
   **at least 35 trades**. Finite PF only — infinite PF (no losing trades) is
   excluded. 10–34 trades appear under “Too few trades to rank.” Fallback
   share ≥ 0.20 is labeled **not Schwab-clean**; live/current membership is not
   point-in-time. Each row has **Test further** (2y / 5y / 8y). Same sleeve and
   index; new dates only. If that window already exists, the menu jumps to it
   (`Already have 5y`); otherwise it POSTs `queue_spec` from server-built
   `horizon_actions` (5y/8y set `skip_mirofish`). This is per-window PF, not a
   multi-era LIVE verdict. `GET /api/backtest-hub` includes `leaderboard` from
   up to 400 finished runs plus `latest_success` (never the latest queued job).
3. **Library** on `#backtestQueuePanel` — click a row to load it. Cancel applies
   to `queued` jobs only (`POST /api/backtest-runs/{id}/cancel` → 409 if running).
4. **Briefing** on `#backtestResultPanel` — short above the fold:
   `outcome.headline`, four tiles (PF · trades · return · max DD), ≤3 honesty
   chips, one primary button. Funnel, exits, sample trades, vs-previous,
   charts, extra metrics, and JSON stay under **More detail**. Thin or
   narrow-index wins still ask to **confirm on Nasdaq-100 / S&P 1500** as a
   secondary action; that is not in the row menu. A single-window PF ≥ 1.20 is
   still not LIVE-ready. The form + shortcuts collapse under `#backtestSection`
   as “Edit settings.”
5. **Related evidence** — validation / promotion files. Missing files say
   “Not generated yet.” The landing shows a promotion chip from the phase-2
   audit when present.

Operator-facing titles prefer catalog index + sleeve + window. Date presets
like `last 8 years` are not used as the hero title. Campaign `theory_name`
strings stay `wknd w36 …` in storage.

The composer under “Edit settings” matches Scan studio: named indexes
(`universe_preset`: S&P 1500 / 500 / Nasdaq-100 / sector ETFs / focused /
custom), catalog `strategy_ids` (Stage 2 live book plus shadow/paper
sleeves), and scanner variables (Stage 2 52-week %, VCP days, min score).
Horizon-only sleeves dual-admit with the same evaluators as
[[horizon-strategies]]; they do not promote plugins to LIVE. A hypothesis
label (`theory_name`) names the experiment. Custom tickers stay capped
(default 40). Russell 2000 is not wired.

Next steps are rule-based (no LLM). A single-window PF ≥ 1.20 is **not**
LIVE-ready.

APIs: `GET /api/backtest-hub`, `GET /api/backtest-runs/{id}`,
`POST /api/backtest-runs`, `POST /api/backtest-runs/{id}/cancel`,
`GET /api/backtest-artifacts`, `GET /api/backtest-artifacts/{name}`
(allowlist only; path traversal rejected).

## Weekend queue campaign (2026-09-11)

The Backtest tab on 8182 (`?screen=backtest` or `?section=lab`) now opens
with a compact worker header and a ranked leaderboard. The live monitor
(`#backtestLiveMonitor`, alias `?section=btmonitor`) stays collapsed until
opened: running job, waiting slots, recent campaign PF bars, and index mix from
the latest 100 runs (`GET /api/backtest-runs?limit=100`). While a replay is
running, the worker writes a partial tape about every 8 seconds: five best
and five worst closes so far, labeled partial. Finished runs store the same
rank on `trade_ranks`, and the monitor re-ranks those extremes across the
slice. Older saved runs have no rank until they are replayed. The queue list
defaults to Live (running + waiting) so a weekend campaign is readable.
This is not a full-catalog total — the list API caps at 100.

`scripts/run_weekend_backtest_campaign.py` keeps the local one-worker queue
full with **no cutoff** (`--no-deadline`, the default as of 2026-09-13).
Pass `--deadline` plus optional `--until` only if a stop time is needed.
It POSTs the same `StrategySpecV1` the Queue composer uses (catalog
`universe_preset` + `strategy_ids` + scanner knobs). It does **not**
place live orders.

Order of work: **confirmation queue first** (wave 57: `weekly_vcp` 5y/8y on
Nasdaq-100 / S&P 500 / S&P 1500, then `monthly_pullback` 5y/8y on S&P 500 /
S&P 1500, then `weekly_swing` 5y/8y on S&P 1500 as a survivorship diagnostic).
Wave 58 adds unique 5y/8y dual-admit follow-ups (`weekly_vcp`+`weekly_breakout`,
`monthly_pullback`+`monthly_position`) on S&P 500 / S&P 1500. Pass `--horizon-only`
to queue only those longer-window core jobs and cancel waiting 2y slots.

`--dry-run` prints the plan. State lands in
`schwab_skill/artifacts/weekend_backtest_campaign/` (gitignored).
Ready [[hypothesis-cards]] are printed with a `card` label and queued ahead of
the catalog. A fingerprint already in the plan is skipped.
This is not a promotion sweep and does not flip plugins to LIVE.

## Related Pages

- [[local-ops-box]] — overnight `--max-workers` on the dedicated mini PC; prefer NVMe over USB `D:` for unsupervised weekends
- [[signal-scanner]] — Live pipeline whose rules the backtest replays
- [[backtest-intelligence-overlay]] — Plugin-aware historical attribution
- [[probabilistic-ranking-research-architecture]] — Feature store + ML ranking design
- [[feature-store]] — Ops SQL vs research Parquet split
- [[validation]] — Where backtest fits into the broader gate matrix
- [[promotion-playbook]] — Promotion gates that consume backtest PF
- [[signal-quality-rollout]] — Live stack rollout that consumes these results
- [[frontend-route-contract]] — Frozen `?screen=backtest` / `?section=lab|leaderboard|backtest` aliases; optional `?run=`
- [[scan-catalog]] — Indexes and strategy ids the Queue composer and weekend campaign share
- [[hypothesis-cards]] — One-change cards the weekend supervisor prepends when status is ready

---

*Last compiled: 2026-09-25*
