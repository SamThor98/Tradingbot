---
source: n/a (quant agent analysis; yfinance OHLC to 2026-10-26 10:10 ET; schwab_skill/webapp/webapp.db scan_results, pending_trades, portfolio_equity_snapshots, read-only; experiments/backtester pools and modules, read-only)
created: 2026-10-26
updated: 2026-10-26
tags: [quant, debate, predictions, UTL, utilities, liquidity]
---

# Quant debate log 2026-10-26: UTL

Sam asked "Debate UTL". UTL = Unitil Corporation (NYSE: UTL, regulated utility, sector XLU, market cap about $1.0B). Same model as [[quant-debate-2026-10-23]]: entry = live price, stop = 2 x ATR14 (Wilder), target = 2R, 60-day max hold, history from 2015-10, setup = close > SMA50 > SMA200, Wilson 95% CI. **All win rates below are in-sample.** Related: [[trade-verdict-2026-10-23]], [[bt-2026-10-23-debate-candidates]], [[scan-quality-2026-10-23]], [[signal-scanner]], [[stage-2-analysis]].

## Scan record
- webapp.db `scan_results`: 2 UTL rows. Job 23576331d4 (10-22, the 221-signal bypassed scan; price 53.58, weekly_swing only, not executable) and job 0a12065e50 (10-23 premarket, the corrected scan): price **55.06** (the 10-22 close), SMA50 52.82, SMA200 52.26, ext50 +4.2%, signal_score 30.45, entry_family stage2, **trend_breakout live + weekly_swing shadow both triggered**, `executable=true`, `breakout_confirmed=true`, stop_pct 5%, volume_ratio 0.70, adv_usd_proxy $7.1M, sector XLU winning.
- Red flags on the row: `forensic_flags=[altman_distress]` (scan-quality excluded UTL on this; Altman Z is unreliable for leveraged regulated utilities), `sec_risk_tag=high` (on 44 of 45 rows, so no information), `recent_8k=true`, advisory p_up_10d 0.143 (confidence low, expected 10d move -2.1%), setup_confidence 45.0 `risk_capped` (neutral, no information).
- **Pending trade:** `pending_trades` id 9f0ad8c3, UTL 90 sh @ 55.06, status `pending`, queued from the scan table 10-23 11:52 CT. An earlier 100-share request (79d40c7f) failed the guardrail ($5,472 > $5,000 max per ticker). No order exists in `orders`. Nothing was approved or touched by quant.
- No 10-26 scan is in the DB yet (latest row 10-23 13:49 UTC).

## Open positions (corrected)
`.positions_tracker.json` and `.exit_manager_state.json` are stale (MGY/SNA, last written 7/6 and 10/15). The latest Schwab snapshot in `portfolio_equity_snapshots` (2026-10-23) shows equity **$187,728.56** and stock legs LSAK, LSCC, LQDA, RRX, JAKK, ATEX, GRPN, INTC, ESTA (plus a few small option legs). **MGY and SNA are not in it.** Earlier quant pages measured correlation against MGY/SNA, which are no longer the open book. Snapshot market values sum to about $417k vs $187.7k equity (margin or snapshot quirk; not resolved). UTL correlation: market-value-weighted stock book -0.00 (60d) / -0.14 (1y); per name between -0.18 and +0.14; SPY -0.08 / -0.15; XLU 0.74 / 0.59. No utility in the book.

