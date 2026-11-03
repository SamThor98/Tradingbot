---
source: n/a (quant agent analysis; yfinance OHLC and 1-minute quotes 2026-10-28 about 9:53 ET; schwab_skill/webapp/webapp.db scan f488db0e6c (2026-10-28 13:50 UTC), portfolio_equity_snapshots 2026-10-26, pending_trades; experiments/backtester pool parquets and results json, read-only)
created: 2026-10-28
updated: 2026-10-28
tags: [quant, debate, predictions, CTAS, ELF, weekly-swing, multiple-testing, bear-case, scoring]
---

# Quant debate log 2026-10-28

Twelve weekly_swing-only names for debate chair (none executable: entry_family horizon, trend_breakout not triggered, WS triggered, sec_risk high on all). Same model as [[quant-debate-2026-10-27]]: entry = LIVE price (scan prices are 10-26/10-27 closes or partial bars), stop = entry - 2 x ATR14 (Wilder, completed bars through 10-27), target = entry + 4 x ATR (2R), 60-day max hold, history 2015-10 on, setup close > SMA50 > SMA200 (adjusted), non-overlapping trades, same-bar stop+target counts as a stop, win rate among resolved trades with Wilson 95% CI. EV = 3p - 1 (R). Kelly = p - (1-p)/2. Break-even p = 1/3. Shares = 1% of equity / (entry - stop). No orders placed or staged.

## Data checks
- Equity base is the 10-26 snapshot, $201,729.40 (no 10-27 or 10-28 snapshot exists). Book is about 1.9x gross, INTC about 62% of equity. UTL holds 42 sh; pending trade 9f0ad8c3 (UTL 90 sh @ 55.06) is still pending.
- Scan prices: CRM 240.89, TTEK 36.77, ADUS 121.83, PGNY 27.96, WDAY 203.44, BDX 195.03, CTAS 214.80, CACI 659.25. Live is 0.2% to 1.7% different (CRM +1.4%, WDAY +1.7%); several scan rows (WDAY, ELF, TTEK, ADUS, CACI, SONO, PGNY) were built on near-empty partial bars (volume ratio 0.005-0.04). SONO, USPH, DHR, ELF had no scan price.
- All 12 pass close > SMA50 > SMA200 at the live price. ELF (+9.5% above SMA50) and DHR (+9.4%) are above the scan-desk 8% extension cut ([[scan-quality-2026-10-28]]).
- Earnings dates are yfinance estimates, unconfirmed.
- Sector map lacks SPTN (irrelevant here).

