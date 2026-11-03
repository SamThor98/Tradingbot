---
source: schwab_skill/core/hypothesis_card.py
created: 2026-10-10
updated: 2026-10-16
tags: [backtest, hypothesis, agents, validation]
---

# Hypothesis Cards

> One pre-registered change, scored by copying profit-factor numbers out of an artifact.

## What a card is

Cards live in `schwab_skill/research/hypothesis_cards/`. Each card names one `single_change`, floors at least as strict as `signal_edge_ledger.json` `promotion_floors`, and either:

- an **artifact** command on the allowlist (`phase2_edge_audit.py`, `compare_cap37_unsorted_vs_live.py`, the multi-era runner, ablation, or PF robustness), or
- a **queue spec** the weekend supervisor can POST to the local dashboard.

`status: proposed` is scored when an artifact is already on disk. `status: ready` is what the supervisor and `--run` will execute. `status: settled` is a finished queue card: the weekend supervisor writes it when a matching dashboard run is `success` or `failed`, and the planner will not queue it again. `discarded` is ignored.

## What scores them

`python scripts/run_hypothesis_cards.py` writes `schwab_skill/artifacts/hypothesis_cards/<card_id>.json` and `network_status.json` in that same directory. Metrics are copied from the artifact path on the card. `promote` is always false. `network_status.json` counts filed cards, floors cleared, floors missed, queue cards still waiting, and any result whose `promote` is not false. A settled catalog window keeps `clears_numeric_floors` null. The close-of-day loop in [[hypothesis-ledger]] calls the same scorer from `core/research_close.py` and does not start a backtest.

`python scripts/run_hypothesis_cards.py --enqueue-ready` posts ready queue cards to the local dashboard and does not start the catalog sweep. `python scripts/run_hypothesis_cards.py --run --card-id <id>` executes an artifact command, and only when that card is `ready`. The weekend supervisor also prepends ready queue cards and skips a fingerprint already on the dashboard. See [[backtest]].

`python scripts/validate_hypothesis_cards.py` is in `validate_all`. It checks the schema, the one-change rule, the command allowlist, and the floor floor. It does not replay history.

## Review

The `backtest-result-review` skill quotes the result file and the ledger. It does not sign [[promotion-playbook]]. A clearing gate still waits for `python scripts/promotion_ledger.py append`.

Shipped cards on 2026-10-16:

| card_id | Status | Kind | Question |
|---|---|---|---|
| `cap37-capped-replay` | `proposed` | artifact | Capped bare book versus the five-era floors |
| `sort-key-shadow` | `proposed` | artifact | Unsorted cap37 book versus the live top-5 cut |
| `weekly-vcp-score-46` | `proposed` | queue spec | 5y weekly VCP on S&P 1500 with signal score 46. Held 2026-10-16. |

## Related Pages

- [[backtest]] — Replay harness and the weekend queue this card prepends
- [[hypothesis-ledger]] — Live decision ledger scored in the same research close
- [[promotion-playbook]] — Signed OFF → SHADOW → LIVE step a card cannot take
- [[agent-session-handoff]] — Review skill that quotes these result files
- [[validation]] — `validate_hypothesis_cards` in the local profile
- [[autonomous-investment-network]] — Why a card is the only agent-facing research contract
- [[autonomous-investment-network-build-plan]] — Reject-rate report and the settled-card latch

---

*Last compiled: 2026-10-16*
