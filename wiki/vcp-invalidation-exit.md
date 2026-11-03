---
source: schwab_skill/validation_artifacts/vcp_invalidation_exit.json
created: 2026-09-26
updated: 2026-09-26
tags: [p0, exits, vcp, research]
---

# VCP Invalidation Exit

> One replay: keep the 15/40 grace profile and skip only the VCP-invalidation exit. The live exit path is unchanged.

The September exit replay started its bar window at the entry bar. A VCP check with fewer than 50 rows returns false, so many of those “invalidations” were a short window. This measurement keeps at least 50 pre-entry bars (16,241 of 16,433 `control_legacy_aug` trades).

| Arm | n | PF mean | Worst-era PF |
|---|---:|---:|---:|
| Grace 15/40 | 16,241 | 1.3250 | 1.1283 |
| Grace, skip VCP | 16,241 | 1.3624 | 1.1133 |

Grace still exits 10,151 trades on VCP invalidation. Skipping that exit moves them onto the trailing stop (9,883), SMA50 break (2,670), or the 40-day clock (3,688).

**Verdict: `shadow_ready_exit_not_promoted`.** Mean profit factor clears the +0.03 bar and both floors. Worst-era profit factor falls from 1.1283 to 1.1133. There is no live skip flag. Re-run with `python scripts/analyze_vcp_invalidation_exit.py`.

## Related Pages

- [[vcp-detection]] — the contraction check this exit uses
- [[scan-confidence]] — earlier exit replays on the same book
- [[signal-quality-rollout]] — Stage 2l record
- [[adaptive-stops]] — trailing stop that absorbs most of the skip arm

---

*Last compiled: 2026-09-26*
