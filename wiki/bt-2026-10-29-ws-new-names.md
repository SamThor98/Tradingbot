---
title: "Backtest 2026-10-29: new weekly_swing names HRB, FIZZ, DFIN"
date: 2026-10-29
type: backtest
tags: [backtest, weekly_swing, debate]
---
# New WS names 2026-10-29 (HRB, FIZZ, DFIN)

Script `experiments/backtester/debate_2026_10_29.py` (copy of [[bt-2026-10-28-ws-only-candidates]] method); results `results_debate_2026-10-29.json`. Prices through 2026-10-28 close (partial bar dropped). Entry next open after Friday WS confirmation, 0.34% RT, IS 2015-2021 / OOS 2022-on, shrunk toward WS pool (OOS STATE -1.02%, T20 -0.33%).

| Name | n IS/OOS | OOS vs SPY STATE raw->shrunk | T20 raw->shrunk | OOS beat SPY (STATE/T20) | OOS vs sector STATE shrunk | Fresh WS 10-23? | vs 50d / 10wk |
|---|---|---|---|---|---|---|---|
| HRB | 12/11 | +4.75 -> +1.73% | +2.08 -> +0.69% | 4/11, 7/11 | +3.4% (XLY) | yes, plus TB 10-22 (clean both) | +2.3% / +1.7% |
| DFIN | 8/12 | -1.07 -> -1.04% | -1.52 -> -0.86% | 3/12, 5/12 | -1.5% (XLK, wrong) | no, state-only from 10-16 | +7.5% / +7.4% |
| FIZZ | 8/7 | -6.58 -> -3.05% | -4.93 -> -1.80% | 0/7, 2/7 | -2.2% (XLP) | yes | +6.7% / +6.5% |

- All CIs include zero except FIZZ OOS STATE (significantly negative, 0 of 7). Every name flips sign IS vs OOS. n under 15 everywhere.
- HRB is the only positive own history, driven by a few big STATE trades (hit 36%). Its "both" signal is not validated (pooled both = -0.23%, see lookahead correction).
- Sector map: HRB XLY and FIZZ XLP match GICS. DFIN (Donnelley Financial) mapped XLK; GICS is not tech (Financials/Industrials), so vs-sector is unreliable.
- DFIN 10-28 close 53.12 (-0.6% day), +7.5% over 50d. Reported +8.2% premarket would put it near +16%; not verifiable from daily bars.
- Caveats: survivorship (delisted names missing), single-trade not portfolio, small n, 3-name family (Bonferroni alpha 0.0167).