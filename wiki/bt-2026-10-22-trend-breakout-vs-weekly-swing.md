---
source: experiments/backtester/results_debate_2026-10-22_r2.json
created: 2026-10-22
updated: 2026-10-22
tags: [backtest, research, trend-breakout, weekly, relative-performance]
---

> **CORRECTION 2026-10-27:** the 'both' result below (TB + same-week weekly_swing, +0.38% STATE OOS) entered at the trend_breakout next open, before the Friday weekly_swing confirmation for Mon-Thu signals (lookahead). Re-simulated with entry after the Friday: OOS -0.23% T20 / -0.32% STATE, no edge. Do not use the 'both' numbers below. See [[bt-2026-10-27-both-lookahead-free]].

# trend_breakout vs weekly_swing vs both: CHEF, CRM, SAIC + pool (2026-10-22)

Debate chair round 2. Offline, read-only app code, no orders. Script: `experiments/backtester/debate_2026_10_22_r2.py`.
Related: [[bt-2026-10-22-weekly-swing-six-names-breadth]], [[bt-2026-10-21-weekly-swing-stretch]], [[horizon-strategies]].

## Rule used
`trend_breakout` is the live book (`core/scan_catalog.py`: "Weinstein Stage 2 uptrend plus volume contraction, ranked as a momentum breakout. This is the live book."). It has no function in `core/horizon_strategies.py`; the gates live in `stage_analysis.py`:
- `is_stage_2`: close > SMA50 > SMA150 > SMA200; SMA200 slope up over the last 18+1 days; close >= 0.85 x 52-week high (config: STAGE2_52W_PCT 0.85, STAGE2_SMA_UPWARD_DAYS 18).
- `check_vcp_volume`: each of the last 4 days' volume is below the 50-day average (VCP_DAYS 4, exclude 0).

Offline core: event = the day Stage 2 and VCP first both hold. Entry is at the next open, with the usual costs. Not replicated: breakout buffer, quality gates, ranking, adaptive stops and the 15/40 grace. The harness's own `control_legacy_aug` trades for these names are shown as a reference.
Exits: **STATE** = next open after the first close where Stage 2 fails, capped at 40 sessions (the live time-stop); **T20** = 20-session close. weekly_swing uses its STATE/T20 rules from weekly_swing.py. **BOTH** = weekly_swing trades in a W-FRI week where trend_breakout also fired.
Benchmarks: SPY; CHEF XLP plus peers SYY/USFD/PFGC/UNFI (SPTN had no data); CRM IGV+XLK plus ORCL/ADBE/NOW/WDAY/INTU; SAIC XLK plus CACI/LDOS/BAH/PSN/KBR. Alpha uses a 252-day pre-entry beta to SPY and the sector. Shrinkage toward each strategy's pool: empirical Bayes, kappa floor 10.

## Per ticker (STATE exit; mean excess, trade-bootstrap 95% CI)
| tk | arm | n | raw | win | vs SPY | vs sector | vs peers | alpha | shrunk SPY (w) | flags |
|---|---|---|---|---|---|---|---|---|---|---|
| CHEF | WS | 36 | -2.4% | 19% | -2.7% [-5.2,+0.1] | XLP -1.9% | -2.3% | -2.6% | -1.8% (0.58) | |
| CHEF | TB | 54 | -1.3% | 32% | -1.7% [-4.2,+1.0] | XLP -1.1% | -1.0% | -1.3% | -1.3% (0.75) | |
| CHEF | BOTH | 8 | -4.9% | 13% | -4.6% [-7.1,-2.4] | XLP -3.4% | -5.3% | -4.9% | -1.4% (0.23) | anecdotal |
| CRM | WS | 37 | +1.9% | 38% | +1.1% [-1.1,+3.5] | IGV +0.3% | +0.0% | +0.1% | +0.4% (0.58) | |
| CRM | TB | 72 | +1.8% | 36% | +1.9% [+0.3,+3.7] | IGV +2.1%, XLK +1.7% | +1.5% | +1.8% | +1.5% (0.80) | |
| CRM | BOTH | 6 | +4.9% | 50% | +4.9% [-0.3,+10.8] | IGV +3.4% | +2.3% | +2.7% | +0.5% (0.18) | anecdotal |
| SAIC | WS | 37 | -1.7% | 32% | -2.2% [-5.2,+1.5] | XLK -2.7% | -2.3% | -2.2% | -1.5% (0.58) | |
| SAIC | TB | 72 | +0.2% | 29% | -0.2% [-1.9,+1.6] | XLK -0.7% | -1.2% | -0.0% | -0.2% (0.80) | |
| SAIC | BOTH | 14 | -4.1% | 43% | -4.9% [-9.1,-1.1] | XLK -5.4% | -5.7% | -5.1% | -2.0% (0.34) | insufficient |

