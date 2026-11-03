---
source: experiments/backtester/debate_2026_11_02.py
created: 2026-11-02
updated: 2026-11-02
title: "Backtest 2026-11-02: debate candidates (WS, TB, lookahead-free both; bank cluster)"
date: 2026-11-02
type: backtest
tags: [backtest, weekly_swing, trend_breakout, debate, correlation]
---
# Debate candidates 2026-11-02

Script `experiments/backtester/debate_2026_11_02.py`; results `results_debate_2026-11-02.json`. Prices through 2026-10-30 close (today's partial bar dropped). Method as [[bt-2026-10-29-ws-new-names]]. "Both" here is lookahead-free per [[bt-2026-10-27-both-lookahead-free]]: a WS Friday event with a TB event in the same week, entered at the next open after that Friday. TB = TB events in weeks with no WS event. Costs 0.34% RT, IS 2015-2021 / OOS 2022 on, EB shrink toward the setup pool (kappa >= 10). Peer baskets are hand-picked current names, and peer excess is shrunk toward 0. Holm correction across 12 names x 2 exits (m=24).

Pool OOS mean excess vs SPY: WS -1.02 STATE / -0.33 T20; TB_only -0.53 / -0.58; both_lf -0.92 / -0.23.

| Name | Setup | n OOS | STATE vs SPY raw->shrunk | T20 raw->shrunk | T20 95% CI | T20 vs peers shrunk | Verdict |
|---|---|---|---|---|---|---|---|
| INSW | WS | 13/14 | +9.8 -> +4.6 | +9.7 -> +4.5 | [+4.4, +15.1] | +1.1 (tankers) | beats pool after Holm (T20, p=0.007), but tanker beta; IS -11 (n=7); stale WS event (07-03) |
| RGEN | WS | 4 | -2.8 -> -1.5 | -0.1 -> -0.3 | [-10.9, +8.6] | +0.6 | noise, n=4 |
| OKTA | WS | 9 | -3.7 -> -2.2 | +2.0 -> +0.6 | [-5.8, +10.0] | -0.3 | no edge; IS/OOS flip |
| DHR | WS | 5 | -5.7 -> -2.4 | -1.8 -> -0.7 | [-7.6, +2.4] | +0.4 | STATE 0/5 beat SPY, Holm-significant negative; veto-lean |
| BDX | WS | 7 | +0.9 -> -0.3 | +1.3 -> +0.2 | [-2.7, +5.2] | +1.7 | pool-like |
| HWC | TB | 29 | -1.6 -> -1.3 | -2.8 -> -2.2 | [-5.4, -0.1] | +0.2 | negative vs SPY, flat vs banks |
| SEIC | TB | 21 | -1.5 -> -1.2 | -1.2 -> -1.0 | [-2.8, +0.5] | -0.2 | negative (STATE CI excludes 0, not after Holm) |
| WSBC | TB | 17 | -0.4 -> -0.5 | -0.9 -> -0.8 | [-4.2, +3.1] | +0.3 | pool-like |
| KFY | TB | 22 | -1.9 -> -1.5 | -1.7 -> -1.3 | [-4.3, +1.0] | +0.7 | negative; IS/OOS flip |
| FULT | both_lf | 4 | +1.6 -> -0.3 | +3.4 -> +0.8 | [-0.4, +9.0] | +0.2 | n=4, nothing |
| STBA | both_lf | 2 | +8.1 -> +0.3 | +15.4 -> +2.4 | n/a | +0.9 | n=2, anecdotal |
| WOR | both_lf | 0 | pool -0.9 | pool -0.2 | n/a | n/a | no OOS history (IS n=4) |

Pool portfolio, OOS 2022-01-03..2026-10-30 (T20, equal weight across all open positions, not capital-constrained):

| Arm | CAGR | Sharpe | Max DD | Win rate | Trades | Exposure | SPY CAGR/Sharpe/DD |
|---|---|---|---|---|---|---|---|
| WS | +4.4% | 0.34 | -24.0% | 50.0% | 16,939 | 97% | +11.6% / 0.72 / -24.5% |
| TB_only | -0.3% | 0.06 | -28.4% | 48.4% | 25,116 | 98% | same |
| both_lf | +4.5% | 0.33 | -28.5% | 50.0% | 4,008 | 97% | same |

## Bank cluster (HWC, WSBC, FULT, STBA)
- Daily return correlation averages 0.82 since 2015 and 0.81 over the last year (pairs 0.76-0.88). Correlation with KRE is 0.85-0.94.
- Effective independent bets: 1.2 of 4 (eigenvalue and 1+(N-1)rho methods). Net of KRE, the residuals are nearly independent (avg rho 0.05 over 1y), so the shared factor is the regional-bank factor.
- Trade-week T20 excess vs SPY correlation: 0.70-0.90 on 19-33 common weeks. 80-94% of each bank's event weeks have another bank firing within a week.
- Verdict: treat the four as one bet. Cap the cluster at one name-equivalent (for example 1.25x a single position total, or max 2 names at half size). This complements the sector cap, which keys on XLF and would also catch SEIC.

## Sector map ([[sector-map-mismatches]], PR #35 unmerged)
All 12 names look right: INSW XLE, RGEN/DHR/BDX XLV, OKTA XLK, banks + SEIC XLF, KFY XLI, WOR XLI. KFY and WOR in XLI match GICS Industrials. XLF is a loose benchmark for regional banks; use KRE or bank peers.

## Flags
- **Lookahead:** none. Both entries come after the Friday WS. FULT and STBA had a WS event in the week of 10-23 and TB events on 10-29/10-30, so they are WS-then-TB, not same-week both. Same-week both is not validated anyway. For HWC, SEIC, and KFY the repo rule shows no TB event since September and no stage 2 today, so the live TB label is not reproduced here.
- **Survivorship:** pools and peer baskets use current names only. Failed banks (2023) are missing. Peer downloads failed for CADE, HSII, and ASGN, which leaves smaller baskets.
- **Overfitting:** INSW is the only name to survive correction. It sits on the 2022-26 tanker cycle: only +1.1 vs tanker peers and -11 IS. The edge is the industry, not the setup.
- **Small n:** every WS and both name has OOS n <= 14; RGEN, DHR, FULT, STBA, and WOR are <= 5.
- **Costs:** 0.34% RT, with no slippage beyond that. Small-cap banks (STBA, WSBC) spread wider.