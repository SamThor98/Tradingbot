---
source: experiments/backtester/results_debate_2026-10-22.json
created: 2026-10-22
updated: 2026-10-22
tags: [backtest, research, weekly, relative-performance]
---

# Weekly swing: six tickers, near-50d entries, breadth (2026-10-22)

> Same method as [[bt-2026-10-21-weekly-swing-stretch]] (relative rerun). Of six names, only WELL shows a positive relative history; the others are at or below the pool. Entries within 1% of the 50-day SMA are no better, and slightly worse in sample. Breadth of 200+ triggered names is the norm (median 516) and does not separate outcomes.

## Setup

- `weekly_swing` rule, event definition, STATE / T20 exits, 0.34% round trip, all as in `weekly_swing_relative.py`.
- Fresh read-only yfinance prices. The 2026-10-22 bars are intraday.
- Benchmarks:
  - UNP: IYT and XLI; peers CSX, NSC, CP, CNI, ODFL
  - SXT: XLB; peers IFF, ASH, CBT, NEU, AVNT
  - WELL: XLRE (starts 2015-10); peers VTR, DOC, OHI, SBRA, CTRE
  - SAFT: KIE and XLF; peers ALL, PGR, SIGI, HMN, KMPR
  - ITGR: IHI and XLV; peers TFX, MMSI, ICUI, GMED, PEN
  - SLAB: SOXX and XLK; peers SWKS, QRVO, SYNA, LSCC, MCHP (4 with data)
- Alpha uses the first ETF in each list.
- Shrinkage target is the universe pool: 80,133 rows, 1,473 tickers, signals 2015-10 to 2026-09, SP1500-now, today's sector map. Kappa is floored at 10.
- Breadth = the number of cached tickers with the weekly trigger ON in the signal week.
- Script: `experiments/backtester/debate_2026_10_22.py`. The pool is saved to `pool_weekly_swing_2026-10-22.parquet`.
- **Tests:** about 72 per-ticker metric CIs, 16 stretch contrasts, 8 breadth contrasts and 4 quintile curves, roughly 100 in all, with no correction applied. At 95%, expect about 5 false positives.

## 1. Per ticker (STATE exit; T20 in brackets)

| Ticker | Stretch now | n | Raw mean (win) | x SPY [95% CI] | x industry ETF | x SPDR | x peers | Alpha | Shrunk x SPY (w; pool -0.47%) |
|---|---:|---:|---|---|---|---|---|---|---|
| UNP | **+2.89%** | 47 | -0.29% (36%) [T20 -0.31%] | **-1.19% [-2.3, -0.0]** [T20 -0.41%] | IYT +0.10% | XLI -0.28% | -1.06% [-2.2, +0.1] | +0.28% | -0.93% (0.64) |
| SXT | 0.00% | 41 | -0.54% (27%) [T20 -0.88%] | **-1.49% [-2.7, -0.3]** [T20 -0.88%] | - | XLB -0.21% | -0.70% | -0.72% | -1.09% (0.61) |
| WELL | +0.61% | 39 | **+2.37% (49%)** [T20 +2.77%] | +0.54% [-1.8, +2.9] [T20 +1.45% [-0.5, +3.5]] | - | XLRE +1.98% [-0.1, +4.1] (n 34) [T20 **+2.37% [+0.9, +3.9]**] | +0.69% [T20 +1.44%] | +1.92% [T20 **+2.40% [+0.9, +3.9]**] | +0.13% (0.59) [T20 +1.15%] |
| SAFT | +0.63% | 36 | -0.38% (31%) [T20 +0.34%] | -1.21% [-2.8, +0.5] [T20 -0.59%] | KIE -0.47% | XLF -0.54% | -1.10% [-2.3, +0.1] | -0.64% | -0.90% (0.58) |
| ITGR | +0.50% | 38 | -0.04% (34%) [T20 +1.14%] | -0.01% [-2.8, +3.0] [T20 +0.92%] | IHI +0.05% | XLV +0.13% | +0.70% | -0.01% | -0.20% (0.59) |
| SLAB | +0.63% (10-21 bar) | 38 | +0.76% (34%) [T20 +1.04%] | +0.30% [-2.5, +3.7] [T20 +0.68%] | SOXX -0.85% | XLK -0.07% | -0.27% | -0.87% | -0.02% (0.59) |

All six had the trigger ON in the last completed week (2026-10-16). **UNP is at +2.9%, not within 0.6% of its 50d.** The other five are between 0.0% and +0.63%. No name has n below 30, but every n is 36-47 over 13 years, which is thin. Worst MAE per trade ranges from -7.7% (WELL) to -26% (UNP T20).

