---
name: backtest-result-review
description: >-
  Scores TradingBot hypothesis cards by copying profit-factor numbers from
  existing backtest artifacts, then reports the gate. Use when a weekend
  backtest campaign drains, when hypothesis cards or result cards are
  mentioned, or when the user asks what the next pre-registered backtest
  should be.
---

# Backtest result review

Read the cards and the result files. Do not edit scanner code, `.env`, or
`scripts/promotion_ledger.jsonl` in this pass. Do not invent prices, filings,
or profit-factor numbers.

## Read first

- `schwab_skill/signal_edge_ledger.json` for `plugin_live` and `promotion_floors`
- `schwab_skill/research/hypothesis_cards/*.json`
- `schwab_skill/artifacts/hypothesis_cards/*.json` after the scorer runs
- `schwab_skill/artifacts/weekend_backtest_campaign/state.json` when it exists

From `schwab_skill/`, refresh result files with:

```bash
python scripts/run_hypothesis_cards.py
```

That command only copies metrics. It does not start a backtest.

`python scripts/run_hypothesis_cards.py --run` executes a command, and only for
a card whose status is `ready` and whose script is on the allowlist. Run one
`--card-id` only when the user explicitly asked to execute that card.

## Rules

- Quote `metrics`, `gate`, `clears_numeric_floors`, and `source_artifact` from
  the result file. If the result says `artifact_missing`, say that. Do not
  fill the number from memory or from the ledger.
- `promote` on a result card stays false. A clearing gate is not a signature.
- Plugin mode changes and `promotion_ledger.py append` wait for the operator
  to name the target and the reason.
- A new idea is one card: one `single_change`, floors at least as strict as
  `promotion_floors`, and either an allowlisted command or one queue spec.
  Leave the file unwritten until the user asks to add that card.
- `python scripts/run_hypothesis_cards.py --enqueue-ready` posts ready queue cards to
https://127.0.0.1:8182 and does not start the catalog sweep. The weekend
supervisor also prepends those cards when it is already running. Do not POST a
second copy of the same fingerprint.

## Report

```markdown
## Backtest result review

- Ledger: `plugin_live` and promotion floors quoted from signal_edge_ledger.json
- Cards: card_id, status, gate, and the metrics copied from each result file
- Queue: waiting / running / remaining from the campaign state, or "state file absent"
- Next card: one proposed single change, or "none"
- Signature: "not requested"
```