## Numbers (ranked by EV, live prices about 9:53 ET)
| # | Ticker | Live | ATR14 (%) | Stop / Target | n (W/L) | Win p [95% CI] | EV R [95% CI] | Kelly | Sh per $100k @1% risk | Sh under $5k cap (risk % equity) | Next earnings |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | ELF | 114.49 | 3.85 (3.4%) | 106.79 / 129.90 | 57 (28/29) | .491 [.37-.62] | +0.47 [+0.10,+0.85] | .237 | 129 | 43 (0.16%) | 11-04 (7d) |
| 2 | SONO | 17.78 | 0.57 (3.2%) | 16.65 / 20.05 | 27 (13/14) | .481 [.31-.66] | +0.44 [-0.08,+0.98] | .222 | 881 | 281 (0.16%) | 11-10 (13d) |
| 3 | CTAS | 214.40 | 4.88 (2.3%) | 204.64 / 233.92 | 87 (41/46) | .471 [.37-.58] | +0.41 [+0.11,+0.73] | .207 | 102 | 23 (0.11%) | 12-17 |
| 4 | TTEK | 36.95 | 0.85 (2.3%) | 35.26 / 40.34 | 74 (34/40) | .459 [.35-.57] | +0.38 [+0.05,+0.72] | .189 | 590 | 135 (0.11%) | 11-11 (14d) |
| 5 | DHR | 237.77 | 6.53 (2.7%) | 224.71 / 263.90 | 59 (26/33) | .441 [.32-.57] | +0.32 [-0.04,+0.70] | .161 | 76 | 21 (0.14%) | 2027-01-27 |
| 6 | CACI | 661.65 | 21.38 (3.2%) | 618.89 / 747.15 | 71 (30/41) | .423 [.31-.54] | +0.27 [-0.06,+0.62] | .134 | 23 | 7 (0.15%) | 2027-01-20 |
| 7 | CRM | 244.30 | 8.61 (3.5%) | 227.08 / 278.74 | 73 (30/43) | .411 [.31-.53] | +0.23 [-0.08,+0.58] | .116 | 58 | 20 (0.17%) | 12-02 |
| 8 | USPH | 87.90 | 2.74 (3.1%) | 82.42 / 98.86 | 55 (22/33) | .400 [.28-.53] | +0.20 [-0.16,+0.60] | .100 | 182 | 56 (0.15%) | 11-04 (7d) |
| 9 | WDAY | 206.80 | 6.98 (3.4%) | 192.85 / 234.71 | 65 (25/40) | .385 [.28-.51] | +0.15 [-0.17,+0.52] | .077 | 71 | 24 (0.17%) | 12-01 |
| 10 | ADUS | 122.46 | 2.74 (2.2%) | 116.98 / 133.42 | 50 (18/32) | .360 [.24-.50] | +0.08 [-0.28,+0.50] | .040 | 182 | 40 (0.11%) | 11-03 (6d) |
| 11 | PGNY | 28.30 | 0.87 (3.1%) | 26.55 / 31.79 | 26 (9/17) | .346 [.19-.54] | +0.04 [-0.42,+0.61] | .019 | 572 | 176 (0.15%) | 11-04 (7d) |
| 12 | BDX | 196.64 | 3.98 (2.0%) | 188.67 / 212.58 | 60 (17/43) | .283 [.19-.41] | -0.15 [-0.44,+0.22] | -.075 | 125 | 25 (0.10%) | 11-05 (8d) |

Sizing note: 1% risk at equity $201.7k is 2x the per-$100k share count (CTAS 206 sh = $44.2k = 21.9% of equity), so the $5,000 per-ticker cap binds on every name. Under the cap the risk is only 0.10-0.17% of equity per name.
Earnings inside 2 weeks: ADUS 11-03, PGNY 11-04, ELF 11-04, USPH 11-04, BDX 11-05 (6-8 days); SONO 11-10 and TTEK 11-11 are 13-14 days out. Average earnings-window moves in history: PGNY 13.7%, ELF 13.5%, USPH 8.2%, SONO 7.9%, WDAY 7.6%, CRM 7.4%, CACI 6.8%, TTEK 6.4%, BDX 6.0%, ADUS 5.9%, DHR 5.9%, CTAS 4.9%. Within the 60-day hold nearly all names have earnings (CTAS 12-17, CRM 12-02, WDAY 12-01 inside; DHR 1-27 outside; CACI 1-20 borderline).

## Context, liquidity, book correlation
| Ticker | Ext vs SMA50 | From 52wH | 20d ADV $M | Position % ADV (equity-size / $5k cap) | Corr SPY 60d/1y | Corr book 60d/1y | Beta |
|---|---|---|---|---|---|---|---|
| ELF | +9.5% | -13.5% | 135.0 | 0.02% / 0.004% | .22/.32 | .04/.20 | 1.02 |
| SONO | +7.4% | -10.3% | 29.0 | 0.11% / 0.017% | .41/.28 | .23/.11 | 1.78 |
| CTAS | +5.3% | -5.4% | 395.0 | 0.01% / 0.001% | -.03/.05 | -.18/-.14 | -0.06 |
| TTEK | +4.7% | -14.3% | 66.4 | 0.07% / 0.008% | .15/.10 | -.09/0.00 | 0.38 |
| DHR | +9.4% | -2.1% | 1063.2 | ~0% | .27/.18 | .07/.04 | 0.86 |
| CACI | +6.1% | -3.2% | 163.4 | 0.02% / 0.003% | .05/.07 | -.10/-.10 | 0.29 |
| CRM | +3.1% | -9.2% | 1770.8 | ~0% | .16/.10 | -.04/-.11 | 0.98 |
| USPH | +6.7% | -5.7% | 14.0 | 0.23% / 0.035% | .13/.19 | -.04/.09 | 0.33 |
| WDAY | +6.7% | -15.3% | 421.8 | 0.01% / 0.001% | .25/.06 | -.06/-.18 | 1.49 |
| ADUS | +4.9% | -1.6% | 21.8 | 0.21% / 0.022% | -.19/.08 | -.32/-.01 | -0.42 |
| PGNY | +5.9% | -14.4% | 27.7 | 0.12% / 0.018% | -.09/.07 | -.06/.07 | -0.36 |
| BDX | +5.8% | -1.9% | 280.5 | 0.02% / 0.002% | .05/.14 | -.34/-.11 | 0.12 |

