# Sector map override audit

Point-in-time comparison of the yfinance sector label against the bundled sponsor map. The map is today's classification (sponsor files as of 2026-10-27), not the sector those stocks had on earlier dates.

Retrieved 2026-10-29. yfinance `Ticker.info['sector']` was read that day for every symbol in the tracked universe and watchlist lists below. The yfinance ETF is the **off-mode** `SECTOR_TO_ETF` lookup, so `Consumer Defensive` stays unresolved. `BF.B` was read as `BF-B` because yfinance returns 404 for the dot form.

Tracked lists:

- `signal_scanner.py` `DEFAULT_WATCHLIST`
- `watchlist_loader.py` `_NASDAQ100_FALLBACK`
- `scripts/analyze_sector_regime.py` `DEFAULT_TICKERS`
- `scripts/refresh_prob_rank_dual_run_sample.py` `SAMPLE_TICKERS`
- `scripts/refresh_scoring_trade_sample.py` `SAMPLE_TICKERS`
- `scripts/refresh_sec_cache.py` `REFRESH_TICKERS`
- `scripts/run_entry_timing_experiment_scan.py` `EXPERIMENT_SMOKE_TICKERS`
- `scripts/run_scoring_trades_backtest.py` `SAMPLE_TICKERS`
- `scripts/verify_prob_rank_dashboard_scan.py` `DEFAULT_TICKERS`

## Counts

- Tracked tickers: 245
- In the bundled map: 231
- Not in the bundled map: 14
- Agree (yfinance ETF equals bundled ETF): 210
- Disagree: 21

Tracked names that are not in the bundled map are not counted as disagreements: AVB (Real Estate), EA (Communication Services), EQR (Real Estate), HES (no sector), IWM (no sector), K (no sector), MMC (no sector), PARA (Technology), PXD (no sector), QQQ (no sector), SHOP (Technology), SNOW (Technology), SPY (no sector), WBD (Communication Services).

The local `.sector_map_cache.json` on Sam's computer was not in this workspace. It still needs the same check: compare each cached ETF with this bundled map before trusting shadow or live counts from that cache.

## Disagreements

