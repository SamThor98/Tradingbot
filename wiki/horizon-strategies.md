---
source: schwab_skill/core/horizon_strategies.py
created: 2026-08-19
updated: 2026-08-19
tags: [scanner, strategies, weekly, monthly, research]
---

# Horizon Strategies

> Shadow/research evaluators for Scan studio timeframes. They resample the daily OHLCV the scanner already fetched. They do not replace the live Stage 2 + VCP book.

## What this is

Operators asked for **more than one real sleeve per timeframe**, not just labels on `trend_breakout`. Evaluators live in `core/horizon_strategies.py` and stamp `strategy_plugins` in shadow/research mode.

Live execution stays [[stage-2-analysis]] (`trend_breakout`). Plugin promotion remains OFF → SHADOW → LIVE (see [[plugin-modes]]). Selecting a sleeve in Scan studio **filters** the scan and can **dual-admit** names that fail daily VCP; it does **not** go LIVE.

Weekly and monthly rules use `resample_weekly` (Friday close) and `resample_monthly` (month-end). That is not a separate vendor weekly/monthly feed.

Intraday gap/range sleeves use **session structure on the latest daily bar**. They are not a minute-bar book. The existing live-quote overlay is still [[scan-catalog]] `breakout_confirm`.

## Stage A dual-admit

When the scan's `strategy_ids` include a dual-admit sleeve (`donchian_20`, `nr7_breakout`, `gap_and_go`, `range_expansion`, `pullback`, weekly/monthly ids), Stage A admits the name if that evaluator fires, even if daily Stage 2 / VCP fails.

Horizon-only admits:

- `entry_family=horizon`
- `executable=false` (same non-executable rule as PEAD-only)
- skip VCP hard gate and breakout-confirm (those are Stage 2 gates)
- join Stage B on extra slots (`HORIZON_STAGE_B_CAP`, default 40) so they do not steal Stage 2 shortlist capacity

Default empty `strategy_ids` (full book API) does **not** dual-admit. Horizon plugins still evaluate on Stage 2 survivors for diagnostics.

## Sleeves

| Id | Timeframe | Rule (short) |
|----|-----------|----------------|
| `gap_and_go` | intraday | ≥1% opening gap vs prior close, holds, close in upper half, volume vs 50-day avg, above 200-day SMA when present |
| `range_expansion` | intraday | Close through prior high, TR ≥ 1.25× ATR(14), close in top quartile, above 50-day SMA |
| `donchian_20` | daily | Close through prior 20-day high, 200-day SMA filter |
| `nr7_breakout` | daily | Yesterday was NR7; today closes above that high; above 50-day SMA |
| `weekly_swing` | weekly | Weinstein-style: close > 10w SMA > 30w SMA, 30w rising, within 15% of 52-week high |
| `weekly_vcp` | weekly | Last 5 weekly bars below 10-week average volume, close above 30w SMA |
| `weekly_breakout` | weekly | Weekly close through prior week high, above 30w SMA |
| `monthly_position` | monthly | Faber: month-end close above a rising 10-month SMA |
| `monthly_52w_high` | monthly | Month-end close within 5% of 52-week high and above 10-month SMA |
| `monthly_pullback` | monthly | Rising 10-month SMA, this month tagged it, close holds above |

`trend_breakout`, `pullback`, `pead_primary`, and `breakout_confirm` keep their existing paths (see [[scan-catalog]]).

## Literature sleeves (additive)

Paper strategies from the evidence ranking in the [Claude share](https://claude.ai/share/60aa957c-fd7a-4d15-95d9-b9fb4f42382c) were **appended**. Iterated ids (`ITERATED_STRATEGY_IDS` in `scan_catalog.py`) are never removed.

| Id | Timeframe | Evidence | What we actually evaluate |
|----|-----------|----------|---------------------------|
| `opening_range_breakout` | intraday | Zarattini, Barbon & Aziz 2024 (5-min ORB, stocks in play) | Daily RVOL ≥ 1.5× and close through open + prior high. **Not** a 5-minute ORB fetch on the full universe |
| `st_reversal_5d` | daily | Jegadeesh 1990, Lehmann 1990; Avramov et al. 2006 liquidity caveat | 5-session return ≤ −8%, today turns up, 50-day volume ≥ 200k |
| `overnight_gap_fade` | daily | Overnight-drift literature (Bogousslavsky; NY Fed). Edge decaying | Long fade of a ≥1.5% gap down that fills ≥50% |
| `weekly_reversal` | weekly | Same Jegadeesh/Lehmann weekly contrarian | Prior week ≤ −6%, this week closes higher |
| `momentum_12_1` | monthly | Jegadeesh & Titman 1993 (most replicated CS momentum) | Skip-last-month 12m return ≥ 20%, above 200-day SMA. Single-name screen, not WML |
| `tsmom_12m` | monthly | Moskowitz, Ooi & Pedersen 2012 | Month-end close > close 12 months ago. Single-name analog of futures TSMOM |

Skipped as not scanner-viable here: SPY overnight-hold (not a name scanner), VWAP/stat-arb (institutional), weekly futures TSMOM.

## Related Pages

- [[scan-catalog]] — dashboard grouping and API ids
- [[signal-scanner]] — Stage A/B wiring and dual-admit
- [[stage-2-analysis]] — live book these sleeves do not replace
- [[vcp-detection]] — skipped for horizon-only admits
- [[plugin-modes]] — why these stay shadow/research
- [[pead]] — the other non-executable dual-admit family

---

*Last compiled: 2026-08-19*
