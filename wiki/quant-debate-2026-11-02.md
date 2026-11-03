---
source: n/a (quant agent analysis; scan desk midday rescan 2026-11-02 live Schwab entries and ATR14; yfinance adjusted daily OHLC through 2026-11-02; webapp.db portfolio_equity_snapshots 2026-11-02)
created: 2026-11-02
updated: 2026-11-02
tags: [quant, debate, predictions, INSW, KFY, DHR, earnings-rule, multiple-testing]
---

# Quant debate log 2026-11-02

Twelve names from scan desk's midday rescan. Same model as [[quant-debate-2026-10-29]]: entry and ATR14 as given by debate chair (live Schwab), stop = entry - 2 x ATR14, target = entry + 4 x ATR14 (2R), 60-session max hold. p = per-ticker in-sample win rate of setups since 2015 (close > SMA50 > SMA200, non-overlapping, a bar touching both levels = stop, a 60-day timeout scored by sign of P&L), Wilson 95% CI, EV = 3p - 1 R. Kelly = (3p-1)/2. Shares at 1% risk per $100k; $5k cap shares, halved under the earnings rule. No orders.

Equity base: $141,578.35 (2026-11-02 snapshot, down from $201,729.40 on 10-26). Book for correlation: LSAK, LSCC, RRX, JAKK, ATEX, GRPN, INTC, ESTA (stock legs, market-value weighted; INTC 61% of equity). UTL is no longer in the snapshot.

Desk rules: earnings in first 10 sessions = HALF size (INSW, BDX 11-05). RGEN: yfinance shows Q3 reported 10-27, so no earnings in the early window -> full size (next about late Jan, unconfirmed). Small-sample history is not a veto.

