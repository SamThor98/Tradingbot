---
source: n/a (quant agent analysis; yfinance daily OHLC to 2026-10-20; research_store/models/scan_confidence.json; validation_artifacts/prob_rank_control_confirm/scored_features_control_legacy_aug_labeled.parquet)
created: 2026-10-21
updated: 2026-10-21
tags: [quant, debate, predictions, ALL, BIIB, setup-confidence]
---

# Quant debate log 2026-10-21 (ALL, BIIB)

Requested by debate chair. Research only; no orders. Related: [[adaptive-stops]], [[guardrails]], [[scan-catalog]], [[hypothesis-ledger]], [[backtest]].

## Model
- Entry = 2026-10-20 close. Stop = entry - 2 x ATR14 (Wilder). Target = entry + 2R (4 x ATR). Max hold 60 trading days.
- Win prob: per-ticker history 2015-10..2026-10, setup = close > SMA50 > SMA200 (adjusted), non-overlapping trades, same-bar stop+target counted as a stop. Wilson 95% CI.
- EV = p*2 - (1-p)*1 on resolved trades; Kelly = p - (1-p)/2.
- Size: shares = 1% x equity / (entry - stop). Account equity not found in TradingBot state files; quoted per $100k.

## Data checks
- yfinance Oct 20 closes match debate inputs (ALL 249.91, BIIB 243.10). BIIB SMAs match. ALL SMA50/200 computed 246.12/226.38 vs given 245.82/224.88 (probably dividend adjustment or window differences; minor).
- Open positions from .positions_tracker.json / .exit_manager_state.json: MGY 154 sh, SNA 8 sh.

## Numbers
| | ALL | BIIB |
|---|---|---|
| Entry / stop / target | 249.91 / 238.76 / 272.21 | 243.10 / 229.92 / 269.46 |
| ATR14 | 5.58 (2.23%) | 6.59 (2.71%) |
| Daily vol 20d / 1y | 1.34% / 1.57% | 1.54% / 2.12% |
| Corr / beta SPY 60d | -0.17 / -0.44 | 0.26 / 0.62 |
| Corr / beta SPY 1y | -0.23 / -0.45 | 0.12 / 0.32 |
| Corr MGY / SNA 60d | 0.10 / 0.26 | -0.21 / 0.30 |
| Hist trades n (W/L/timeout) | 84 (30/49/5) | 43 (12/29/2) |
| p(win), resolved [95% CI] | 0.380 [0.281, 0.490] | 0.293 [0.176, 0.445] |
| EV (R) [CI] | +0.14 [-0.16, +0.47] | -0.12 [-0.47, +0.33] |
| Kelly / half | 0.070 / 0.035 | negative (no bet) |
| Shares per $100k at 1% risk | 89 (~$22.2k notional) | 75 (~$18.2k notional) |

Notes: ALL target 272.21 sits just under the 52w high 277.22, so the target is reachable inside the range. BIIB is at its 52w high 244.65 after a +4.1% day on Oct 20, so the target needs a 10% new high.

## Confidence > 45 test (revised 2026-10-21 after scan desk review)
**Original 66k test (superseded as a test of the live score):** I took `research_store/models/scan_confidence.json` (model `conf_57e9a8a229`, features `sma_200_slope`, `trend_strength_score`, `sma_20_slope`, all with sign -1 and fixed bounds) and applied it to the precomputed feature columns of `validation_artifacts/prob_rank_control_confirm/scored_features_control_legacy_aug_labeled.parquet` (66,301 rows, 2015-10 to 2026-06). It mirrors `core/scoring_confidence.py::compute_setup_confidence` (100 x mean of the inverted min-max features) **without** the rank-v2 risk caps in `score_setup_confidence`. So it is the uncapped score, not live `setup_confidence`: 79.7% of rows scored above 45, but only 8.1% of live rows do (745 of 861 live rows are `risk_capped`). The test is also in-sample. The artifact was fit on this same `control_legacy_aug` book, and the 16.4k realized trades are a subset of these rows.
- Result there: 40d forward return was 2.0pp lower for scores above 45 (CI -2.3 to -1.7), and the 40d hit rate was 1.4pp higher.

**Live-score validation:** I recomputed the score for the `webapp/webapp.db` `scan_results` rows (861 with setup_confidence, 9/21-10/21) with `signal_scanner._attach_setup_confidence`'s path. That path is `research.feature_engine.compute_feature_row` on yfinance bars as of the last completed bar, then `score_setup_confidence`. Results: n=859, corr 0.997, median abs diff 0.0, 91% within 0.5, max abs diff 9.96 (likely bar or provider differences).

**Out-of-sample test on stored live setup_confidence (forward returns from the yfinance scan-bar close):**
| Horizon | n >45 (tickers) | n <=45 | Mean >45 | Mean <=45 | Diff | Ticker-cluster bootstrap 95% CI | Welch p | MDE |
|---|---|---|---|---|---|---|---|---|
| 5d | 54 (10) | 643 | +0.18% | -0.70% | +0.88pp | [-1.17, +2.59] | 0.11 | 1.5pp |
| 10d | 36 (10) | 381 | -0.47% | -0.64% | +0.17pp | [-5.3, +6.5] | 0.91 | 4.1pp |
| 20d | 6 | 34 | +1.18% | +1.40% | -0.2pp | [-9.2, +9.8] | 0.96 | 13pp |

**Verdict: INCONCLUSIVE (small sample).** On the live score there are only 10 tickers above 45 across 14 scan dates. The 66k "refuted" result applies to the uncapped in-sample score, not to the live number.

## Predictions to score (by 2027-01-16, about 60 trading days)
- ALL: P(hit 272.21 before 238.76) = 0.38 (CI 0.28-0.49).
- BIIB: P(hit 269.46 before 229.92) = 0.29 (CI 0.18-0.45).

## Scored (2026-10-27, 5 sessions after the 10-20 entry)
Both predictions are still open: neither the target nor the stop has been touched, so the target-before-stop outcome is unresolved (deadline 2027-01-16). Interim path from the 10-20 close, last price 10-27 about 9:53 ET: **ALL** 252.78 (+1.15%, +0.26R; high 259.37 = +0.85R, low 247.89 = -0.18R; the target 272.21 is 7.7% above), **BIIB** 249.90 (+2.80%, +0.52R; high 254.41 = +0.86R, low 240.02 = -0.23R; the target 269.46 is 10.8% above). SPY -0.38% over the same window, so ALL +1.5pp and BIIB +3.2pp vs SPY. Both are on the right side of entry, which is consistent with, but does not test, the in-sample p of 0.38 and 0.29 (EV +0.14R and -0.12R). Details and the other 10-20 candidates are in the Outcome section of [[trade-verdict-2026-10-20]].

**Scored 2026-10-29 (10-28 due date):** ALL +0.12% (+0.03R) and BIIB +0.39% (+0.07R), both open at the 10-28 close and slightly behind SPY (+0.42%). See the Outcome section of [[trade-verdict-2026-10-21]].