Liquidity is a non-issue (largest position is 0.23% of ADV, USPH). Correlation is against the 10-26 book (LSAK, LSCC, RRX, JAKK, ATEX, GRPN, INTC, ESTA, UTL): all low; largest pair is SONO-GRPN +0.41. CTAS and ADUS diversify the INTC-heavy book most (negative). Scan fields (all 12): setup_confidence 45.0 (ELF 42.5) with risk_capped, i.e. no information. p_up_calibrated: CTAS .319 (highest), BDX .280, DHR .245, CRM/ADUS .220, USPH .216, others .165-.187. MiroFish: CTAS +33 Strong Continuation, BDX +30, CRM +26, DHR +22; CACI and SONO -8 Bull Trap.

Conditioning buckets (in-sample): breakout bucket EV ELF +0.88 (CI low +0.36, n=32), CTAS +0.45 (low +0.10), TTEK +0.36, CRM +0.25, SONO -0.35. Near-52wH: CTAS +0.38. Within 3% above SMA50: ELF +0.75, SONO +0.71, CTAS +0.68, WDAY +0.54.

## Multiple testing (12 names tested)
- Null for a break-even trade is p = 1/3 (EV 0 at 2R). Exact one-sided binomial p-values vs 1/3, Bonferroni and Holm adjusted (m = 12):
  - CTAS raw .0052, Bonferroni .063, Holm .063
  - ELF raw .0098, Bonferroni .117, Holm .107
  - TTEK raw .0162, Bonferroni .195, Holm .162
  - DHR raw .056 (Holm .50), CACI .073 (.58), SONO .079 (.58), CRM .101 (.61), the rest above .18 (Holm .91 to 1.0).
  - Nothing survives at 0.05 after correction. Best (CTAS) is borderline at 0.063.
- Adjusted CI for the best name (CTAS), Wilson at alpha 0.05/12 (99.6%): p [.327, .620], EV [-0.02R, +0.86R]. It includes 0. ELF: p [.315,.670], EV [-0.06,+1.01]. TTEK: EV [-0.08,+0.86].
- Expected number of names with a 95% lower bound above 0 by chance (exact, true p = 1/3, using the actual n): 0.32 (P at least 1 = 28%). Observed: 3 (ELF, CTAS, TTEK). That excess looks like signal but is mostly the market drift: the same setup and exits on 53 large caps (2015-10 to now) gives mean p = .380 (SD .049, which is what binomial noise at n of about 60 produces), pooled p = .384 (EV +0.15R), SPY p = .417. 81% of those names have p > 1/3. Testing against that baseline (p0 = .384): CTAS raw .060, ELF .065, TTEK .113; Holm adjusted .72 / .72 / 1.0; zero names with raw p < .05 (0.6 expected by chance).
- Caveats: names are correlated (common market factor, overlapping dates), so the tests are not independent; current large caps carry survivorship bias; the 12 were picked from a scan of about 1,200 names and I also scored about 35 other names this week (10-21 to 10-27), so the true family is larger than 12 and the chance count is higher than 0.32. The 10-22 WELL result (EV +0.44R, lower bound +0.11) was exactly this kind of artifact (see Scoring below).

