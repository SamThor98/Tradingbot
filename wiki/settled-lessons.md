---
source: schwab_skill/webapp/settled_lessons.py
created: 2026-09-26
updated: 2026-09-26
tags: [dashboard, learning, calibration]
---

# Settled Lessons

> Today card that shows what the book has proved and records one operator decision. It does not change settings or place orders.

## What the card shows

1. **What settled.** Round trips against the self-study floor of 30. When advisory or scanner hypotheses are scored at T+5, hit rate is compared with a 50% base rate. Under the floor, or when the calibration demo seed is loaded, the card says still paper and the decision buttons stay off. The PEAD canary stays paper until T+5 N reaches 40.
2. **Pocket.** The conviction band (or sector) with shrinkage-adjusted expectancy, labeled Paper, Shadow, or Blocked. Blocked means the bare-signal profit-factor gates are still closed (mean ≥ 1.20 and worst era ≥ 1.00 on `control_legacy` in `phase1_progress_signal_gate.json`). Shadow is the highest label this card will show.
3. **Proposal.** The first adjustment in `strategy_update.json`, or the first trade-review tuning proposal when that file is empty.

## Decisions

Keep, Sharpen, and Revert append a row to `validation_artifacts/settled_lesson_decisions.jsonl`.

- **Keep** records no change.
- **Sharpen** records that the knob stays in shadow.
- **Revert** discards that proposal on the card. The strategy file itself is left in place for the challenger runner.

None of the three writes `.env`, edits scanner code, or submits an order. Live orders stay on the approve flow.

## API

- `GET /api/settled-lessons`
- `POST /api/settled-lessons/decision` with `{ "action": "keep" | "sharpen" | "revert" }`

Deep link: `?section=lessons` → `#settledLessonsSection` on Today.

## Related Pages

- [[self-study]] — Round-trip floor this card reads
- [[hypothesis-ledger]] — T+5 hit rate
- [[evolve-logic]] — Source of `strategy_update.json`
- [[promotion-playbook]] — The path that actually changes a live flag
- [[frontend-route-contract]] — `lessons` alias
- [[pead]] — Canary sample floor

---

*Last compiled: 2026-09-26*
