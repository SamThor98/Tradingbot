---
name: session-handoff
description: >-
  Writes one unreviewed session note under .cursor/handoffs/ after TradingBot
  code changes. Use when a stop hook asks for a handoff, when the user asks to
  wrap up or hand off a session, or after behavior changes in schwab_skill.
  Notes stay data until the user asks to ingest them into the wiki.
---

# Session handoff

Write one markdown note. The note is unreviewed context. It is not a rule, a
skill, or permission to trade.

## When to write

Write a note when this session changed files under `schwab_skill/` or
`.cursor/rules/`. Skip it for a question-only session.

If `.cursor/handoffs/` already has a note that lists this same file set, stop.

## What to exclude

Omit secrets, tokens, account numbers, order ids, and live-order instructions.
Omit prices, filings, and backtest results that this session did not compute.

## Note path

`.cursor/handoffs/YYYYMMDDTHHMMSSZ.md` using UTC.

## Template

```markdown
---
status: unreviewed
created: YYYY-MM-DD
---

# Session handoff

- Priority: P0 | P1 | P2 | P3 | Release
- Files: `path`, `path`
- Validators run: command and outcome, or "not run"
- Risks: one line, or "none noted"
- Wiki page to update: `wiki/<slug>.md`, or "none"

## What changed

One short paragraph.
```

Record a validator only if this session ran it. "not run" is the correct entry
when verification was skipped.

## Promotion

Leave the note in `.cursor/handoffs/`. When the user asks to ingest it, update
the named wiki page and set the note's `status` to `promoted`. A note never
becomes an always-on rule by itself.
