---
source: n/a (quant agent analysis; yfinance daily OHLC to 2026-10-22 plus 10-23 intraday)
created: 2026-10-23
updated: 2026-10-23
tags: [quant, debate, predictions, liquidity]
---

# Quant debate log 2026-10-23

Uses the same model as [[quant-debate-2026-10-22]] and [[quant-debate-2026-10-21]]: entry = given price, stop = 2 x ATR14 (Wilder, computed here), target = 2R, 60-day max hold, history from 2015, setup = close > SMA50 > SMA200, Wilson 95% CI. **All p values are in-sample.**

The "10-23" scan prices equal the **Oct 22 closes**. Live prices around 09:50 CT: JNJ 263.64 (-1.5%), VRTX 528.88 (-1.2%), REGN 807.66 (-1.1%), LQDT 43.80 (-1.1%), EMR 159.16, LTC 43.56, CENTA 39.01.

| Rank | Ticker | Entry / stop / target | ATR% | p [CI] (n) | EV R [CI] | Kelly | Shares per $100k | Earnings (days) | Corr SPY 60d / MGY / SNA |
|---|---|---|---|---|---|---|---|---|---|
| 1 | LQDT | 44.30 / 42.19 / 48.51 | 2.38 | 0.389 [0.27-0.52] (54) | +0.17 [-0.19, +0.57] | 0.083 | 474 | 11/19 (27) | 0.01 / 0.14 / -0.01 |
| 2 | JNJ | 267.61 / 254.73 / 293.37 | 2.41 | 0.378 [0.28-0.49] (74) | +0.14 [-0.17, +0.48] | 0.068 | 77 | 2027-01-26 (95) | -0.19 / -0.15 / -0.18 |
| 3 | REGN | 816.33 / 773.16 / 902.68 | 2.64 | 0.358 [0.24-0.49] (53) | +0.08 [-0.27, +0.48] | 0.038 | 23 | **10/30 (7)** | 0.25 / 0.01 / -0.14 |
| 4 | LTC | 43.33 / 41.51 / 46.98 | 2.10 | 0.356 [0.25-0.48] (59) | +0.07 [-0.26, +0.45] | 0.034 | 548 | **11/03 (11)** | -0.34 / 0.25 / -0.14 |
| 5 | CENTA | 38.81 / 36.96 / 42.51 | 2.38 | 0.345 [0.24-0.47] (58) | +0.03 [-0.29, +0.42] | 0.017 | 540 | 11/25 (33) | -0.08 / -0.12 / -0.06 |
| 6 | EMR | 160.29 / 151.66 / 177.55 | 2.69 | 0.338 [0.24-0.46] (68) | +0.01 [-0.29, +0.37] | 0.007 | 115 | **11/04 (12)** | 0.57 / -0.28 / 0.54 |
| 7 | VRTX | 535.17 / 508.31 / 588.89 | 2.51 | 0.321 [0.23-0.43] (81) | -0.04 [-0.31, +0.29] | <0 | 37 | **11/02 (10)** | 0.07 / -0.06 / -0.04 |

- **All seven are in an uptrend** (close > 50d > 200d, with the 200d rising). Every EV CI includes 0.
- **Breakout / near-52wH conditioning:** it does not raise p for any name. LQDT falls to 0.31 (20d breakout) and 0.28 (near 52wH), and CENTA's breakout p is 0.25 [0.14-0.41].
- **Overextension (more than 8% above the 50d):** CENTA p = 0.321 [0.18-0.51] (n=28) vs 0.345 in general, with no detectable difference. For other names, JNJ is 0.545 (n=11) and LTC is 0.18 (n=11); both samples are too small to read.
- **Liquidity:** the positions are about $18-24k per $100k. As a share of 20d average dollar volume: LQDT 0.19% (ADV $10.8M), CENTA 0.20% (ADV $10.6M), LTC 0.10% ($24.8M), and the others about 0.0%. **None exceeds 1% of ADV.** The yfinance bid/ask was unreliable near the open (LQDT and CENTA showed about 57%), so spreads are **not available**. Average 20d high-low range is 1.9-2.7% for all names.
- **Best: JNJ.**
  - LQDT has the highest point EV (+0.17 vs +0.14), but its breakout and near-52wH conditioning lowers p, it reports earnings inside the window (11/19), and it is a small cap.
  - JNJ has the largest sample (n=74) and no earnings until January. Its correlation to SPY, MGY and SNA is negative, and it is the most liquid name.
  - Caveat: JNJ is trading -1.5% today, so a live entry gets a tighter R.

## Predictions (score by about 2027-01-20)
- JNJ P(293.37 before 254.73) = 0.38
- LQDT P(48.51 before 42.19) = 0.39
- REGN 0.36, LTC 0.36, CENTA 0.35, EMR 0.34, VRTX 0.32 (target before stop, using the levels above)
