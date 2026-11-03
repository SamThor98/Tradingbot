---
source: wiki/autonomous-investment-network.md
created: 2026-10-16
updated: 2026-10-16
tags: [runbook, build-plan, research, agents, p0]
---

# Autonomous Investment Network Build Plan

> Build the unattended research loop on hypothesis cards. Do not build a new agent team, and do not let the loop sign live changes.

Feasibility and the authority split are in [[autonomous-investment-network]]. This page is the build order.

**Status:** Phases 0–2 are in as of 2026-10-16. Phase 3 stays closed.
**Live stack:** frozen through 2026-11-07 by [[thirty-day-signal-cut-plan]]. Research cards may run. A new live ranker or plugin may not.

## How to use this plan

1. One phase at a time. A phase is done when its exit criteria pass.
2. Phases 0–2 are the network. Phase 3 is a model that drafts cards, and it stays closed until you ask for it.
3. Every card stays one `single_change`, an allowlisted command, and `promote: false` on the result file.
4. From `schwab_skill/`: `python -m ruff check .` and `python -m pytest -q` on the card tests when Python changes. `python scripts/validate_hypothesis_cards.py` on every card edit.

## What is already built

| Piece | Where | What it does |
| --- | --- | --- |
| Card contract | `core/hypothesis_card.py` | Statuses `proposed`, `ready`, `settled`, `discarded`. One change. Floors at least as strict as the ledger. |
| Scorer | `scripts/run_hypothesis_cards.py` | Copies metrics from an artifact, or enqueues / runs a `ready` card. Result `promote` is false. |
| Weekend queue | `core/weekend_backtest_campaign.py` | Prepends `ready` queue cards. Skips a fingerprint already on the dashboard. Does not place orders. |
| Daily close | `core/research_close.py` | Scores the hypothesis ledger and cards from artifacts already on disk. Does not start a backtest. |

Shipped cards on 2026-10-16:

| card_id | Status | Kind | Next machine action |
| --- | --- | --- | --- |
| `cap37-capped-replay` | `proposed` | artifact | Score from the existing audit file. No new backtest. |
| `sort-key-shadow` | `proposed` | artifact | Score from `cap37_unsorted_vs_live.json`. `decision` is `quote_only`. |
| `weekly-vcp-score-46` | `proposed` | queue spec | Held on 2026-10-16. A weekend supervisor will not prepend it until you set `ready`. |

## Phase 0 — Hold the live stack

**Goal:** The build does not change scan, rank, or orders.

- Leave `SCAN_LIVE_SORT_KEY`, `SIGNAL_TOP_N`, plugin modes, and the `pts_52w` cap alone.
- Do not run `evolve_logic.py --apply`.
- Do not append `scripts/promotion_ledger.py` from this plan.

**Exit criteria**

- [x] No diff under `signal_scanner.py`, `backtest.py`, `core/scoring_rank_v2.py`, exit logic, or quality-gate enforcement.
- [x] `weekly-vcp-score-46` is `proposed`. You chose to hold it on 2026-10-16. Set it to `ready` only when you want that 5y S&P 1500 job queued.

**Depends on:** nothing else. The hold was chosen on 2026-10-16.
**Effort:** done.

## Phase 1 — Reject-rate report

**Goal:** One file that says whether the loop is learning or just filing paper.

`score_all_cards` writes `artifacts/hypothesis_cards/network_status.json` (gitignored with the rest of `artifacts/`). Counts are copied from card JSON and result JSON:

- filed, by status
- scored with an artifact
- numeric floors cleared
- floors missed
- queue cards still waiting
- results whose `promote` field is anything other than false (this count must stay 0)

The daily research close calls `score_all_cards`, so the rollup refreshes at 16:00 New York. Scoring does not start a backtest. The first run on 2026-10-16 scored the two artifact cards from audits already on disk and left `weekly-vcp-score-46` at `not_enqueued`.

**Exit criteria**

- [x] `network_status.json` exists after `python scripts/run_hypothesis_cards.py`.
- [x] `promote_not_false` is 0.
- [x] A unit test covers a clearing card, a missing artifact, and a `ready` queue card with no result yet.
- [x] `python scripts/validate_hypothesis_cards.py` still passes.

**Depends on:** Phase 0.
**Effort:** small (one module, one test, one script hook).

## Phase 2 — Finished cards stay finished

**Goal:** A `ready` queue card cannot re-enter next weekend after its run is on the books.

Before this phase, a `ready` queue card was prepended whenever its fingerprint was absent from the dashboard run list. `settled` is accepted by the loader and ignored by `augment_campaign_plan`. When the weekend supervisor sees a dashboard run with status `success` or `failed` whose spec fingerprint matches a `ready` queue card, it writes `settled` and a result file. Queued and running rows stay `ready`. A catalog window does not set `clears_numeric_floors`. A human may set `discarded` to drop a card without a run. Nothing in this phase writes `.env` or the promotion ledger.

**Exit criteria**

- [x] A `settled` card is not prepended to the campaign plan.
- [x] A `ready` card whose finished run matches its fingerprint becomes `settled` instead of being submitted again.
- [x] Tests cover both cases, including a queued run that must stay `ready`.
- [x] `promote` on the settled result is false.

**Depends on:** Phase 1, so the status change shows up in `network_status.json`.
**Effort:** small (card schema, campaign hook, tests).

## Phase 3 — Drafter (closed)

**Goal:** A model may write the next card file. It may not run commands except through the card runner, and it may not sign a promotion.

Do not start this phase until Phase 1 has a written reject rate and you explicitly ask for a drafter. The draft has to pass `validate_hypothesis_cards.py` before it can be `ready`. Point-in-time membership stays on [[pit-research-platform]]. `PIT_UNIVERSE_PROVIDER` stays `stub` until that path is reviewed, so a drafter must not treat a Wikipedia-now backtest as evidence.

Grok stays `GROK_AGENT_MODE=off`. The dashboard route still injects no code sink.

**Exit criteria**

- [ ] You asked for this phase in a later session.
- [ ] A drafted card that breaks the one-change rule fails validation.
- [ ] No order path, no ledger append, no `.env` write.

**Depends on:** Phases 1 and 2, plus a later request.
**Effort:** not estimated here.

## What this plan will not do

- Place Schwab orders or change live scan behavior.
- Invent strategy families, a new ranker, or a vector memory of chats.
- Turn the weekend catalog sweep into an open knob search. That sweep stays a confirmation queue.
- Promote `PROB_RANK_MODE`, analog overlay, setup confidence, or correlation guard.

## Related Pages

- [[autonomous-investment-network]] — Feasibility and what stays human-signed
- [[hypothesis-cards]] — The file format this plan operates
- [[promotion-playbook]] — The signature this plan never appends
- [[thirty-day-signal-cut-plan]] — Live-stack freeze through 2026-11-07
- [[backtest]] — Allowlisted runners and the weekend queue
- [[pit-research-platform]] — Universe rule for any later drafter

---

*Last compiled: 2026-10-16*