## Numbers (ranked by EV)
| # | Ticker | Sig | Entry | ATR (%) | Stop / Target | n (W/L) | p [95% CI] | EV R [95% CI] | Kelly / half | sh @1%/$100k | cap sh (risk $) | Ext vs 50d | corr SPY / book (60d) | % 20d ADV | Holm vs 1/3 / vs .384 | Earnings | Size |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | INSW | WS | 106.05 | 6.04 (5.7%) | 93.97 / 130.21 | 65 (31/34) | 0.477 [0.36-0.60] | +0.43 [+0.08,+0.79] | 0.215 / 0.108 | 82 | 23 ($278) | -1.3% | 0.13 / 0.14 | 0.006% | 0.137 / 0.96 | 11-05 (session 3) | HALF |
| 2 | KFY | TB | 79.16 | 2.25 (2.8%) | 74.66 / 88.16 | 67 (28/39) | 0.418 [0.31-0.54] | +0.25 [-0.08,+0.61] | 0.127 / 0.063 | 222 | 63 ($284) | +3.5% | 0.01 / -0.22 | 0.014% | 1.000 / 1.00 | 12-08 (in hold, after session 10) | full |
| 3 | OKTA | WS | 197.51 | 14.24 (7.2%) | 169.03 / 254.47 | 73 (30/43) | 0.411 [0.31-0.53] | +0.23 [-0.08,+0.58] | 0.116 / 0.058 | 35 | 25 ($712) | +1.8% | 0.35 / 0.33 | 0.001% | 1.000 / 1.00 | 12-01 (in hold, after session 10) | full |
| 4 | DHR | WS | 229.32 | 6.91 (3.0%) | 215.50 / 256.96 | 73 (30/43) | 0.411 [0.31-0.53] | +0.23 [-0.08,+0.58] | 0.116 / 0.058 | 72 | 21 ($290) | +4.8% | 0.30 / 0.18 | 0.000% | 1.000 / 1.00 | 01-27 (in hold, late) | full |
| 5 | STBA | both | 52.21 | 1.15 (2.2%) | 49.91 / 56.81 | 63 (25/38) | 0.397 [0.29-0.52] | +0.19 [-0.14,+0.56] | 0.095 / 0.048 | 434 | 95 ($218) | +5.2% | -0.06 / -0.14 | 0.050% | 1.000 / 1.00 | 01-21 (in hold, late) | full |
| 6 | RGEN | WS | 173.95 | 9.02 (5.2%) | 155.91 / 210.03 | 65 (25/40) | 0.385 [0.28-0.51] | +0.15 [-0.17,+0.52] | 0.077 / 0.038 | 55 | 28 ($505) | -2.2% | 0.30 / 0.34 | 0.002% | 1.000 / 1.00 | reported 10-27 (yf) | full |
| 7 | HWC | TB | 73.90 | 2.00 (2.7%) | 69.90 / 81.90 | 68 (26/42) | 0.382 [0.28-0.50] | +0.15 [-0.17,+0.50] | 0.074 / 0.037 | 250 | 67 ($268) | +0.5% | 0.17 / 0.03 | 0.009% | 1.000 / 1.00 | 01-19 (in hold, late) | full |
| 8 | SEIC | TB | 106.42 | 2.44 (2.3%) | 101.54 / 116.18 | 76 (29/47) | 0.382 [0.28-0.49] | +0.14 [-0.16,+0.48] | 0.072 / 0.036 | 204 | 46 ($224) | +0.5% | 0.46 / 0.16 | 0.006% | 1.000 / 1.00 | 01-27 (in hold, late) | full |
| 9 | FULT | both | 24.16 | 0.52 (2.2%) | 23.12 / 26.24 | 73 (26/47) | 0.356 [0.26-0.47] | +0.07 [-0.23,+0.41] | 0.034 / 0.017 | 961 | 206 ($214) | +5.0% | 0.04 / -0.01 | 0.014% | 1.000 / 1.00 | 01-20 (in hold, late) | full |
| 10 | WOR | both | 65.05 | 1.52 (2.3%) | 62.01 / 71.13 | 64 (22/42) | 0.344 [0.24-0.47] | +0.03 [-0.28,+0.40] | 0.016 / 0.008 | 328 | 76 ($231) | +7.3% | 0.39 / 0.38 | 0.030% | 1.000 / 1.00 | 12-15 (in hold, after session 10) | full |
| 11 | WSBC | TB | 39.65 | 0.83 (2.1%) | 37.99 / 42.97 | 59 (19/40) | 0.322 [0.22-0.45] | -0.03 [-0.35,+0.35] | -0.017 / -0.008 | 602 | 126 ($209) | +2.4% | 0.08 / -0.06 | 0.020% | 1.000 / 1.00 | 01-26 (in hold, late) | full |
| 12 | BDX | WS | 199.12 | 3.74 (1.9%) | 191.64 / 214.08 | 66 (21/45) | 0.318 [0.22-0.44] | -0.05 [-0.34,+0.31] | -0.023 / -0.011 | 133 | 12 ($90) | +6.8% | 0.06 / -0.23 | 0.002% | 1.000 / 1.00 | 11-05 (session 3) | HALF |

Notes: INSW (-1.3%) and RGEN (-2.2%) are BELOW their SMA50 today, so they fail the close > SMA50 setup check at these entries. All 12 have SMA50 > SMA200. Every position at the cap is under 0.06% of 20d ADV. Book correlations all |r| < 0.4 (highest RGEN 0.34, OKTA 0.33).

## Multiple testing (12 names)
- Holm vs break-even 1/3: best INSW raw .011 / Holm .137; everything else Holm 1.0. Nothing below .05.
- Holm vs generic baseline .384: all Holm >= .96.
- Only INSW has a 95% EV lower bound above 0 (+0.08R); with 12 tests that is about what chance gives (expected 0.3+ under drift).

## Caveats
- Backtester: pooled WS-only out-of-sample excess vs SPY is slightly negative (-0.36% T20 [-0.76,+0.06]; -1.05% with the state exit), and the pooled 'both' edge was a lookahead artifact ([[bt-2026-10-27-both-lookahead-free]]). FULT, STBA, WOR 'both' signals get no extra credit.
- p is in-sample per ticker; timeouts scored by sign; earnings dates are yfinance estimates.
- Equity is down about 30% since 10-26; risk $ shown is per the $5k cap, not per equity.