T20 gives the same ordering. CRM TB T20 vs SPY is +1.4% [+0.1,+2.9], shrunk +1.2%. CHEF TB T20 vs SPY is -2.5%. SAIC TB T20 vs SPY is +0.3%, vs peers -1.0%.
Harness reference (control_legacy_aug, own exits, next-open): CHEF n=14, vs SPY +0.5%; CRM n=7, -2.8%; SAIC n=6, +1.5%. All are small samples.

Current state, last bar 2026-10-22: CHEF stretch -1.4%, not in Stage 2 (last TB event 10-09). CRM stretch +4.6%, not in Stage 2 (last TB event 2025-02-05); weekly_swing fired the week of 10-16. SAIC stretch +7.6%, Stage 2 and the TB trigger are both on today (event 10-20); weekly_swing is on (event 10-09), so SAIC is in a "both" state now. Its own "both" history is negative (n=14).

## Pooled universe (1,479 names, 2015-10 onward), mean excess per trade, PF on excess
| exit/period | WS only | WS+TB | TB only | TB+WS | TB+WS minus TB only, vs SPY [CI, P>0] |
|---|---|---|---|---|---|
| STATE IS | +0.06% (1.02) | -0.44% (0.86) | -0.23% (0.91) | +0.37% (1.20) | [+0.20,+0.97] 1.00 |
| STATE OOS | -1.05% (0.74) | -0.92% (0.77) | -0.53% (0.83) | +0.38% (1.16) | [+0.35,+1.53] 0.999 |
| T20 IS | +0.19% (1.07) | +0.19% (1.06) | -0.14% (0.95) | +0.88% (1.33) | [+0.47,+1.55] 1.00 |
| T20 OOS | -0.36% (0.90) | -0.23% (0.94) | -0.58% (0.84) | +0.48% (1.14) | [+0.26,+1.82] 0.998 |
The table shows vs SPY. Vs the SPDR sector the picture is the same: TB+WS minus TB-only is positive in all 4 cells (P >= 0.984), and TB+WS sector PF is 1.17-1.36. WS+TB minus WS-only is mixed: STATE IS vs sector is significantly *negative*, and the other cells are inconclusive. TB-only minus WS-only is not reliable in either direction.
Week-block bootstrap, 1000 draws. Overlap: 24% of WS trades and 14% of TB trades share a week with the other signal.

## Tests
Pooled: 3 contrasts x 2 benchmarks x 2 exits x 2 periods = 24 (Bonferroni ~P>0.999 or <0.001). Per ticker: 3 names x 3 arms x 2 exits x 5-6 metrics (descriptive).

## Verdicts
1. **trend_breakout core alone has no pooled edge vs SPY.** PF on excess is 0.83-0.95, worse OOS. The offline core is not the live book: no ranking or quality gates.
2. **A same-week weekly_swing confirmation lifts trend_breakout:** about +0.6 to +1.5pp per trade, positive in 8/8 cells, IS and OOS. 3 of the 4 vs-SPY cells and 2 vs-sector cells clear Bonferroni. PF on excess is 1.14-1.36. This is the one candidate here worth a pre-registered shadow test. It is not a live change: the gain per trade is small relative to costs and dispersion, and it is not tested at portfolio level.
3. **The reverse does not hold.** Adding TB to weekly_swing does not reliably help, so "both" means "TB entries filtered by WS", not "WS entries filtered by TB".
4. **Per ticker:** only CRM TB shows a CI above zero (+1.9% vs SPY, shrunk +1.5%), and that is unadjusted for ~100 per-ticker cells. CHEF is negative across the board. SAIC is flat on TB and negative on WS and BOTH. The per-ticker "both" samples (6-14) are anecdotal and contradict the pooled result for CHEF and SAIC.

## Caveats
Survivorship: uses today's S&P 1500 cache. Pool prices are cached to 2026-09-21; single names were refreshed via yfinance to 2026-10-22. Per-ticker CIs are trade-level and overlapping, so they are too narrow. Pool sector excess uses .sector_map_cache SPDRs. The STATE cap of 40 and the VCP-onset event definition are analogues, not the live exit engine. No portfolio, capacity or tie-break simulation was done.

## Update 2026-10-27
The BOTH-arm edge here enters Mon-Thu trend_breakout signals before the Friday weekly_swing confirmation (lookahead). Lookahead-free splits show no edge. See [[bt-2026-10-27-debate-candidates]].
