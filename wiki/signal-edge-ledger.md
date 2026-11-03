---
source: schwab_skill/signal_edge_ledger.json
created: 2026-10-08
updated: 2026-10-08
tags: [signal-edge, promotion, ledger]
---

# Signal edge ledger

> The signed scoreboard for whether a book clears the promotion floors. It does not place orders.

## What the file records

`schwab_skill/signal_edge_ledger.json` was last updated 2026-10-08. The floors in that file are profit-factor mean 1.20 and worst-era profit factor 1.00.

`plugin_live` is `regime_v2_only`. The note on that field says the operator signed `REGIME_V2_MODE=live` on 2026-10-06 after the capped `stage2_pts52w_cap37` replay: profit-factor mean 1.2137, worst era 1.1054, worst drawdown 15.6099%. `CORRELATION_GUARD_MODE` stays off. `SCAN_LIVE_SORT_KEY` stays `signal_score`.

The capped replay block quotes `overlay_finding`: `overlays_neutral`. That is the line [[autonomous-investment-network]] points at. The same block's verdict is `proceed`, with 15994 trades, from `validation_artifacts/phase2_edge_audit_cap37_capped.json` generated 2026-10-06. The file says those profit factors were quoted, not recomputed, in that pass.

## How other pages use it

[[hypothesis-cards]] set their floors at least as strict as `promotion_floors`. [[thirty-day-signal-cut-plan]] quotes the same ledger for the drawdown reference and the live-sort lock. [[plugin-modes]] quotes `plugin_live: regime_v2_only`.

## Related Pages

- [[autonomous-investment-network]] — Research network that cites this scoreboard
- [[hypothesis-cards]] — One-change cards bounded by these floors
- [[thirty-day-signal-cut-plan]] — Window that keeps the live Stage 2 stack
- [[plugin-modes]] — What `plugin_live` allows

---

*Last compiled: 2026-11-03*
