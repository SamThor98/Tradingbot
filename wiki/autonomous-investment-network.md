---
source: chatgpt.com/c/6ad23f4f-d274-83e9-ad12-2cc891707187
created: 2026-10-16
updated: 2026-10-16
tags: [research, agents, promotion, feasibility]
---

# Autonomous investment network

> A continuous research network is feasible as a scheduler over the existing scanner, hypothesis cards, and signed promotion ledger. It does not get a new trading brain, and it does not sign its own promotions.

## Question

The 2026-10-16 ChatGPT thread "Create Investment Network Prompt" asked whether specialized agents can scan names, test them against backtests and guardrails, form strategies, and keep iterating. The reply is a master prompt (Autonomous Investment Intelligence Network) that holds production code until validation rules and safety boundaries exist.

This page is that feasibility pass against the current repo. It does not recompute profit factors.

## Verdict

Unattended research inside a sandbox is already the designed path. Unattended changes to live orders, live sort, risk limits, or plugin mode are refused. Agents may draft a hypothesis. A human signs `scripts/promotion_ledger.py append` before anything becomes live.

`schwab_skill/signal_edge_ledger.json` (updated 2026-10-08) is the scoreboard:

- `plugin_live`: `regime_v2_only`
- Capped book `stage2_pts52w_cap37`: verdict `proceed`, profit-factor mean 1.2137, worst era 1.1054, worst drawdown 15.6099% at 10% size and 100% gross
- `overlay_finding`: `overlays_neutral`
- `SCAN_LIVE_SORT_KEY` stays `signal_score`; `promote_sort` is false
- Floors remain profit-factor mean ≥ 1.20 and worst-era profit factor ≥ 1.00

[[thirty-day-signal-cut-plan]] runs through 2026-11-07. That window locks the live Stage 2 stack and refuses a new ranker. A research network does not reopen that window.

## What already is the network

| Prompt role | Repo counterpart | Authority |
| --- | --- | --- |
| Market discovery | [[signal-scanner]] Stage A → shortlist → Stage B | Emits candidates. Does not place orders. |
| Hypothesis | [[hypothesis-cards]] — one `single_change`, floors at least as strict as the ledger | `promote` is always false on the result file. |
| Backtest | [[backtest]] allowlisted runners only (`phase2_edge_audit.py`, `compare_cap37_unsorted_vs_live.py`, the multi-era runner, ablation, PF robustness) | Param sweeps are not promotion-eligible. See [[pit-research-platform]]. |
| Adversarial review | `scripts/validate_hypothesis_cards.py` plus the signal-edge floors | One change, command allowlist, floor floor. Does not replay history. |
| Research memory | Card artifacts, [[hypothesis-ledger]], counterfactual jsonl, [[settled-lessons]] | Chat transcripts are not memory. |
| Regime | `REGIME_V2_MODE` signed live 2026-10-06 (ledger seq 20) | Does not authorize the next plugin. |
| Risk | [[guardrails]], exec quality, event risk, exit manager | Deterministic limits sit outside any language-model call. |
| Orchestrator | Weekend card queue and `core/research_close.py` | Close scores artifacts. It does not start the catalog sweep. |
| Specialist model | [[grok-agent-orchestration]] `GROK_AGENT_MODE` default **off** | Does not score scans, place orders, or open pull requests. The dashboard route injects no code sink. |

Shipped cards on 2026-10-16 are all `proposed`, including `weekly-vcp-score-46`, which was held so the weekend queue will not prepend that 5y S&P 1500 job. Build order: [[autonomous-investment-network-build-plan]].

## Three kinds of learning

| Kind | What improves | Where it lives | Status |
| --- | --- | --- | --- |
| Research memory | Failed hypotheses are not re-run as if new | Hypothesis cards, ledger, `validation_artifacts/` | Usable now |
| Statistical update | Models refit on new outcomes | [[advisory-model]], [[evolve-logic]] proposals, prob-rank | Shadow or proposal-only. `PROB_RANK_MODE` stays shadow. |
| Recursive self-modification | Agents edit strategy code or `.env` and promote it | `evolve_logic.py --apply`; Grok → Cursor → GitHub only with an injected sink, `live` mode, and verdict `proceed` | Out of the first loop |

[[evolve-logic]] may write `strategy_update.json`. Applying that file is an operator act, then a signed ledger entry. A card result cannot take that step.

