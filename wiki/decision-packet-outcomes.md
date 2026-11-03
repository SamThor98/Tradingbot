---
source: schwab_skill/core/outcome_backfill.py
created: 2026-11-03
updated: 2026-11-03
tags: [scanner, learning, outcomes]
---

# Decision packet outcomes

> Kept scan signals and approved orders are snapshotted into `decision_packets.json` and later labeled win, loss, or scratch from the forward close.

## What stalled

Packets were appended only after a live order was approved. Outcome labeling ran only inside `python main.py` on Sunday at 18:00 and 18:15 ET, plus a manual cockpit button. The operator dashboard, which is what writes `scan_results`, never did either step. Scans and the counterfactual log kept growing while the packet file stayed at its last approval write.

## When it runs

- Dashboard `POST /api/scan` (async worker and sync) and the bot's 09:30 ET `run_scan_and_notify` record one `kind="staged"` packet per kept signal per ticker per UTC day, then label matured pending packets.
- `main.py` labels again at 16:00 ET (self-study) and on the Sunday digest. Labeling is once per UTC day unless forced.
- `POST /api/cockpit/review/backfill` always forces a label pass.
- Filled orders still record `kind="approved"` from the existing approval path.

Filtered shortlist names stay in `.counterfactual_log.jsonl`. They are not decision packets, so the false-positive report is not filled with names the scanner already dropped.

The horizon is `DECISION_PACKET_HORIZON_DAYS` (default 10 trading days, the horizon already used on the labeled book). History is loaded back far enough to include the oldest pending decision. A price window that starts more than five calendar days after the decision is left pending instead of labeled off the wrong bar.

## Catch-up

From `schwab_skill/`, after the file has been frozen:

```bash
python scripts/backfill_decision_outcomes.py --since 2026-09-03
```

That imports `scan_results` rows still in `webapp/webapp.db` (local user) since 2026-09-03 and labels every pending packet, including ones older than that date. Rows already pruned by `WEB_LOCAL_SCAN_RESULT_RETENTION_DAYS` (default 30) cannot be rebuilt.

## Related Pages

- [[signal-scanner]] — source of kept signals
- [[self-study]] — 16:00 ET slot that also labels packets
- [[hypothesis-ledger]] — separate decision-quality ledger
- [[feature-flags]] — `DECISION_PACKET_*` env vars
- [[local-dashboard-endpoints]] — cockpit review and backfill routes
- [[today-tab-agent]] — grades persona prompts on these labeled packets

---

*Last compiled: 2026-11-03*