## Backtester history (read-only; D:\TradingBot\experiments\backtester)
Lookahead caveat from debate chair: the pooled "both signals" edge (TB + WS, +0.7pp) was a lookahead artifact: TB entered Mon-Thu before the Friday WS confirmation. See [[bt-2026-10-27-both-lookahead-free]]. WS alone is the honest base. All numbers below are from the 10-22 WS pool (80,133 events, signals 2015-10-16 to 2026-09-11; entry open after the Friday; 0.34% round-trip on trade and benchmark); WS-only = no TB core event in the same W-FRI week for that ticker. Survivorship biases all arms upward. Week-block bootstrap, 2000 draws. OOS = signals 2022+. The 10-23 and later signals are not in the pool.

Per-name WS-only, OOS, excess vs SPY (T20 exit | weekly-state exit), n events:
| Ticker | n | T20 x SPY | STATE x SPY | Note |
|---|---|---|---|---|
| ELF | 4 | +10.02% | +25.70% | n=4, noise; 12 events all-time avg +0.96% T20 |
| CACI | 12 | +1.33% [-2.17,+4.53] | -1.56% | |
| PGNY | 3 | +5.56% | -0.94% | n=3 |
| DHR | 4 | +0.79% | -4.89% | |
| WDAY | 7 | -0.07% [-5.63,+7.43] | -3.35% | |
| CTAS | 10 | -0.44% [-3.67,+2.11] | +1.22% [-3.38,+7.73] | 26 events all-time avg +0.04% T20 |
| TTEK | 7 | -0.87% [-5.57,+4.35] | -0.29% | |
| BDX | 4 | -2.13% | -1.69% | 20 events all-time -0.89% |
| USPH | 7 | -3.46% [-10.83,+3.29] | -10.33% [-16.46,-4.80] | 0 of 7 positive on STATE |
| CRM | 6 | -3.76% [-7.34,-0.42] | -2.37% [-4.53,+0.08] | |
| ADUS | 14 | -4.17% [-8.53,+0.22] | -4.17% [-8.10,-0.94] | 3 of 14 beat SPY |
| SONO | 4 | -10.94% | -7.81% | |
No name has a positive, CI-excluding-zero WS-only history. Per-name n (3 to 14) is far too small to support any single name; the negatives (ADUS, USPH, CRM, SONO) are the only ones with a CI or a clear sign, and they point against.

## Bear case: no-trade vs trade on a typical day
Pooled WS-only, OOS 2022+ (12,931 events over 241 weeks, about 54 per week), excess vs SPY net of 0.34% costs on both legs:
- T20 exit: -0.36% [-0.76, +0.06]; absolute +0.32%; 46% beat SPY.
- Weekly-state exit: -1.05% [-1.44, -0.64] (n 12,871); absolute -0.08%; only 30% of trades are positive.
- IS 2015-2021 was flat: T20 +0.19% [-0.20,+0.58], STATE +0.06% [-0.26,+0.39]. So the sign flipped OOS.
- By year (T20 x SPY): 2022 +0.21%, 2023 -1.08% [-1.83,-0.36], 2024 -0.21%, 2025 -0.51%, 2026 +0.10%. By extension: stretch 6-8% above SMA50 is -0.70% [-1.26,-0.11]; 0-3% -0.42% [-0.88,+0.06].
- The lookahead-free rerun agrees (WS_only T20 -0.36%, STATE_WS -1.04%, TB_only_delayed -0.47% [-0.90,-0.02]); the pooled BOTH_main T20 is -0.23% [-0.86,+0.43].
- Read: no-trade (SPY or cash, 0 excess) beats the typical WS-only trade by 0.36pp on a 20-day hold (CI includes 0) and by about 1.05pp with the state exit (CI excludes 0). Nothing in WS-only history says trading a random WS name on a random day beats not trading. The in-sample per-name EVs above are the trend-setup model on each stock's own history, which is not the WS signal edge and is mostly drift.

