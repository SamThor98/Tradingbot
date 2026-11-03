---
source: scan desk handoff 2026-10-22 premarket; research analyst memos 2026-10-22; quant-debate-2026-10-22; bt-2026-10-22-weekly-swing-six-names-breadth; no /raw doc
created: 2026-10-22
updated: 2026-10-22
tags: [trade-verdict, debate, no-trade]
---

# Trade Verdict - 2026-10-22

> Debate chair verdict for Thu Oct 22, 2026: NO TRADE TODAY.

## Verdict

| Field | Value |
|---|---|
| Ticker | none |
| Direction | none |
| Entry / Stop / Target / Size | n/a |
| Conviction (1-5) | n/a (no-trade call itself: 3/5) |

**Strongest reason for (the case for trading):** WELL. Quant has EV +0.44R with a 95% CI of [+0.11, +0.78] that excludes 0 (p = 0.48), and backtester's T20 exit beat XLRE by 2.37% per trade.

**Strongest reason against:** UNP has no edge: EV +0.07R with a CI including 0, -0.93% vs SPY over 47 trades, and today's low already breached the stop. WELL's earnings on Oct 26 are a binary event inside the hold, and its XLRE result is 1 hit in ~72 tests, so it may be a multiple-testing artifact.

**Dissent:** research analyst on UNP (best long, medium-high conviction, after a record Q3).

## Candidates considered

Live, clean data, but Stage 2 was skipped, so the 221-signal count is inflated.

| Ticker | Notes |
|---|---|
| UNP | research best long (medium-high); quant EV +0.07R; backtester -0.93% vs SPY |
| SXT | no specific case made |
| WELL | quant EV +0.44R; earnings Oct 26 |
| SAFT | cash-deal spread |
| TECH | cash-deal spread (Merck KGaA $73) |
| ITGR | cash-deal spread |
| SLAB | cash-deal spread |

## Research analyst (bull side)

- **UNP:** best long, medium-high conviction. Record Q3: EPS $3.49 (+16%), revenue +15%.
- **ITGR, SAFT, SLAB:** cash-deal spreads, so upside is capped.

## Quant (bear side; see [[quant-debate-2026-10-22]])

- **UNP:** entry 287.87, stop 279.12, target 305.38. p = 0.36; EV +0.07R [-0.24, +0.43]. Today's low of 278.59 breached the stop. Earnings-day moves fade (rho -0.31). Correlation 0.45 to SNA (held).
- **WELL:** entry 234.19, stop 224.17, target 254.23. p = 0.48; EV +0.44R [+0.11, +0.78]. Size: 99 sh per $100k. Earnings Oct 26.

## Backtester (see [[bt-2026-10-22-weekly-swing-six-names-breadth]])

- **UNP:** -0.93% vs SPY per trade over 47 trades.
- **WELL:** +0.13%; the T20 exit beat XLRE by 2.37%, but that is 1 hit in ~72 tests.
- Entries near the 50d have no edge.
- Breadth is not a useful filter.

## Round 2 (corrected scan, 9:17 AM CT)

The scan was rerun with trend_breakout plus weekly_swing; Stage 2 had been skipped because only weekly_swing was passed. Result: 37 signals. Candidates: CHEF, CRM, SAIC.

**Verdict: still NO TRADE, 3/5.**

### Quant (bull side)

- **SAIC:** entry 139.05, stop 131.46, target 154.22. p = 0.41; EV +0.24R [-0.15, +0.67]. Size: 131 sh per $100k.
- **CRM:** EV +0.25R, but its 200d is falling and it is +33% in 60 days.
- **CHEF:** pass.

### Research analyst (bear side)

- **CRM:** $5.66B Braze deal; organic growth about 7-8%.
- **CHEF:** $430M more floating-rate debt; Q3 Oct 28.
- **SAIC:** CFO handover Dec 7, around Q3; weak organic growth.

### Backtester (see [[bt-2026-10-22-trend-breakout-vs-weekly-swing]])

- The 'both signals' combo (trend_breakout + weekly_swing) is +0.38% vs SPY (PF 1.16): a shadow-test candidate.
- SAIC's own 14 'both' trades: -4.9% [-9.1, -1.1].
- CHEF: negative.
- CRM trend_breakout: +1.5%.

**Dissent:** quant on SAIC.

**Action:** scan desk was asked to flag 'both' names as a shadow strategy.

## Related Pages

- [[trade-verdict-2026-10-21]] - previous verdict (no trade)
- [[quant-debate-2026-10-22]] - quant levels, EV, and sizing
- [[bt-2026-10-22-weekly-swing-six-names-breadth]] - weekly-swing backtest and breadth test
- [[bt-2026-10-22-trend-breakout-vs-weekly-swing]] - round 2: trend_breakout vs weekly_swing and the 'both' combo
- [[horizon-strategies]] - weekly-swing research evaluator
- [[signal-ranking]] - how candidates were ranked

---

*Last compiled: 2026-10-22*

## Outcome (scored 2026-10-29 by quant, daily OHLC through the 10-28 close)
A bar that spans both stop and target counts as a stop. Exit at the stop, or at the open if the price gapped through it.
| Ticker | Entry / Stop / Target | Result | Exit | Return | R | SPY | Excess |
|---|---|---|---|---|---|---|---|
| UNP (from the 10-22 bar) | 287.87 / 279.12 / 305.38 | STOP 10-22 (low 278.59; open 280.10 was above the stop, so fill at 279.12) | 279.12 | -3.04% | -1.00 | -1.18% | -1.86pp |
| UNP (from the 10-23 bar) | same | open (high 302.60, low 288.61) | 297.80 | +3.45% | +1.13 | -0.10% | +3.55pp |
| WELL | 234.19 / 224.17 / 254.23 | STOP 10-28, gapped through after the 10-26 Q3 report (10-27 -5.8%; 10-28 open 222.81) | 222.81 | -4.86% | -1.14 | -0.21% | -4.64pp |
| SAIC (round 2, entry intraday 10-22) | 139.05 / 131.46 / 154.22 | open (high 150.79, 0.45R short of the target) | 148.93 | +7.11% | +1.30 | +0.78% | +6.33pp |
SPY: from the 10-21 close (UNP, WELL) or the 10-22 open (SAIC) to the exit. Hypothetical equal-risk book (UNP + WELL + SAIC): -0.83R if UNP counts from the 10-22 bar, +1.30R if it counts from 10-23. Round 1 only (UNP + WELL): -2.14R or 0.00R.
No-trade was right on the realistic count (UNP's day-1 low pierced the stop; WELL lost to the earnings gap flagged in the verdict) and wrong only under the generous UNP count. The dissent (quant on SAIC) was the one winner; the "case for" (WELL, CI above 0) was the biggest loser.
