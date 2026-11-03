---
source: n/a (quant agent analysis; yfinance OHLC and 1-minute quotes 2026-10-27 about 9:53 ET; schwab_skill/webapp/webapp.db scan 6089c1d034 (2026-10-27 13:49 UTC), portfolio_equity_snapshots 2026-10-26, pending_trades; experiments/backtester modules, read-only)
created: 2026-10-27
updated: 2026-10-27
tags: [quant, debate, predictions, PKG, NTNX, FBNC, weekly-swing, trend-breakout]
---

# Quant debate log 2026-10-27

Ten scan-desk names for debate chair. Same model as [[quant-debate-2026-10-26]]: entry = live price, stop = entry - 2 x ATR14 (Wilder, completed bars through 10-26), target = entry + 4 x ATR (2R), 60-day max hold, history 2015-10 on, setup close > SMA50 > SMA200 (adjusted), non-overlapping trades, same-bar stop+target = stop, Wilson 95% CI. **All win rates are in-sample.** 10 names were tested at once, so about 1 lower bound above zero is expected by chance. Related: [[scan-quality-2026-10-27]], [[trade-verdict-2026-10-20]], [[signal-scanner]], [[stage-2-analysis]], [[guardrails]].

## Data checks
- **Scan prices are 10-26 closes, not live** (CACI 655.02 and RLI 64.22 match no close: those rows were built on near-empty partial bars, 1,717 and 6,227 shares). Live at 9:53 ET: PAYX 113.19 (-2.2% vs scan), FBNC 64.99 (-1.0%), CRM 245.18 (-1.0%), RLI 63.79 (-0.7%), CACI 661.96 (+1.1%), NTNX 73.37 (+0.9%), others within 0.5%. PAYX now trades below its SMA50 (113.70), so it fails the uptrend check at the live price.
- Scan 6089c1d034 ran Stage 2 (stage2_fail 1176, entry_shadow_stage2_evaluated 316). Stage A/B diagnostics are normal (37 signals; 3 stale bars, 7 empty frames).
- Equity: $201,729.40 (snapshot 2026-10-26). Current book (snapshot): LSAK, LSCC, RRX, JAKK, ATEX, GRPN, INTC, ESTA, **UTL 42 sh ($2,321, new since 10-23)** plus option legs. Stock market value is about $379k vs $201.7k equity (about 1.9x), so every new position adds to a book that is already above equity. Pending UTL trade 9f0ad8c3 (90 sh) is still `pending`.

## Table (ranked by EV, uptrend setup; entry = live)
| # | Ticker | Signal / scanner | Live | ATR (%) | Stop / Target | p [CI] n | EV R [CI] | Kelly | sh per $100k (notional) | At equity 1% risk | $5k guardrail | Earnings |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | NTNX | BOTH, Stage 2 executable | 73.37 | 2.27 (3.1%) | 68.83 / 82.44 | 0.474 [0.35-0.60] 57 | +0.42 [+0.05, +0.80] | 0.211 | 220 ($16.1k) | 444 sh $32.6k (16.1%) | 68 sh, risk 0.15% | 11-25 (29d) |
| 2 | TTEK | WS only, not executable | 36.88 | 0.87 (2.4%) | 35.15 / 40.35 | 0.459 [0.35-0.57] 74 | +0.38 [+0.05, +0.72] | 0.189 | 576 ($21.2k) | 1163 sh $42.9k (21.3%) | 135 sh, 0.12% | **11-11 (15d)** |
| 3 | PKG | BOTH, Stage 2 executable | 253.88 | 5.17 (2.0%) | 243.54 / 274.55 | 0.423 [0.32-0.53] 78 | +0.27 [-0.04, +0.60] | 0.135 | 96 ($24.4k) | 195 sh $49.5k (24.5%) | 19 sh, 0.10% | 2027-01-26 |
| 4 | CACI | WS only, not executable | 661.96 | 21.91 (3.3%) | 618.14 / 749.58 | 0.423 [0.31-0.54] 71 | +0.27 [-0.06, +0.62] | 0.134 | 22 ($14.6k) | 46 sh $30.5k (15.1%) | 7 sh, 0.15% | 2027-01-20 |
| 5 | CRM | WS only, not executable | 245.18 | 8.70 (3.6%) | 227.78 / 279.97 | 0.411 [0.31-0.53] 73 | +0.23 [-0.08, +0.58] | 0.116 | 57 ($14.0k) | 115 sh $28.2k (14.0%) | 20 sh, 0.17% | 12-02 (36d) |
| 6 | PAYX | WS only, not executable | 113.19 | 2.91 (2.6%) | 107.37 / 124.84 | 0.364 [0.27-0.48] 77 | +0.09 [-0.20, +0.43] | 0.045 | 171 ($19.4k) | 346 sh $39.2k (19.4%) | 44 sh, 0.13% | 12-18 (52d) |
| 7 | ADUS | WS only, not executable | 121.56 | 2.83 (2.3%) | 115.91 / 132.87 | 0.360 [0.24-0.50] 50 | +0.08 [-0.28, +0.50] | 0.040 | 176 ($21.4k) | 356 sh $43.3k (21.5%) | 41 sh, 0.12% | **11-03 (7d)** |
| 8 | RLI | WS only, not executable | 63.79 | 1.78 (2.8%) | 60.24 / 70.90 | 0.352 [0.25-0.47] 71 | +0.06 [-0.25, +0.40] | 0.028 | 281 ($17.9k) | 567 sh $36.2k (17.9%) | 78 sh, 0.14% | 2027-01-20 |
| 9 | FBNC | BOTH, Stage 2 executable | 64.99 | 1.47 (2.3%) | 62.05 / 70.85 | 0.333 [0.23-0.46] 60 | +0.00 [-0.32, +0.38] | 0.000 | 341 ($22.2k) | 688 sh $44.7k (22.2%) | 76 sh, 0.11% | 2027-01-20 |
| 10 | BDX | WS only, not executable | 196.10 | 4.11 (2.1%) | 187.87 / 212.55 | 0.283 [0.19-0.41] 60 | -0.15 [-0.44, +0.22] | negative | 121 ($23.7k) | 245 sh $48.0k (23.8%) | 25 sh, 0.10% | **11-05 (9d)** |

