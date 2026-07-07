---
source: n/a (repository governance policy)
created: 2026-04-30
updated: 2026-07-04
tags: [governance, docs, rules, skills]
---

# Governance Source Of Truth

> Precedence and ownership model for rules, wiki pages, and skill contracts.

## Precedence

1. **Executable contracts and validation scripts** (`schwab_skill/scripts/*.py`): enforceable truth.
2. **Repository rules** (`.cursor/rules/*.mdc`): coding/runtime standards for agents.
   - **Strategic orchestrator**: `.cursor/rules/tradingbot-orchestrator.mdc` — north star, priority stack (P0–P3), promotion gates, session workflow (`alwaysApply: true`).
   - **Conventions**: `.cursor/rules/project-conventions.mdc` — architecture, config, plugins, code style.
   - **Scoped rules**: e.g. `webapp-frontend.mdc`, `signal-edge-priority.mdc`, `validation-release.mdc` — activate by file glob when editing those areas.
3. **Canonical skills** (`.cursor/skills/ACTIVE_SKILLS.md` + referenced active skills).
4. **Operational runbooks/wiki** (`wiki/*.md`): context, rationale, and procedures.
5. **Tactical prompts** (`schwab_skill/agent_mode_prompt.md`, feature specs under `schwab_skill/docs/`): module capabilities; reference, do not override validators.
6. **Vendor/reference skills** (`.cursor/skills/awesome-claude-skills/**`): non-canonical.

## Canonical Skill Model

- Canonical Schwab architecture contract: `.cursor/skills/schwab-api/SKILL.md`.
- OpenClaw execution specialization: `schwab_skill/SKILL.md`.
- Skill frontmatter `name:` values must remain unique across active skills.

## Drift Prevention

- Run `python scripts/validate_docs_governance.py` to check:
  - wiki frontmatter completeness
  - broken wikilinks
  - index-orphan pages
  - path consistency in project rules
  - active skill references
  - canonical/extension Schwab skill identity split
- `validate_docs_governance` is included in `scripts/validate_all.py`.

## Agent prompt map

| Prompt / rule | When to use |
|---------------|-------------|
| `tradingbot-orchestrator.mdc` | Every Agent session — prioritization and safety |
| `signal-edge-priority.mdc` | Editing scanner, backtest, scoring, or exit logic (P0) |
| `validation-release.mdc` | Validators, promotion scripts, pre-PR / pre-promotion gate |
| `agent_mode_prompt.md` | Live research, DCF/comps, SEC review, manual scan/order workflows |
| `openclaw_operator_prompt.txt` | OpenClaw tool-only operator (analyze / account / execute) |

## Related Pages

- [[validation]] — full validation pipeline
- [[project-overview]] — system context
- [[signal-scanner]] — two-stage pipeline context for P0 work
- [[quality-gates]] — shadow-first signal filtering rollout

---

*Last compiled: 2026-07-04*
