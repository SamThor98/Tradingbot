---
source: Brain/Strategies/PEAD.md
created: 2026-04-13
updated: 2026-10-08
tags: [strategy, earnings]
---

# PEAD

> Post-Earnings Announcement Drift — boosts or penalizes signals based on recent earnings surprises. S1 paper canary is a separate dual-admit sleeve, not a live order path.

## Rationale

Academic research shows stocks drift in the direction of their earnings surprise for weeks after announcement.

## Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `PEAD_ENABLED` | true | Enable PEAD enrichment |
| `PEAD_LOOKBACK_DAYS` | 10 | Recent earnings window |
| `PEAD_SCORE_BOOST` | 3.0 | Boost for positive surprise |
| `PEAD_SCORE_BOOST_LARGE` | 5.0 | Boost for strong positive surprise |
| `PEAD_SCORE_PENALTY` | 3.0 | Penalty for negative surprise |

## S1 paper canary (2026-09-03)

Non-executable dual-admit beside live Stage 2. Capacity arm is `top5_by_edge_score`
(`PEAD_PRIMARY_SHADOW_RANK_TOP_N=5`). Exit research note is grace 15/40 only —
do not transfer the Stage 2 1% breakout buffer. `STRATEGY_PEAD_PRIMARY_ALLOW_LIVE`
stays **false**.

RTH `data_quality=ok` sessions harvest canary names into the hypothesis ledger as
`sleeve_id=S1` / `source=pead_canary`. Each row also stores two observational
flags that do not change membership: extended when `close_vs_sma200_pct` is at
least **0.2028**, and large surprise when the fractional surprise is above
**0.15**. R7 must reach **N ≥ 40 at T+5** with positive expectancy on the full
sleeve before any executable discussion. The same horizon reports three slices
— all five, not extended, surprise above 15% — on the canary card.

```bash
python scripts/apply_pead_canary_paper_env.py
python scripts/run_pead_canary_paper_session.py
```

## Related Pages

- [[signal-ranking]] — PEAD adjustments modify composite score
- [[signal-scanner]] — applied during Stage B enrichment
- [[multi-sleeve-trading-system-constitution]] — S1 PEAD-primary canary sleeve in target architecture
- [[hypothesis-ledger]] — R7 scoring of S1 paper names
- [[scan-catalog]] — PEAD listed as a daily shadow sleeve in Scan studio
- [[horizon-strategies]] — another non-executable dual-admit family (`entry_family=horizon`)
- [[thirty-day-signal-cut-plan]] — October–November paper harvest; N ≥ 40 at T+5 stays the bar

---

*Last compiled: 2026-10-06*