Earnings dates are yfinance estimates and are unconfirmed; the 2027 dates are placeholders (FBNC, RLI, CACI and PKG just reported). Within 60 days: ADUS, BDX, TTEK (inside 2 weeks), NTNX, CRM, PAYX. The average earnings-window move over the last 12 reports is 3.4% to 9.5% (NTNX 9.5%, CRM 7.4%, CACI 6.8%, TTEK 6.4%, BDX 6.0%, ADUS 5.9%).

### Conditioning (20d breakout within 1% of the 20d high / near 52wH within 3% / within 3% above SMA50)
| Ticker | EV breakout | EV near-52wH | EV <=3% above SMA50 | Ext vs SMA50 now | From 52wH |
|---|---|---|---|---|---|
| NTNX | +0.33 (CI low -0.11) | -0.08 | +0.56 | +6.2% | -3.4% |
| TTEK | +0.36 (CI low -0.01) | +0.10 | +0.29 | +4.6% | -14.5% |
| PKG | +0.28 (CI low -0.09) | +0.15 | +0.41 | +7.0% | -2.3% |
| CACI | +0.14 | +0.20 | +0.35 | +6.2% | -3.2% |
| CRM | +0.25 | +0.26 | +0.40 | +3.9% | -8.9% |
| PAYX | +0.15 | +0.08 | +0.14 | -0.4% | -11.6% |
| ADUS | 0.00 | -0.17 | +0.19 | +4.2% | -2.3% |
| RLI | -0.09 | +0.07 | +0.12 | +5.2% | -5.2% |
| FBNC | +0.04 | -0.12 | -0.09 | +3.5% | -2.7% |
| BDX | -0.05 | 0.00 | -0.09 | +5.6% | -2.2% |
Extension is not a handicap in the history for PKG (EV +0.44 when 5% or more above the 50d, n=50, CI +0.04 to +0.84) or NTNX (+0.31, CI -0.08 to +0.73).

## Liquidity (20d ADV in $, position as % of ADV; flag if above 1%): no flags
ADV: CRM $1.83B, PAYX $311M, BDX $283M, NTNX $176M, CACI $164M, PKG $155M, TTEK $65M, RLI $59M, ADUS $21.6M, FBNC $12.8M. At 1% risk of equity the biggest position share of ADV is FBNC 0.35%, then ADUS 0.20%; all the rest are 0.07% or less. At $100k equity FBNC is 0.17%. No spread is verified (yfinance quotes at 9:53 ET are unreliable). 20d average range: 1.95% (PKG) to 3.4% (CRM).

