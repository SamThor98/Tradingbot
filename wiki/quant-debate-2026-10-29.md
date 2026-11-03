---
source: n/a (quant agent analysis; yfinance OHLC and 1-minute quotes 2026-10-29 about 9:45 ET; news via web search (Reuters, 24/7 Wall St., DFIN 8-K 2026-10-29); experiments/backtester WS pool 2026-10-22, read-only)
created: 2026-10-29
updated: 2026-10-29
tags: [quant, debate, predictions, CTAS, HRB, FIZZ, DFIN, weekly-swing, earnings-rule, multiple-testing]
---

# Quant debate log 2026-10-29

Twelve weekly_swing-only, non-executable names (scan degraded). Same model as [[quant-debate-2026-10-28]]: entry = LIVE price (about 9:45 ET), stop = entry - 2 x ATR14 (Wilder, completed bars through 10-28), target = entry + 4 x ATR (2R), 60-day max hold, setup close > SMA50 > SMA200 (adjusted) since 2015-10, Wilson 95% CI, EV = 3p - 1. Equity base $201,729.40 (10-26 snapshot). No orders.

NEW RULE applied: earnings inside the 60-trading-day hold (to about 2027-01-26) are flagged; earnings within the first 2 weeks (by 11-12) = VETO or half size. Earnings dates are yfinance estimates, unconfirmed.

## Gaps today
- CRM -4.7% and WDAY -4.2% vs the 10-28 close: no company news; sympathy selling in software after ServiceNow (-19%, slower cRPO guide) and Fortinet (-25%) reported 10-28 after the close (IGV about -5%, SPY about -0.5%). CRM is now 3.1% BELOW its SMA50 and fails the uptrend check.
- DFIN +8.2%: Q3 reported 10-29 premarket (8-K): adj EPS 1.07 vs 0.84 est, revenue 185.0M vs 179.9M, Q4 guide 175-180M above consensus. Earnings are therefore behind it (next about Feb 2027, outside the hold), but the price is now +16.3% above SMA50.

## Numbers (ranked by EV)
| # | Ticker | Live | ATR (%) | Stop / Target | n (W/L) | p [95% CI] | EV R [95% CI] | Ext vs 50d | Uptrend | $5k cap sh (risk $) | Earnings | Rule |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | SONO | 17.48 | 0.55 (3.2%) | 16.37 / 19.70 | 27 (13/14) | .481 [.31-.66] | +0.44 [-0.08,+0.98] | +5.3% | yes | 286 ($317) | 11-10 | VETO/half (wk 2) |
| 2 | CTAS | 215.96 | 4.83 (2.2%) | 206.29 / 235.28 | 87 (41/46) | .471 [.37-.58] | +0.41 [+0.11,+0.73] | +5.9% | yes | 23 ($222) | 12-17 | in hold, after wk 2: ok |
| 3 | DFIN (new) | 57.50 | 1.63 (2.8%) | 54.24 / 64.02 | 45 (21/24) | .467 [.33-.61] | +0.40 [-0.01,+0.83] | +16.3% | yes | 86 ($280) | reported 10-29 | ok, but stretched |
| 4 | TTEK | 37.34 | 0.83 (2.2%) | 35.67 / 40.68 | 74 (34/40) | .459 [.35-.57] | +0.38 [+0.05,+0.72] | +5.8% | yes | 133 ($222) | 11-11 | VETO/half (wk 2) |
| 5 | DHR | 234.14 | 6.67 (2.8%) | 220.80 / 260.82 | 59 (26/33) | .441 [.32-.57] | +0.32 [-0.04,+0.70] | +7.4% | yes | 21 ($280) | 2027-01-27 | just outside hold |
| 6 | CACI | 676.05 | 20.68 (3.1%) | 634.69 / 758.79 | 71 (30/41) | .423 [.31-.54] | +0.27 [-0.06,+0.62] | +8.4% | yes | 7 ($290) | 2027-01-20 | in hold, late |
| 7 | CRM | 230.66 | 8.54 (3.7%) | 213.58 / 264.82 | 73 (30/43) | .411 [.31-.53] | +0.23 [-0.08,+0.58] | -3.1% | NO | 21 ($359) | 12-02 | in hold |
| 8 | USPH | 86.68 | 2.78 (3.2%) | 81.13 / 97.78 | 55 (22/33) | .400 [.28-.53] | +0.20 [-0.16,+0.60] | +5.0% | yes | 57 ($316) | 11-04 | VETO/half (wk 1) |
| 9 | WDAY | 195.70 | 6.88 (3.5%) | 181.93 / 223.24 | 65 (25/40) | .385 [.28-.51] | +0.15 [-0.17,+0.52] | +0.8% | yes | 25 ($344) | 12-01 | in hold |
| 10 | HRB (new) | 48.31 | 1.58 (3.3%) | 45.15 / 54.62 | 56 (21/35) | .375 [.26-.51] | +0.12 [-0.22,+0.52] | +3.2% | yes | 103 ($325) | 11-09 | VETO/half (wk 2) |
| 11 | FIZZ (new) | 34.51 | 1.03 (3.0%) | 32.46 / 38.61 | 64 (24/40) | .375 [.27-.50] | +0.12 [-0.20,+0.49] | +7.4% | NO (SMA50 32.13 < SMA200 33.66) | 144 ($295) | 12-10 | in hold |
| 12 | ADUS | 123.59 | 2.76 (2.2%) | 118.07 / 134.64 | 50 (18/32) | .360 [.24-.50] | +0.08 [-0.28,+0.50] | +5.8% | yes | 40 ($221) | 11-03 | VETO/half (wk 1) |
Liquidity: all positions under 0.05% of 20d ADV at the cap (DFIN ADV $12.2M, FIZZ $12.1M, HRB $77M). Book correlation (60d) all low; FIZZ -0.37, ADUS -0.29, CTAS -0.22, HRB -0.21.

