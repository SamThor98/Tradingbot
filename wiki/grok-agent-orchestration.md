---
source: schwab_skill/core/grok_agent.py
created: 2026-10-15
updated: 2026-10-16
tags: [agents, xai, grok, orchestration]
---

# Grok Agent Orchestration

> Grok is a specialist behind `GROK_AGENT_MODE` (default **off**). It does not score scans, place orders, or open pull requests.

## Wire format

Figures below are from the xAI docs fetched 2026-10-15, not from a live probe.

The adapter calls `POST https://api.x.ai/v1/responses` with bearer `XAI_API_KEY` or `MIROFISH_API_KEY`. It never sends `OPENAI_API_KEY`. Allowed hosts are `api.x.ai` and `us.api.x.ai` (the US host is a documented 10% token premium).

xAI returns HTTP 410 for Chat Completions `search_parameters`. Realtime retrieval uses the `web_search` and `x_search` tools. Responses `background` is unsupported, so a deferred dashboard call is the synchronous Responses request.

| Capability | Documented behavior used here |
| --- | --- |
| Model default | `grok-4.7` (`GROK_AGENT_MODEL` overrides the id; list price below applies only to that id) |
| Context | 500,000 tokens; knowledge cutoff May 2026 |
| List price | $2.00 / 1M input, $6.00 / 1M output. Prefer `usage.cost_in_usd_ticks` (10,000,000,000 ticks = $1) |
| Reasoning | `reasoning.effort`: low, medium, high, xhigh. Omit the field to keep the API default |
| Tools | Function tools, plus `web_search` and `x_search` when search is on |
| Structured output | `text.format.type=json_schema`, name `grok_agent_review` |
| Realtime | None unless search is on. Then `web_search` and `x_search` |
| Streaming | `stream=true` SSE |
| Async | Responses `background` is unsupported. Deferred calls wait on the synchronous response |
| Rate limits | grok-4.7 T0–T4: 150/172/208/312/500 RPS and 50M/53M/60M/74M/100M TPM. HTTP 429 backs off |

## Handoff

`core/hybrid_orchestrator.py` walks one order:

user → openai_orchestrator → specialist → grok → cursor → github → validation

- An attached `openai_plan` is marked `planner=openai`. With no plan, a local planner only copies the requested role. That is not an OpenAI Agents SDK call.
- `CONSULT_GROK_TOOL` / `dispatch_orchestrator_tool` is the function an OpenAI orchestrator can register. The tool name is `consult_grok`.
- Cursor and GitHub run only when a caller injects a sink, mode is `live`, and the verdict is `proceed`. The dashboard route injects no sink.
- Validation commands are recorded, not executed. Only the repo's ruff, pytest, and `validate_all.py --profile local --strict --skip-backtest` strings are accepted.

`off` skips the network and blocks the downstream stages. `shadow` calls xAI and records handoffs. `live` makes a `proceed` verdict eligible for an injected sink.

## Control API

Local dashboard (`https://127.0.0.1:8182/?section=grok`):

- Today → Grok review, and **Ask Grok about {ticker}** on the open setup. The form sends the scan row's score, price, stop, volume ratio, and data quality. Live web/X search starts checked for Research and Reasoning.
- `GET /api/agents/grok/capabilities`
- `POST /api/agents/grok` with `execution` of `sync` or `deferred`, and optional `reasoning_effort` of `low`, `medium`, `high`, or `xhigh`

SaaS strategy chat adds the `consult_grok` tool only when `GROK_AGENT_MODE` is `shadow` or `live`. Local strategy chat stays the shortcut panel.

MiroFish keeps its own model routing in `core/llm_endpoint.py`. This agent does not change that default.

## Related Pages

- [[agent-intelligence]] — Scan-time weighting and meta-policy, separate from this agent
- [[plugin-modes]] — off / shadow / live rollout
- [[local-dashboard-endpoints]] — Route catalog
- [[frontend-route-contract]] — `?section=grok` alias
- [[mirofish-engine]] — Existing Grok-backed simulation path
- [[autonomous-investment-network]] — Research-network feasibility; this agent stays off that loop until a card reject rate exists

---

*Last compiled: 2026-10-16*