## 2. Entries less than 1% above the 50d SMA (pooled)

| Exit | Slice | n (<1% / >=1%) | <1% x SPY | >=1% x SPY | Diff [95% CI] (P>0) | <1% x SPDR | >=1% x SPDR | Diff [95% CI] (P>0) |
|---|---|---|---:|---:|---|---:|---:|---|
| STATE | IS | 3,938 / 18,544 | -0.41% | +0.01% | [-0.85, +0.02] (0.03) | -0.33% | +0.28% | **[-1.01, -0.24] (0.00)** |
| STATE | OOS | 2,441 / 13,875 | -1.00% | -1.02% | [-0.60, +0.69] (0.53) | -0.35% | -0.16% | [-0.74, +0.39] (0.24) |
| T20 | IS | 3,938 / 18,544 | -0.04% | +0.26% | [-0.92, +0.27] (0.17) | -0.14% | +0.26% | [-0.87, +0.07] (0.06) |
| T20 | OOS | 2,426 / 13,943 | -0.77% | -0.26% | [-1.14, +0.16] (0.07) | -0.29% | +0.13% | [-0.97, +0.11] (0.06) |

The differences are week-clustered bootstraps. Against the pooled average, the <1% bucket is lower in all 8 cells, significantly so in 2 (STATE IS vs SPDR, and vs SPY at P 0.05). **Near-50d entries are not better, and lean slightly worse.**

## 3. Breadth: 200 or more names triggered vs fewer (pooled)

The breadth distribution per pooled signal has a median of 516 names, an IQR of 374-612, and a minimum of 13. About 92-94% of signals fall on 200+ weeks, so the <200 arm is small (1,299-1,338 rows over few weeks).

| Exit | Slice | x SPY: 200+ vs <200, diff [95% CI] | x SPDR: diff [95% CI] (P>0) |
|---|---|---|---|
| STATE | IS | -0.10% vs +0.46%: [-1.83, +0.72] | [-1.82, +0.27] (0.08) |
| STATE | OOS | -0.97% vs -1.57%: [-0.61, +1.78] | [-0.09, +1.57] (0.96) |
| T20 | IS | +0.20% vs -0.03%: [-1.23, +2.02] | [-1.02, +0.91] (0.44) |
| T20 | OOS | -0.35% vs -0.14%: [-1.59, +1.19] | [-0.43, +1.38] (0.82) |

Breadth-quintile curve, mean x SPY by quintile from low to high breadth:

- STATE IS: +0.52, +0.40, -0.35, -0.77, -0.15%
- STATE OOS: -0.90, -0.54, -1.10, -1.26, -1.28%
- T20 IS: +0.25, +0.58, -0.25, -0.63, +0.99%
- T20 OOS: -0.24, -0.10, +0.35, -0.67, -1.04%

Not monotonic. STATE leans slightly worse at high breadth, which is the opposite of the "200+ is a tailwind" idea.

## Verdicts

- **Per ticker:** WELL is the only name with a consistently positive relative history (T20 vs XLRE +2.4%, alpha +2.4%, CIs above zero; vs SPY not significant). With about 72 tests, one name clearing is close to chance, so treat it as weak. UNP and SXT trail SPY significantly under STATE. SAFT, ITGR and SLAB are null. After shrinkage, all sit within +/-1.2% per trade of the pool.
- **Near-50d (<1%) entries:** no advantage, and slightly worse in sample. Not a reason to prefer these names.
- **Breadth 200+:** not informative. It is the default state of this universe, there is no significant difference out of sample, and the quintile curve is flat to slightly negative at high breadth.

## Caveats

- Survivorship: the pool and breadth use today's SP1500 names, so breadth counts are inflated relative to a point-in-time universe, and a desk "200+" count on a different universe is not comparable.
- Peers and ETFs are today's choices, not point-in-time. XLRE before 2015-10 is missing, so WELL has n 34 for the sector metrics.
- The 2026-10-22 bars are intraday (SLAB's latest is 10-21), so current stretches will move.
- Small single-ticker n means the CIs are wide. About 100 tests were run with no multiple-testing correction.

## Related Pages

- [[bt-2026-10-21-weekly-swing-stretch]] - rule, exits and the first relative rerun
- [[horizon-strategies]] - `weekly_swing` definition
- [[bt-2026-10-20-exits-holding-period]] - entries lag SPY at 5-120 days
- [[scan-confidence]] - extension vs early stop-outs on the daily book
- [[backtest]] - cost model

---

*Last compiled: 2026-10-22*
