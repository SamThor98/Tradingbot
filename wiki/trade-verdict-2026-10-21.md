---
source: scan desk handoff 2026-10-21 premarket; research analyst bear memos 2026-10-21; quant-debate-2026-10-21; bt-2026-10-21-weekly-swing-stretch; no /raw doc
created: 2026-10-21
updated: 2026-10-21
tags: [trade-verdict, debate, no-trade]
---

# Trade Verdict - 2026-10-21

> Debate chair verdict for Wed Oct 21, 2026: NO TRADE TODAY.

## Verdict

| Field | Value |
|---|---|
| Ticker | none |
| Direction | none |
| Entry / Stop / Target / Size | n/a |
| Conviction (1-5) | n/a (no-trade call itself: 4/5) |

**Strongest reason for (standing aside):** no candidate shows a demonstrable edge. All five are research-only weekly-swing signals from degraded data (no live quotes, Oct 20 closes, partial 100-name universe). Quant's best case, ALL, has EV +0.14R with a 95% CI that includes 0; BIIB's EV is negative. Backtester finds no edge for ALL, BIIB, or the universe, and out of sample (2022 on) the setup trails SPY in every bucket by 0.1 to 1.1 points per trade. Scanner confidence >45 was initially reported as refuted, later withdrawn (see Correction).

**Strongest reason against (the case for trading):** ALL is cheap next to peers (trailing P/E 4.99 vs peer median 8.87), with ROE of 46%, $2.6B left on the buyback, beta 0.14, and -0.23 correlation to SPY; quant's point estimate on it is slightly positive.

**Dissent:** mild. Quant's slightly positive EV on ALL (+0.14R, CI [-0.16, +0.47]), and backtester's BIIB T20 exit beating SPY by 1.27 points per trade on a small sample (24 trades). Neither is distinguishable from zero.

## Candidates considered

| Ticker | Scan confidence | Price (Oct 20 close) | vs 50d | Research bear conviction | Nearest catalyst |
|---|---|---|---|---|---|
| ALL | 88.7 | 249.91 | +1.7% | Medium (peak earnings) | Q3 Nov 4 after close |
| BIIB | 52.1 | 243.10 | +10% | Medium-low (priced for good news) | Q3 Oct 28 before open |
| TECH | 45 | 72.58 | +0.3% | Low (deal spread to $73 cash) | deal close late 2026/early 2027 |
| AIZ | 45 | 283.61 | +2.2% | Low (cat losses risk) | Q3 Nov 3 after close |
| APD | 45 | 302.58 | +3.5% | Low-medium (charges, sales miss) | FQ4 Nov 5 before open |

## Quant (see [[quant-debate-2026-10-21]])

Model: entry at Oct 20 close, stop 2x ATR14, target 2R, max hold 60 days; win rate from setups since 2015 (close > 50d > 200d), Wilson 95% CI; EV = p*2 - (1-p).

- **ALL:** entry 249.91, stop 238.76, target 272.21. p = 0.38 [0.28, 0.49]; EV +0.14R, CI includes 0. Size at 1% risk: 89 sh per $100k (~$22k). Target sits below 52-week high of 277.22.
- **BIIB:** entry 243.10, stop 229.92, target 269.46. p = 0.29 [0.18, 0.45]; EV -0.12R, negative Kelly. Skip.
- **Confidence >45:** (WITHDRAWN 2026-10-21 10:38 AM CT, see Correction) refuted, not a sample-size issue (n = 66k labeled candidates 2015-2026; 40-day forward return -2.0 points, bootstrap CI -2.3 to -1.7). Caveat: in-sample test.
- Account equity not found, so sizes are per $100k.

## Backtester (see [[bt-2026-10-21-weekly-swing-stretch]])

- No edge for ALL, BIIB, or the universe; ALL/BIIB samples (23-40 trades) are inconclusive.
- Stretch above the 50-day makes no reliable difference; the one significant in-sample result vanishes out of sample. Stretched entries take deeper drawdowns.
- Out of sample the setup trails SPY in every bucket.
- **Data flag (resolved, see Correction):** backtester's cached bars (ending 2026-09-21) disagree with scan desk on the 50d distance for ALL and BIIB. Recheck on fresh data.

## Research analyst (bear side)

- **ALL:** peak earnings - $1.5B of auto reserve releases this year, auto net rate changes ~0% in Q1-Q2, $1.1B of reversible equity markup gains, $1.65B Q3 cat losses; forward P/E 8.55 vs 4.99 trailing. Q3 Nov 4.
- **BIIB:** priced for good news - 10% above 50d, near 52-week high, at/above average target; MS revenue -13%, ~$284M acquired R&D charge in Q3. Q3 Oct 28.
- Memos live on the box: `/workspace/memos/research-memo-<ticker>-2026-10-21.md`.

## Correction (2026-10-21 10:38 AM CT)

- **Quant withdrew "conf>45 refuted (n=66k)".** That test used an uncapped, in-sample recomputed score. On the live `setup_confidence` (861 rows, 10 tickers above 45), the >45 minus <=45 gap in 5-day return is +0.88pp [-1.2, +2.6]: inconclusive. Scanner confidence is treated as **neutral**.
- **Backtester update:** the relative-terms rerun on fresh prices found no edge for ALL or BIIB. Stretch above the 50d is not harmful. The earlier 50d mismatch came from backtester's stale 2026-09-21 bars; scan desk's inputs are correct.
- **Verdict unchanged:** NO TRADE, 4/5 conviction in standing aside.

## Scoring (fill in later)

- Outcome check due: 2026-10-28. Compare ALL and BIIB closes to the quant levels:
  - ALL: entry 249.91 / stop 238.76 / target 272.21
  - BIIB: entry 243.10 / stop 229.92 / target 269.46
  - Did either hit stop or target, and was standing aside correct?

## Related Pages

- [[trade-verdict-2026-10-20]] - previous verdict (no trade)
- [[quant-debate-2026-10-21]] - quant levels, EV, and confidence>45 test
- [[bt-2026-10-21-weekly-swing-stretch]] - weekly-swing backtest and stretch analysis
- [[horizon-strategies]] - weekly-swing research evaluator
- [[signal-ranking]] - how candidates were ranked

---

*Last compiled: 2026-10-21*

## Outcome (scored 2026-10-29 by quant, daily OHLC through the 10-28 close)
Window 10-21 to 10-28 close; entry at the 10-20 close; a bar that spans both stop and target counts as a stop (none did).
| Ticker | Entry / Stop / Target | Result | Exit (10-28 close) | Return | R | SPY | Excess |
|---|---|---|---|---|---|---|---|
| ALL | 249.91 / 238.76 / 272.21 | open (high 259.37, low 247.89) | 250.20 | +0.12% | +0.03 | +0.42% | -0.31pp |
| BIIB | 243.10 / 229.92 / 269.46 | open (high 258.78, low 240.02; Q3 10-28 gapped -3.1% at the open, no stop) | 244.05 | +0.39% | +0.07 | +0.42% | -0.03pp |
Hypothetical equal-risk book: +0.10R, both trailing SPY. No-trade was right (flat result, no edge vs SPY). Current status: both still open; ALL reports Q3 11-04, which is inside the hold.
