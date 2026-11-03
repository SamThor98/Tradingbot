---
source: schwab_skill/core/alex_strategy.py
created: 2026-08-13
updated: 2026-08-14
tags: [alex, mean-reversion, backtest, research-sleeve, shelved]
---

# Alex Strategy

> Research sleeve labeled from Swing Edge v10: RSI oversold mean reversion with ATR bracket exits. Does **not** change the live Stage 2 scanner.

## Operator decision

**No-go / shelved (2026-08-14).** Do not promote, do not wire into the live scanner, and do not spend more P0 cycles on this sleeve. Code and multi-era artifacts may remain for historical reference only.

## What it is

Alex is a daily-after-close mean-reversion book:

- Enter when RSI(14) ≤ 30 **and** close is ≥ 5% below SMA20
- Exclude Information Technology
- Block all entries when SPY 10-day ROC ≤ −1.0%
- Block Financials / Real Estate when SHY 60-day ROC ≤ −1.5%
- Rank by RSI ascending, SMA drop as tiebreak
- Stop = ATR(14) × 1.5; target = 85% of 2R; RSI ≥ 50 exit; 7-day time stop
- Max 12 positions, 1.1% equity risk, loss-streak + drawdown size tiers
- 7-session cooldown per ticker after exit

Universe: `schwab_skill/universes/alex_strategy.json` (717 tickers, no IT).

## How to run

From `schwab_skill/`:

```bash
python scripts/run_alex_strategy_backtest.py --tickers 25 --start-date 2020-01-01
python scripts/run_alex_strategy_backtest.py --start-date 2020-01-01   # full 717
```

Default fill is `ALEX_ENTRY_MODE=next_open` (honest). `close` matches the doc's optimistic baseline.

## Multi-era (Alex-native, 717 names)

Same five eras as [[backtest]], Schwab-only, **one 12-slot book** (not chunked). From `schwab_skill/`:

```bash
python scripts/run_alex_multi_era_backtest.py --run-tag alex_native_717
```

Artifact: `validation_artifacts/multi_era_backtest_schwab_only_alex_native_717.json`. Gates are PF mean ≥ 1.20 and worst-era PF ≥ 1.00 on equal-weight `profit_factor_net`.

## Honest-test caveats

The source doc screened the 717 names on the same 2020–2026 window used for its reported PF 1.59. Treat that number as in-sample. Use this repo's five-era windows before any promotion claim. Survivorship bias remains: delisted names are absent.

## Related Pages

- [[backtest]] — Stage 2 live-parity harness (Alex is a separate replay)
- [[signal-scanner]] — live pipeline; Alex is not wired into Stage A
- [[adaptive-stops]] — ATR stops on the momentum path (different multiples/exits)

---

*Last compiled: 2026-08-13*