## Verdict input
Lean: NO TRADE. No name survives Holm. If debate chair wants one: 1) INSW at half size (23 sh, ~$278 risk), EV +0.43R [+0.08,+0.79], but it is 1.3% below SMA50 and reports 11-05; 2) KFY full cap 63 sh ($284 risk), EV +0.25R [-0.08,+0.61], ext +3.5%, earnings 12-08, corr to book -0.22; 3) DHR 21 sh ($290 risk), EV +0.23R [-0.08,+0.58], ext +4.8%, earnings 01-27 (end of hold). BDX (EV -0.05R) and WSBC (-0.03R) have negative point EV.

## Predictions (score by about 2027-01-28, 60 sessions)
- INSW: P(hit 130.21 before 93.97) = .48 [.36-.60].
- KFY: P(hit 88.16 before 74.66) = .42 [.31-.54].
- DHR: P(hit 256.96 before 215.50) = .41 [.31-.53].
- Equal-weight 12-name basket: mean R about +0.15 (point), CI includes 0; expect 4-5 of 12 to hit target.

## Rebuttal round

**INSW tanker beta** (yfinance, daily returns through 2026-11-02). The basket is an equal-weight mix of FRO, STNG, TNK, DHT and ASC.
- Beta and correlation to the basket: 1.01 and 0.91 over 60 days, 1.00 and 0.92 over 1 year.
- Beta and correlation to SPY: 0.55 and 0.13 over 60 days, 0.26 and 0.08 over 1 year.
- Residual return vs the basket, using the 1-year beta:
  - 20 days: -8.3% (INSW -9.3% vs basket -1.2%, t = -1.33)
  - 60 days: -6.0% (INSW +20.3% vs basket +28.1%, t = -0.72)
- Conclusion: INSW is mostly tanker beta, and lately it has lagged its peers. The data doesn't show any idiosyncratic edge.

**Signal date.** scan_results has INSW rows only on 10-22 and 11-02, and it has no signal-type field. The DB can't confirm or refute 07-03.

**Bull case (steelman).** The out-of-sample T20 is +4.5 after shrinkage and survives Holm. In-sample EV is +0.43R [+0.08, +0.79], the only CI of the 12 that stays above 0. Half size risks about $140. Q3 is already pre-released, which lowers the gap risk on 11-05.

**Verdict:** INSW does NOT clear the bar.
- It fails Holm in my test (0.137).
- It trades below its 50-day.
- The signal is stale.
- The edge over peers is +1.1 in backtest, and the residual is -8.3% over 20 days and -6.0% over 60 days.

**Equity, 10-26 $201,729.40 to 11-02 $141,578.35 (-$60,151).** The drop is a real account change, not a data artifact.
- All snapshots come from source=schwab for user local, with 15-19 legs.
- Equity is net of margin: implied cash went from -$187,105 to -$153,752.
- Price effect on the quantities held, chained across snapshots: -$48,121.
  - INTC alone is -$39,782. The snapshot price went 92.17 (10-29) to 70.59 (10-29 after hours) to 62.02. yfinance closes: 92.07 on 10-29, 65.76 on 10-30, 61.35 on 11-02.
  - Option legs on AMD, PURR and CMI, plus FSLY, ESTA and LSCC, make up most of the rest.
- Trades and flows residual: -$12,032. This comes from opening and closing AMD and CMI calls, PACB and PURR, plus fees. No withdrawal is visible, but it can't be ruled out without Schwab transactions.
- Sales between 10-30 and 11-02 were used to pay down margin (implied cash +$67k). Shares sold: PACB 5000, ATEX 200, ESTA 300, GRPN 400, LSAK 2400 and UTL 42.
- The orders table has no rows after 10-20, so these trades were made outside the webapp.

