---
source: schwab_skill/validation_artifacts/purged_net_rank/counterfactual.json
created: 2026-09-26
updated: 2026-09-26
tags: [p0, ranking, research]
---

# Purged Net-Return Rank

> Ridge fit on realized net return under purged splits with a 40-day embargo. Not a scanner model.

The label is `net_return` after the stop and trail. `ret_*_fwd` and unpurged outputs (`expected_return_40d` and the related expected-downside / expected-PF columns) are refused as inputs. `PROB_RANK_MODE` stays where it is.

Walk-forward 76th percentile on the 16,433-trade control book:

| Arm | n | PF mean | Worst-era PF |
|---|---:|---:|---:|
| Every scored trade | 16,433 | 1.1637 | 1.0322 |
| Purged model | 4,056 | 1.2348 | 0.9589 |
| Inverted `pts_52w` | 3,939 | 1.3862 | 1.0298 |

**Verdict: `do_not_promote_purged_rank`.** The purged model misses the worst-era floor and does not beat inverted `pts_52w`. Re-run with `python scripts/fit_purged_net_rank.py`.

## Related Pages

- [[scan-confidence]] — why forward-return labels do not transfer
- [[signal-ranking]] — live sort remains `signal_score`
- [[signal-quality-rollout]] — Stage 2l record
- [[pit-research-platform]] — purge and embargo rules

---

*Last compiled: 2026-09-26*