## Correlations (60d / 1y; book = market-value-weighted stock legs incl. UTL)
| Ticker | SPY 60d / 1y | Beta 60d | Book 60d / 1y | Largest single-name pair (60d) |
|---|---|---|---|---|
| NTNX | 0.50 / 0.20 | 1.56 | 0.35 / 0.02 | JAKK +0.29 |
| TTEK | 0.25 / 0.10 | 0.61 | 0.03 / 0.00 | UTL +0.24 |
| PKG | 0.29 / 0.32 | 0.63 | 0.15 / 0.16 | JAKK +0.29 |
| CACI | 0.07 / 0.07 | 0.40 | -0.07 / -0.10 | ESTA -0.42 |
| CRM | 0.19 / 0.11 | 1.10 | 0.01 / -0.11 | GRPN +0.20 |
| PAYX | 0.09 / -0.01 | 0.28 | -0.27 / -0.23 | LSCC -0.39 |
| ADUS | -0.27 / 0.07 | -0.59 | -0.39 / -0.01 | LSCC -0.44 |
| RLI | -0.08 / -0.24 | -0.20 | -0.39 / -0.37 | LSCC -0.52 |
| FBNC | 0.08 / 0.26 | 0.18 | 0.08 / 0.21 | JAKK +0.36 |
| BDX | 0.04 / 0.14 | 0.10 | -0.32 / -0.10 | INTC -0.47 |
INTC is 62% of equity, so book correlation is dominated by it and by LSAK/ESTA/ATEX; none of the ten has correlation above 0.35 to the book.

## Uptrend check and signal liveness (JNJ/UTL standard: Stage 2 on, trigger firing now, same-week event)
All ten have close > SMA50 > SMA200 as of the 10-26 close, and the SMA200 is rising over 20 days in 6 of 10 (ADUS, FBNC, CACI, NTNX, BDX, PKG; flat or falling for PAYX, CRM, TTEK and RLI). PAYX fails at the live price.
| Ticker | Scanner (10-27) | Backtester frame as of 10-26 close | Last TB event / last WS event | Same-week BOTH? |
|---|---|---|---|---|
| NTNX | stage2, executable, TB live triggered + WS shadow triggered | Stage 2 on, TB trigger on | TB 2026-10-21, WS 2026-10-23 | **Yes (week ending 10-23)** |
| PKG | stage2, executable, both triggered | Stage 2 on, TB trigger off | TB 2026-08-04, WS 2026-10-23 | No |
| FBNC | stage2, executable, both triggered | Stage 2 on, TB trigger off | TB 2026-09-10, WS 2026-10-23 | No |
| PAYX, ADUS, CRM, CACI, TTEK, BDX, RLI | entry_family horizon, executable=false, trend_breakout NOT triggered; WS shadow only | Stage 2 off, TB off | TB events 2024-2026-09 (CACI 03-05, CRM 2025-02, PAYX 2025-05, ADUS 2024-12, TTEK 2024-11, RLI 2024-12, BDX 09-24); WS 10-23 (CRM 10-16, RLI 10-16, BDX 10-09) | No |
The seven WS-only names have no live (executable) trend_breakout signal. Per the scanner `BOTH` for PKG and FBNC means the TB plugin triggered today; the backtester's own event reconstruction has no new TB event for them, so they are state matches, not fresh events.

## Backtester per-name history (pools/functions from the 10-23 debate script; 0.34% round trip; excess vs SPY, week-block bootstrap CI; OOS = 2022+)
| Ticker | TB STATE OOS n, mean, xSPY [CI] | TB T20 OOS xSPY [CI] | WS STATE OOS n, xSPY [CI] | BOTH (all) n, xSPY STATE / T20 |
|---|---|---|---|---|
| PKG | n33, +1.38%, +2.15% [+0.4, +4.0] | +1.75% [-0.1, +3.7] | n14, +0.37% [-4.1, +4.7] | n8, +4.1% [-0.1, +8.3] / +5.8% [+3.5, +8.2] |
| NTNX | n26, +2.19%, +0.63% [-2.8, +4.9] | -0.92% [-4.2, +2.2] | n12, +0.21% [-8.1, +10.7] | n10, -1.4% / -4.8% [-10.5, +0.7] |
| FBNC | n28, -0.62%, -0.97% [-2.8, +1.1] | -0.60% [-2.8, +1.9] | n15, -2.64% [-4.5, -0.6] | n14, +0.5% / +0.5% |
| TTEK | n21, -1.75%, -2.70% [-4.0, -1.6] | -3.14% [-6.1, -0.5] | n9, -0.61% [-3.4, +3.3] | n9, -0.2% / -0.1% |
| CACI | n29, -0.04%, -0.10% [-1.7, +1.6] | +0.08% [-1.4, +1.8] | n19, -1.28% [-3.3, +0.6] | n13, +0.2% / -0.1% |
| CRM | n12, -0.25%, -1.61% [-3.2, 0.0] | -2.91% [-5.3, -0.4] | n7, -2.66% [-4.1, -1.2] | n3 (too few) |
| PAYX | n23, +0.18%, -0.33% [-2.1, +1.4] | +0.92% [-0.8, +2.6] | n16, -0.26% [-2.3, +1.9] | n10, -2.1% [-3.5, -0.7] / +0.2% |
| ADUS | n12, -2.26%, -2.31% [-6.9, +1.2] | -1.51% [-5.5, +1.9] | n15, -4.17% [-7.9, -1.3] | n6 (ALL), n1 OOS |
| RLI | n30, -0.17%, -0.78% [-2.2, +0.8] | -2.21% [-4.3, -0.1] | n17, -1.47% [-4.1, +0.9] | n13, +0.8% / +1.9% |
| BDX | n17, -2.68%, -1.18% [-3.1, +0.7] | +1.11% [-1.6, +3.8] | n7, +0.87% [-3.1, +5.1] | n9, +0.5% / +1.3% |
Only PKG has a positive TB OOS excess whose CI excludes zero. TTEK, CRM and RLI have negative OOS excess with CIs that exclude zero; ADUS WS-only is negative with a CI that excludes zero. NTNX's own history is a wash (CI spans zero, BOTH T20 negative).

