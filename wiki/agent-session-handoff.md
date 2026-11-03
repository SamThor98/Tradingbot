---
source: .cursor/skills/session-handoff/SKILL.md
created: 2026-10-05
updated: 2026-10-10
tags: [agents, handoff, governance]
---

# Agent session handoff

> Unreviewed session notes, plus a read-only signal-edge review, adapted from the ECC operating loop.

## What this is

ECC (Everything Claude Code) separates a short working context from durable notes. TradingBot keeps that split:

- Skills `session-handoff` and `signal-edge-review` are the workflow.
- `.cursor/hooks/session_handoff_stop.py` reminds the agent once per dirty Python change set under `schwab_skill/`.
- `.cursor/handoffs/` holds the notes. They are unreviewed data.
- The wiki is the promotion target. A note becomes compiled truth only when someone ingests it.

The Cursor ECC installer is not part of this setup. Generic ECC skills, instincts, and the memory vault stay out of the repo.

## Loop

1. Implement the change.
2. Verify with the validators the orchestrator already names.
3. For scanner, backtest, scoring, exit, or quality-gate diffs, run the `signal-edge-review` skill against [[signal-scanner]] and the backtest path. Quote `schwab_skill/phase1_progress_signal_gate.json`. Do not invent profit-factor numbers.
4. When the diff queues or scores a [[hypothesis-cards]] card, run `backtest-result-review`. Quote the result file. Leave `promote` false.
5. Write one handoff note. Record only commands that actually ran.
6. On request, fold accepted facts into the wiki page named in the note. See [[governance-source-of-truth]] and [[validation]].

## Hook boundary

The stop hook reads `git status` and a local stamp at `.cursor/handoffs/.last-prompted`. It asks for a note. It does not format, commit, trade, or rewrite rules. If git is unavailable, it lets the session stop. A second stop for the same file set does not ask again.

## Related Pages

- [[governance-source-of-truth]] — rules, skills, and wiki precedence
- [[validation]] — validator pipeline the handoff must cite honestly
- [[signal-scanner]] — pipeline a signal-edge review reads
- [[promotion-playbook]] — OFF → SHADOW → LIVE, blocked on the profit-factor gates
- [[hypothesis-cards]] — Pre-registered backtest the result-review skill quotes

---

*Last compiled: 2026-10-05*