## New names
- HRB: EV +0.12R, CI includes 0; earnings 11-09 (week 2) -> veto/half; -17.7% from the 52wH. Backtester WS-only OOS n=6: T20 +1.27% vs SPY, STATE +6.04% (all-time n=17, +1.35%). Weak positive history, small n.
- FIZZ: fails the uptrend (SMA50 below SMA200, flat 200d); EV +0.12R; WS-only OOS n=5: T20 -7.19%, STATE -7.45% (0 of 5 beat SPY). Drop.
- DFIN: EV +0.40R [-0.01,+0.83] on n=45, but the entry is a post-earnings gap at +16.3% above SMA50 (twice the 8% scan cut); WS-only OOS n=4: T20 -8.04%, STATE -2.61% (0 of 4 beat SPY). Not a candidate at this price.

## Multiple testing (12 names)
- Holm vs break-even (p0 = 1/3): CTAS raw .005 / Holm .063; TTEK .016 / .178; DFIN .043 / .434; all others Holm >= .50. Nothing below .05.
- Holm vs generic-uptrend baseline (p0 = .384): CTAS .060 / .722; everything else Holm 1.0.
- Adjusted (99.6%) EV CIs: CTAS [-0.02,+0.86], SONO [-0.27,+1.18], DFIN [-0.17,+1.00]; all include 0.
- Expected names with 95% lower bound above 0 by chance: 0.32; observed 2 (CTAS, TTEK), consistent with drift plus noise (see 10-28 baseline).

## Changes vs 10-28
- Out: ELF, PGNY, BDX. In: HRB, FIZZ, DFIN.
- CRM -5.6% vs yesterday's live 244.30 and now fails the uptrend (was +3.1% above SMA50).
- WDAY 206.80 -> 195.70; ext 6.7% -> 0.8%.
- CACI 661.65 -> 676.05; ext 6.1% -> 8.4% (now above the 8% cut).
- DHR 237.77 -> 234.14; ext 9.4% -> 7.4% (now inside the cut).
- CTAS 214.40 -> 215.96 (stop 206.29, target 235.28); EV unchanged +0.41R.
- Earnings rule now vetoes/halves SONO, TTEK, USPH, ADUS, HRB.
- Backtester per-name WS-only history for the 9 carry-over names is unchanged (see [[quant-debate-2026-10-28]]).

## Verdict input
Best: CTAS, the only name that passes all screens: uptrend, ext 5.9%, earnings 12-17 (after week 2), EV +0.41R [+0.11,+0.73] on n=87, -0.22 corr to the book. At the cap: 23 sh, $4.97k, risk $222 (0.11% of equity). It still fails multiple testing (Holm .063, adjusted CI includes 0), and the pooled WS-only OOS edge is negative (-0.36% vs SPY T20, -1.05% state exit; [[bt-2026-10-27-both-lookahead-free]]). Lean: NO TRADE, CTAS as the only candidate if the chair wants one.

Related: [[quant-debate-2026-10-28]], [[trade-verdict-2026-10-22]], [[bt-2026-10-27-both-lookahead-free]], [[signal-scanner]], [[horizon-strategies]]
