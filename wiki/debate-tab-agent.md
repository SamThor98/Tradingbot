---
source: schwab_skill/core/debate_committee.py
created: 2026-11-03
updated: 2026-11-03
tags: [scanner, prompts, debate]
---

# Debate tab agent

> Versions the debate committee prompts, grades approve/pass calls on labeled packets, and keeps every revision pending until you approve it.

`core/debate_committee.py` holds `_SYSTEM` and `_prompt()` (bull, bear, chair). There was no committee module in the tree before this; the committee only logs calls. It does not place orders and it does not change scanner conviction.

## Turn it on

In `schwab_skill/.env`:

```bash
TAB_AGENT_DEBATE_ENABLED=true
TAB_AGENT_DEBATE_MAX_NAMES=5
# Extra LLM call per name. Off means those calls are not made.
TAB_AGENT_DEBATE_SHADOW_ENABLED=false
```

While the flag is off, scans do not call the committee.

## Schedule

`python main.py` proposes revisions on **Sunday 18:25 ET**, only when `TAB_AGENT_DEBATE_ENABLED=true`. Proposals stay pending.

On demand:

```bash
python scripts/tab_agent.py propose --tab debate
python scripts/tab_agent.py score --tab debate
python scripts/tab_agent.py approve --tab debate --version-id <id>
python scripts/tab_agent.py rollback --tab debate --version-id <id>
```

Approve on the Today screen next to the Today agent: https://127.0.0.1:8182/?section=debate-agent

## Score and join

Approve calls are scored at `DECISION_PACKET_HORIZON_DAYS` (default 10): win rate, average realized return, and conviction calibration (ECE). Pass calls with a realized return are the counterfactual. Wilson intervals apply. Below `TAB_AGENT_MIN_SAMPLES` (default 30) the verdict is `insufficient_sample`.

Logs join packets on `packet_id` when present, otherwise ticker plus UTC date. New rows store prompt version ids, ticker, date, and packet id. Older rows without `packet_id` only match when that ticker already has a packet that day.

## Related Pages

- [[today-tab-agent]] — the shared TabAgent lifecycle
- [[decision-packet-outcomes]] — labeled outcomes
- [[feature-flags]] — `TAB_AGENT_DEBATE_*`
- [[frontend-route-contract]] — `?section=debate-agent`

---

*Last compiled: 2026-11-03*
