---
source: n/a (repository governance policy)
created: 2026-04-30
updated: 2026-04-30
tags: [governance, docs, rules, skills]
---

# Governance Source Of Truth

> Precedence and ownership model for rules, wiki pages, and skill contracts.

## Precedence

1. **Executable contracts and validation scripts** (`schwab_skill/scripts/*.py`): enforceable truth.
2. **Repository rules** (`.cursor/rules/*.mdc`): coding/runtime standards for agents.
3. **Canonical skills** (`.cursor/skills/ACTIVE_SKILLS.md` + referenced active skills).
4. **Operational runbooks/wiki** (`wiki/*.md`): context, rationale, and procedures.
5. **Vendor/reference skills** (`.cursor/skills/awesome-claude-skills/**`): non-canonical.

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

## Related Pages

- [[validation]] — full validation pipeline
- [[project-overview]] — system context

---

*Last compiled: 2026-04-30*
