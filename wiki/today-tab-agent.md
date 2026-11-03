---
source: schwab_skill/core/today_tab_agent.py
created: 2026-11-03
updated: 2026-11-03
tags: [scanner, prompts, learning]
---

# Today tab agent

> Versions the three MiroFish persona prompts, scores them on labeled decision packets, and keeps every revision pending until you approve it.

The Today scan's LLM prompts are `AGENT_SYSTEMS` in `engine_analysis.py`: `institutional_trend`, `mean_reversion`, and `retail_fomo`. The decision brief on the scan row is a template, not an LLM call, so it is not in the registry. There is no separate Grok review prompt in this tree.

Other tabs reuse `TabAgent` (`prompts`, `score()`, `propose()`, `approve`, `reject`, `shadow`, `rollback`) by adding a subclass and a branch in `get_tab_agent`.

## Turn it on

In `schwab_skill/.env`:

```bash
TAB_AGENT_TODAY_ENABLED=true
TAB_AGENT_MIN_SAMPLES=30
# Optional. A version marked shadow is called beside the live prompt.
# Its votes are stored and scored. They do not change conviction or orders.
TAB_AGENT_SHADOW_ENABLED=false
```

While the flag is off, scans keep the built-in persona text even if the registry has a newer live version.

## Schedule

`python main.py` proposes revisions on **Sunday 18:20 ET**, after the weekly digest. The job only runs when `TAB_AGENT_TODAY_ENABLED=true`. It writes `status=pending` versions. It does not approve them.

If you only run the dashboard, propose from the Today card or:

```bash
python scripts/tab_agent.py propose --tab today
```

## Where to approve

Today tab: https://127.0.0.1:8182/?section=today-agent

Or:

```bash
python scripts/tab_agent.py status --tab today
python scripts/tab_agent.py score --tab today
python scripts/tab_agent.py approve --tab today --version-id <id>
python scripts/tab_agent.py reject --tab today --version-id <id>
python scripts/tab_agent.py shadow --tab today --version-id <id>
python scripts/tab_agent.py rollback --tab today --version-id <id>
```

Rollback restores a retired live version. It does not promote a pending proposal.

## Score

Win rate and average realized return use decision packets whose outcome horizon matches `DECISION_PACKET_HORIZON_DAYS` (default 10) and whose ticker and UTC day match a prompt attribution. Suppress precision uses counterfactual-log rows (`reason` containing suppress, filtered, or blocked) on those same days, and only when the row already carries `forward_return_pct`. A suppress counts as correct when that return is below -0.5%.

Comparisons stay `insufficient_sample` until both sides have at least `TAB_AGENT_MIN_SAMPLES` labeled outcomes. A `better_than_live` flag requires non-overlapping 95% Wilson intervals and a higher average return. `auto_promote` is always false.

## Related Pages

- [[mirofish-engine]] — the three persona prompts this agent versions
- [[decision-packet-outcomes]] — labeled packets the score reads
- [[signal-scanner]] — Stage B calls MiroFish during a scan
- [[feature-flags]] — `TAB_AGENT_*` switches
- [[frontend-route-contract]] — `?section=today-agent`
- [[debate-tab-agent]] — same lifecycle for the debate committee

---

*Last compiled: 2026-11-03*