## What to automate

The first loop is the weekend card path that already exists. No new agent process.

1. A card names one change, an allowlisted command, and `status: ready`.
2. `python scripts/run_hypothesis_cards.py --enqueue-ready` posts it to the local dashboard and does not start the catalog sweep. `--run --card-id <id>` executes only a `ready` card.
3. `python scripts/run_hypothesis_cards.py` copies profit-factor numbers out of the artifact. `promote` stays false.
4. The backtest-result review quotes that result file and `signal_edge_ledger.json`.
5. A human signs the promotion ledger only when the floors clear and [[thirty-day-signal-cut-plan]] allows that signature.

Success for this loop is a written reject rate: cards filed, cards that clear the floors, cards left unsigned. A clearing backtest is evidence. It is not a promotion.

Point-in-time membership stays on the research platform. Live scans still use Wikipedia's current members. `PIT_UNIVERSE_PROVIDER` stays `stub` until that Sharadar path is reviewed. A discovery agent that backtests today's index members is fitting a survivorship-biased universe.

## What stays human-signed

- Any `*_MODE` change from shadow to live, including `CORRELATION_GUARD_MODE` (still off; the October signature is an operator action).
- `SCAN_LIVE_SORT_KEY`, `SIGNAL_TOP_N`, rank-v2 percentile, and the `pts_52w` cap.
- Schwab order placement: ticker, side, quantity, and order type confirmed by the operator.
- Risk limits, data-quality fail-closed policy, and validation rules.
- `evolve_logic.py --apply` and any pull request that changes live trading logic.
- Sleeve promotion under [[multi-sleeve-trading-system-constitution]] R7 (sleeve-local T+5 N ≥ 40, expectancy > 0, offline floors still required). S1 paper stays non-executable.

Brokerage credentials stay out of agent prompts, logs, and untrusted execution.

## What this pass does not build

- A ninth agent that invents strategy families from scratch. [[signal-edge-ledger]] already records `overlays_neutral` on the capped replay. More personas are not the next experiment.
- Vector-store memory of conversations treated as learning.
- Live `PROB_RANK_MODE`, analog overlay, or setup-confidence sort. Those live switches are refused. See [[feature-flags]] and [[signal-ranking]].
- A new ranker before 2026-11-07.
- Production code for portfolio construction or self-modifying strategy evolution. Those are later stages in the master prompt, and they wait on a measured card reject rate.

## Falsifiers

The premise fails if any of these show up in artifacts, not in debate:

- Pre-registered cards clear in-sample floors and then miss the next unseen era.
- The validated-card rate is no better than the operator filing one card at a time. Extra agents are then a cost center.
- A shadow overlay book stays `overlays_neutral` or loses to the single-variable control, and the network still asks to promote it.
- An empty or thin book is reported as a profit factor. [[phase1_progress_signal_gate.json]] ranks measured samples only. Configs with every era under 50 trades stay in `unranked_insufficient_sample`.

## Later stages

| Stage | Contents | Gate to start |
| --- | --- | --- |
| 1 — now | One ready card, allowlisted backtest, unsigned result | Already built |
| 2 | A model may draft the card text | Only after Stage 1 has a written reject rate, and the draft still has to pass `validate_hypothesis_cards.py` |
| 3 | Scheduled memory and a strategy registry | Same artifacts directory. No new database required for the first dozen cards. |
| 4 | Regime-conditioned evolution and online updates | Separate from the signed regime-v2 gate. `FRED_REGIME_MODE` stays shadow. |
| 5 | Paper, then human-approved live execution | Existing execution and guardrail path. Agents do not gain order authority. |

## Related Pages

- [[hypothesis-cards]] — The artifact a research agent is allowed to file
- [[promotion-playbook]] — The signature an agent cannot append on its own
- [[thirty-day-signal-cut-plan]] — What is frozen through 2026-11-07
- [[grok-agent-orchestration]] — Specialist model, default off, no order sink
- [[pit-research-platform]] — Point-in-time research path that must not replace the Schwab-only runner
- [[evolve-logic]] — Knob proposals that stay review artifacts until signed
- [[signal-scanner]] — Discovery stage this network calls, and does not replace
- [[backtest]] — Allowlisted commands a card may run
- [[autonomous-investment-network-build-plan]] — Phases that implement this verdict

---

*Last compiled: 2026-10-16*