| Ticker | yfinance symbol | yfinance sector | yfinance ETF (off) | Bundled ETF | Sources |
| --- | --- | --- | --- | --- | --- |
| BF.B | BF-B | Consumer Defensive | — | XLP | signal_scanner.py:DEFAULT_WATCHLIST |
| CL | CL | Consumer Defensive | — | XLP | signal_scanner.py:DEFAULT_WATCHLIST |
| COST | COST | Consumer Defensive | — | XLP | signal_scanner.py:DEFAULT_WATCHLIST;watchlist_loader.py:_NASDAQ100_FALLBACK;scripts/refresh_prob_rank_dual_run_sample.py:SAMPLE_TICKERS;scripts/refresh_scoring_trade_sample.py:SAMPLE_TICKERS;scripts/run_entry_timing_experiment_scan.py:EXPERIMENT_SMOKE_TICKERS;scripts/verify_prob_rank_dashboard_scan.py:DEFAULT_TICKERS |
| CPB | CPB | Consumer Defensive | — | XLP | signal_scanner.py:DEFAULT_WATCHLIST |
| DD | DD | Basic Materials | XLB | XLI | signal_scanner.py:DEFAULT_WATCHLIST |
| GIS | GIS | Consumer Defensive | — | XLP | signal_scanner.py:DEFAULT_WATCHLIST |
| HSY | HSY | Consumer Defensive | — | XLP | signal_scanner.py:DEFAULT_WATCHLIST |
| KHC | KHC | Consumer Defensive | — | XLP | signal_scanner.py:DEFAULT_WATCHLIST |
| KMB | KMB | Consumer Defensive | — | XLP | signal_scanner.py:DEFAULT_WATCHLIST |
| KO | KO | Consumer Defensive | — | XLP | signal_scanner.py:DEFAULT_WATCHLIST;scripts/analyze_sector_regime.py:DEFAULT_TICKERS;scripts/refresh_prob_rank_dual_run_sample.py:SAMPLE_TICKERS;scripts/refresh_scoring_trade_sample.py:SAMPLE_TICKERS;scripts/run_entry_timing_experiment_scan.py:EXPERIMENT_SMOKE_TICKERS;scripts/run_scoring_trades_backtest.py:SAMPLE_TICKERS |
| MDLZ | MDLZ | Consumer Defensive | — | XLP | signal_scanner.py:DEFAULT_WATCHLIST |
| MKC | MKC | Consumer Defensive | — | XLP | signal_scanner.py:DEFAULT_WATCHLIST |
| MO | MO | Consumer Defensive | — | XLP | signal_scanner.py:DEFAULT_WATCHLIST |
| PEP | PEP | Consumer Defensive | — | XLP | signal_scanner.py:DEFAULT_WATCHLIST;watchlist_loader.py:_NASDAQ100_FALLBACK;scripts/analyze_sector_regime.py:DEFAULT_TICKERS;scripts/refresh_prob_rank_dual_run_sample.py:SAMPLE_TICKERS;scripts/refresh_scoring_trade_sample.py:SAMPLE_TICKERS;scripts/run_entry_timing_experiment_scan.py:EXPERIMENT_SMOKE_TICKERS;scripts/run_scoring_trades_backtest.py:SAMPLE_TICKERS |
| PG | PG | Consumer Defensive | — | XLP | signal_scanner.py:DEFAULT_WATCHLIST;scripts/analyze_sector_regime.py:DEFAULT_TICKERS;scripts/refresh_prob_rank_dual_run_sample.py:SAMPLE_TICKERS;scripts/refresh_scoring_trade_sample.py:SAMPLE_TICKERS;scripts/refresh_sec_cache.py:REFRESH_TICKERS;scripts/run_scoring_trades_backtest.py:SAMPLE_TICKERS |
| PM | PM | Consumer Defensive | — | XLP | signal_scanner.py:DEFAULT_WATCHLIST;scripts/refresh_prob_rank_dual_run_sample.py:SAMPLE_TICKERS;scripts/refresh_scoring_trade_sample.py:SAMPLE_TICKERS |
| SJM | SJM | Consumer Defensive | — | XLP | signal_scanner.py:DEFAULT_WATCHLIST |
| STZ | STZ | Consumer Defensive | — | XLP | signal_scanner.py:DEFAULT_WATCHLIST |
| TAP | TAP | Consumer Defensive | — | XLP | signal_scanner.py:DEFAULT_WATCHLIST |
| UBER | UBER | Technology | XLK | XLI | scripts/run_entry_timing_experiment_scan.py:EXPERIMENT_SMOKE_TICKERS;scripts/run_scoring_trades_backtest.py:SAMPLE_TICKERS |
| WMT | WMT | Consumer Defensive | — | XLP | signal_scanner.py:DEFAULT_WATCHLIST;scripts/analyze_sector_regime.py:DEFAULT_TICKERS;scripts/refresh_prob_rank_dual_run_sample.py:SAMPLE_TICKERS;scripts/refresh_scoring_trade_sample.py:SAMPLE_TICKERS;scripts/run_entry_timing_experiment_scan.py:EXPERIMENT_SMOKE_TICKERS;scripts/run_scoring_trades_backtest.py:SAMPLE_TICKERS;scripts/verify_prob_rank_dashboard_scan.py:DEFAULT_TICKERS |

Nineteen rows are `Consumer Defensive`, which off mode does not map, against a bundled XLP. `UBER` is Technology → XLK against XLI. `DD` is Basic Materials → XLB against XLI (DuPont is in the State Street XLI holdings file retrieved 2026-10-28).

CSV: `schwab_skill/data/sector_map_override_audit.csv`.

