---
name: trade-review-loop
description: >-
  Closed-loop learning: snapshot every trade decision into a decision packet,
  run weekly diagnostics (false positives by regime, edge decay by setup,
  execution drag by condition), and emit advisory tuning proposals for scanner
  weights and guardrail thresholds. Use when working on decision packets,
  trade review, outcome attribution, weight/threshold feedback, or
  core/decision_packet.py / trade_review.py / weight_feedback.py.
---

# Trade Review Loop

## Canonical Contract

Every trade decision becomes a `DecisionPacket` (the unit of post-trade
evaluation). Outcomes are backfilled when trades resolve; the weekly review
aggregates packets into diagnostics; diagnostics produce **advisory** tuning
proposals that humans promote OFF → SHADOW → LIVE. Nothing auto-applies.

Pairs with: [`signal-scanner`] (weights it proposes to tune),
[`execution-quality-lab`] (execution drag), [`decision-card-builder`] (the card
captured in each packet).

## Decision packet lifecycle

```
decision (approve) ──build_packet──> record_packet ──> decision_packets.json
                                                           │
trade resolves ──self-study / digest──> backfill_outcome ─┘
                                                           │
weekly ──load_packets──> trade_review.weekly_report ──> weight_feedback.propose
```

- `DecisionPacket` (in `core/contracts/`) captures decision-time context:
  `regime_state`, `volatility_state`, `setup_type`, `gate_disposition`,
  `policy_id`, `rank_score`, `edge_score`, `p_up_calibrated`, plus a backfilled
  `outcome` (`label`, `realized_return_pct`, `horizon_days`,
  `realized_slippage_bps`).
- Recorded additively in `webapp/main.py` `approve_trade` (guarded; never
  affects the trade). Backfill via `decision_packet.backfill_outcome(...)` from
  the self-study / weekly-digest job once outcomes are known.

## Weekly diagnostics (`core/trade_review.weekly_report`)

| Diagnostic | Grouping | Metric |
|------------|----------|--------|
| false positives by regime | `regime_state` | loss rate among resolved decisions |
| edge decay by setup | `setup_type` | predicted edge − realized return |
| execution drag by condition | `volatility_state` | avg realized slippage bps |

Rates are computed only over **resolved** packets; `coverage_pct` reports how
many are labeled. Wire `weekly_report` into the existing weekly digest job
(`schwab_skill/main.py` scheduler) to ship it to Discord.

## Feedback (`core/weight_feedback.propose`)

Turns the report into proposals (each: `kind`, `target`, `direction`, `scope`,
`evidence`, `confidence`). Requires `>= _MIN_SAMPLES` resolved samples. Examples:
- high regime FP rate → increase `QUALITY_MIN_SIGNAL_SCORE` for that regime
- high edge decay for a setup → decrease that setup's ensemble weight
- high execution drag in a volatility bucket → decrease `EXEC_POLICY_TIGHT_SPREAD_BPS`

**Advisory only.** Apply through config + the OFF → SHADOW → LIVE rollout; never
auto-tune.

## Endpoints

- `GET /api/cockpit/decision-packets?limit=N` — recent packets
- `GET /api/cockpit/review` — weekly diagnostics + `tuning_proposals`

## Adding an outcome backfill source (checklist)

1. When a trade resolves, compute label + realized return/slippage.
2. Call `decision_packet.backfill_outcome(skill_dir, packet_id, label=...)`.
3. Ensure the packet_id is discoverable (store it on the order / self-study row).
4. Confirm `weekly_report` coverage rises; proposals appear once `_MIN_SAMPLES` met.

## Key Files

- `schwab_skill/core/contracts/decision_packet.py` — `DecisionPacket` DTO
- `schwab_skill/core/decision_packet.py` — build / record / load / backfill
- `schwab_skill/core/trade_review.py` — weekly diagnostics
- `schwab_skill/core/weight_feedback.py` — advisory tuning proposals
- `schwab_skill/webapp/main.py` — approve hook + `/api/cockpit/review`, `/decision-packets`
- `schwab_skill/self_study.py`, `schwab_skill/main.py` (weekly digest) — outcome backfill points