## Scoring earlier picks since flagged (live ~9:53 ET 10-28)
| Pick | Flagged | Entry / Stop / Target | Live | Return | R | SPY same window | Excess | Path |
|---|---|---|---|---|---|---|---|---|
| JNJ (10-23 best) | 10-23 | 267.61 / 254.73 / 293.37 | 268.98 | +0.51% | +0.11 | +0.83% | -0.32pp | open; high 272.36, low 262.40 |
| SAIC | 10-22 | 139.05 / 131.46 / 154.22 | 149.87 | +7.78% | +1.43 | -0.36% | +8.14pp | open; high 150.79 (0.4R short of target) |
| WELL (10-22 EV leader) | 10-22 | 234.19 / 224.17 / 254.23 | 220.00 | -6.06% | -1.00 (stop hit 10-28; gap open 222.80 means about -1.14R) | -0.36% | -5.70pp | earnings 10-26 after close: 10-27 -5.8%, 10-28 gap through stop |
| UTL | 10-26 | 55.05 / 52.93 / 59.30 | 54.45 | -1.09% | -0.27 | +1.06% | -2.15pp | open; 3 sessions |
| PKG | 10-27 | 253.88 / 243.54 / 274.55 | 250.43 | -1.36% | -0.33 | +0.39% | -1.75pp | open; 2 sessions |
Average of the five: -0.04% absolute, -0.36pp vs SPY; 3 of 5 trail SPY, 1 stopped, 1 near target. n = 5, no inference. Lesson that matters here: WELL was the EV-leading name with a CI above 0 and it lost to a flagged earnings gap, the same profile as ELF today (top EV, CI above 0, earnings in 7 days).
Entry-day bars for UTL and PKG include the full day high/low, so a path-extreme could be overstated. SPY reference is the prior close (JNJ, SAIC, WELL) or the entry-day open (UTL, PKG).

## Verdict inputs for debate chair
- Rank by EV: ELF, SONO, CTAS, TTEK, DHR, CACI, CRM, USPH, WDAY, ADUS, PGNY, BDX.
- Best name: CTAS. It is only rank 3 on raw EV, but it is the best-supported: highest lower bound (+0.11R), largest n (87), not stretched (+5.3%), no earnings for 7 weeks, liquid ($395M ADV), negatively correlated to the book (-0.18), highest p_up (.319) and MiroFish +33. ELF (rank 1) fails on a stretch of +9.5% and earnings in 7 days with a 13.5% average earnings move; SONO has CI low -0.08, n=27, Bull Trap, beta 1.78.
- If forced to trade CTAS: 23 sh under the $5k cap ($4.93k), risk $225 (0.11% of equity); the 1% sizing (206 sh, $44k) is not allowed.
- Strongest case for NO TRADE (5 lines):
  1. Nothing survives correction: Holm .063 at best (CTAS) against break-even, .72 against the generic-uptrend baseline; corrected CI for the best name includes 0 (EV -0.02 to +0.86R).
  2. The 12 per-name EVs are mostly 2015-2026 bull drift: the same setup on 53 large caps gives p .384 (EV +0.15R) vs 0.413 here, and cross-name spread matches binomial noise.
  3. WS-only OOS edge is negative: -0.36% vs SPY at T20 and -1.05% [-1.44,-0.64] with the state exit; the pooled BOTH edge was lookahead; survivorship biases all of it upward.
  4. Per-name WS history is 3-14 events and points down for ADUS, USPH, CRM, SONO; five names have earnings within 8 days (ADUS, PGNY, ELF, USPH, BDX), two more by day 14; none are executable in the scanner and setup_confidence 45.0 carries no information.
  5. Earlier picks: WELL (EV leader, CI above 0) stopped through a gap, PKG and UTL negative, JNJ about flat; and the $5k cap leaves 0.1-0.17% risk per name, so even a win barely moves a $201k book.

Related: [[quant-debate-2026-10-27]], [[bt-2026-10-27-both-lookahead-free]], [[scan-quality-2026-10-28]], [[trade-verdict-2026-10-27]], [[signal-scanner]], [[horizon-strategies]]
