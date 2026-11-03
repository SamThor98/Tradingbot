---
source: Brain/Strategies/Self-Study.md
created: 2026-04-13
updated: 2026-09-26
tags: [strategy, learning]
---

# Self-Study

> Automated analysis of trade outcomes to learn from real results. Conviction floor is opt-in via a shadow/live ladder — never applied from demo seeds or thin samples.

## How It Works

1. Records filled BUY/SELL prices, computes round-trip returns
2. Analyzes performance by conviction band and sector
3. With **30+** round trips and **8+** samples in a band, may suggest a minimum conviction floor
4. Floor rule: first well-sampled band where shrinkage-adjusted expectancy turns **sustainably** positive (a non-positive well-sampled band below, later well-sampled bands stay positive). All-green books yield **no** floor
5. Scanner apply path (`resolve_self_study_floor`):
   - `SELF_STUDY_MODE=off|shadow|live` (process env overrides `.env`)
   - Legacy `SELF_STUDY_ENABLED=true` maps to **live** only when MODE is unset; missing flag is **off**
   - Refuses the calibration demo seed (`scripts/seed_calibration_demo.py`) and any file with fewer than 30 round trips
   - **shadow**: count `self_study_would_filter` / `self_study_would_filter_cached`; do not drop names
   - **live**: Stage A cached-conviction kill + post-Stage-B filter (`self_study_filtered`)
   - Horizon-only names skip the floor

## Schedule

Runs at 4:00 PM ET via `main.py` scheduler. Manual: `python scripts/run_self_study.py`.

## Key Files

- `schwab_skill/self_study.py` — analysis logic and floor resolution
- `.self_study.json` — persisted results
- `.trade_outcomes.json` — raw trade data

## Related Pages

- [[hypothesis-ledger]] — complementary decision quality tracking
- [[settled-lessons]] — Today card over this floor and the signal-gate label
- [[advisory-model]] — calibration data
- [[system-overview]] — learning loop
- [[feature-flags]] — `SELF_STUDY_MODE`
- [[quality-gates]] — sibling OFF → SHADOW → LIVE ladder

---

*Last compiled: 2026-08-26*