## Signal-quality flags (scan 6089c1d034)
| Ticker | setup_confidence | advisory p_up_10d | MiroFish | Other flags |
|---|---|---|---|---|
| PKG | 74.38 (not capped, the only uncapped name) | 0.27 | +33 Moderate Continuation | volume 1.49x, 8-K 5d ago (earnings), sec medium, beat +3.2%, vcp no |
| NTNX | 45.0 risk_capped | 0.10 | -14 Bull Trap | forensic sloan_high, volume ratio 0.71, sector XLK not winning, scan stop 8.3% (wider than the 2 ATR stop), sec high |
| FBNC | 45.0 risk_capped (composite 43.2) | 0.14 | +6 | thin ADV $12.8M, sec high |
| BDX, PAYX, CRM, CACI, TTEK, ADUS, RLI | 45.0 risk_capped (RLI composite 41.5) | 0.07-0.18 | BDX +16, PAYX +29, CRM +4, CACI -13 Bull Trap, RLI -3 Bull Trap (TTEK, ADUS not pulled) | volume ratio 0.0-0.98 on the horizon rows, sec high, executable=false |
`setup_confidence` 45.0 with risk_capped means no information (see [[quant-debate-2026-10-21]]). Advisory scoring is low confidence on all 81 scored rows.

## Verdict
**Best: PKG (Packaging Corp of America), at half size.** The reasons are that it is the only name with all of these: scanner Stage 2 executable with the live trend_breakout trigger, positive EV (+0.27R, CI low -0.04 so it is not significant), the only OOS-significant TB history (xSPY +2.15%, CI +0.4 to +4.0), the only uncapped confidence (74), low correlation to the book (0.15) and no earnings until late January with $155M ADV. Against it: 7% above the SMA50, advisory p_up 0.27, its last TB event (8-4) and WS event (10-23) are not in the same week, and the lower CI includes zero. Half of 1% risk is about 98 shares ($25k) at 253.88 / 243.54 / 274.55; the $5,000 guardrail caps it at 19 shares (risk $196, 0.10% of equity).
- NTNX is the highest EV (+0.42, CI +0.05 to +0.80) and the only same-week event, but is a pass: Bull Trap -14, sloan_high, advisory p_up 0.10, beta 1.56, no OOS edge in its own history, earnings in 29 days.
- TTEK (+0.38) and the other WS-only names are not executable and the scanner has no live trend_breakout trigger; TTEK also has earnings 11-11 and a negative TB OOS history. ADUS and BDX have earnings in 7 and 9 days. FBNC is EV 0.00. If none of the three executable names is wanted, the answer is none.

## Predictions (score by 2027-01-22, about 60 trading days)
- P(target before stop): PKG 0.42 [0.32-0.53] (274.55 before 243.54); NTNX 0.47 [0.35-0.60] (82.44 before 68.83); FBNC 0.33 [0.23-0.46] (70.85 before 62.05); TTEK 0.46 [0.35-0.57]; CACI 0.42 [0.31-0.54]; CRM 0.41 [0.31-0.53]; PAYX 0.36 [0.27-0.48]; ADUS 0.36 [0.24-0.50]; RLI 0.35 [0.25-0.47]; BDX 0.28 [0.19-0.41].
- EV (R): PKG +0.27, NTNX +0.42, FBNC 0.00 (CIs above); expect the realized average to be below the in-sample EV (selection and in-sample bias).
- Earnings reaction: ADUS 11-03 expected absolute move about 5.9%; BDX 11-05 about 6.0%; TTEK 11-11 about 6.4%.
- Backtester TB arm: PKG expected positive excess vs SPY; NTNX and TTEK not distinguishable from zero or worse.