## Numbers
| Item | Value |
|---|---|
| Live price (10:10 ET) | 55.05 (open 55.19, range 54.99-55.55). Scan 55.06; 10-23 close 55.15 |
| Uptrend | Yes: close 55.05 > SMA50 52.85 > SMA200 52.33; SMA200 +1.0% over 20d; Stage 2 true on each of last 5 bars |
| ATR14 | 1.06 (1.93%) |
| Entry / stop / target | 55.05 / 52.93 / 59.30 (R = 2.12) |
| Scan stop 5% (52.30) | R:R to the same target 1.54 |
| p uptrend [CI] | 0.346 [0.232-0.482], n=52 (W18/L34) |
| EV R uptrend [CI] | +0.04 [-0.31, +0.45]; Kelly 0.019 (half 0.010; negative at CI low) |
| 20d breakout (within 1% of 20d high; now) | p 0.353 [0.215-0.521], n=34; EV +0.06 [-0.36, +0.56] |
| Near 52wH (within 3%; now 3.9% below 57.26, so just outside) | p 0.229 [0.121-0.390], n=35; EV -0.31 [-0.64, +0.17] |
| Within 3% above SMA50 | p 0.400 [0.256-0.564], n=35; EV +0.20 [-0.23, +0.69] (now +4.2%, so not in this bucket) |
| 52wH / 60d / 1y return | -3.9% / +2.1% / +9.7% |
| Sizing at 1% risk | $187,728.56 -> $1,877 risk -> **885 sh = $48.7k (26% of equity)**; per $100k: 470 sh ($25.9k) |
| Guardrail cap $5,000/ticker | 90 sh = $4.96k, risk $191 = 0.10% of equity |
| Liquidity | 20d ADV $6.27M (median $5.2M; avg 120k sh). 885 sh = 0.78% of ADV (0.74% of daily shares); 470 sh = 0.41%; 90 sh = 0.08%. None exceeds 1% |
| Spread | yfinance bid/ask 54.91/55.56 (1.2%, 10:10 ET) is not reliable this early; no verified spread. 20d avg high-low range 1.89% |
| Next earnings | **2026-11-02 (7 days; inside a 60-day hold)**. Past 24 earnings-day moves: mean absolute 1.7%, range -4.4% to +4.1% |
| Dividend | yield about 3.5% (ex-div mid-Nov expected; not modeled) |

## Backtester history for UTL (read from the pools and the same functions as the 10-23 debate script; costs 0.34% round trip, next-open entry)
| Arm / exit | n (IS / OOS) | OOS mean return | OOS excess vs SPY [95% CI] |
|---|---|---|---|
| TB / STATE | 40 / 21 | -2.12% (3 of 21 positive) | -2.8% [-5.2, -0.3] |
| TB / T20 | 40 / 21 | -1.65% | -3.55% [-6.9, -0.1] |
| WS / STATE | 19 / 17 | -1.60% | -1.41% [-3.8, +1.0] |
| WS / T20 | 19 / 17 | -0.42% | -0.06% [-3.4, +3.3] |
| BOTH / STATE | 2 / 2 | +4.05% | n too small |
Pool TB OOS mean is about -0.5%, so UTL is worse than the TB pool. Only 6 same-week BOTH events exist (last 2024-10-16).
**Is the signal live?** Yes for trend_breakout: Stage 2 true, TB trigger on each of the last 5 bars, last TB event 10-20 (entry would have been 10-21, so a new entry now is 4 sessions late and about 2.5% higher). weekly_swing state is on but its last event was 10-16, so the same-week BOTH criterion is not met. This is the opposite of JNJ on 10-23, where the backtester found Stage 2 off and the last event 9-24 (not firing). Stage 2 ran on this scan (stage2_fail 1198); the 10-22 bypass does not apply.

## Verdict: PASS
- No positive edge: EV +0.04R, CI [-0.31, +0.45]; the 20d-breakout and near-high buckets are no better.
- UTL's own backtester history for the trend_breakout arm is negative with an OOS CI below zero, and it trails the pool.
- Earnings in 7 days inside the hold; advisory p_up_10d is 14%; Altman and recent-8k flags (caveat above); thin ADV.
- At 1% risk the size is 885 sh (26% of equity), which is not consistent with a coin-flip edge; the guardrail-sized 90 sh is a token position.
- Positives: near-zero correlation to the book and SPY, clean uptrend, liquidity is fine at 100-885 sh, defensive sector.
- If Sam still wants exposure: no more than half of the 1% size by risk (about 440 sh) and not before the 11-02 report; the guardrail would block it above 90 sh anyway.
- Quant recommends rejecting pending trade 9f0ad8c3.

## Predictions (score by 2027-01-25, about 60 trading days)
- P(59.30 before 52.93 from 55.05) = 0.35 [0.23-0.48] (uptrend); breakout bucket 0.35 [0.22-0.52].
- Expected EV about +0.04R (CI includes 0); 95% of the mass between -0.31R and +0.45R.
- Earnings reaction 11-02/11-03: expected absolute move about 1.7%; P(|move| > 3%) about 0.25 (6 of 24 history).
- Backtester TB arm: expect negative excess vs SPY (OOS mean -2.8%) if a position is taken and held to STATE exit.
