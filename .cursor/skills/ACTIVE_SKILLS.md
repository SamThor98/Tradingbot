# Active Skills Catalog (TradingBot)

This file defines the **curated operational skill set** for this repository.
Everything else under `.cursor/skills/` is considered vendor/reference content
unless explicitly added here.

## Lifecycle

- `active`: default for daily engineering and operations workflows.
- `candidate`: being evaluated; not default.
- `archived`: historical/reference only.

## Active Skills

- `schwab-api` (`.cursor/skills/schwab-api/SKILL.md`)
  - Canonical Schwab integration architecture (dual OAuth, tokens, data, execution).
- `signal-scanner` (`.cursor/skills/signal-scanner/SKILL.md`)
  - Stage A/B scan pipeline, diagnostics, plugin rollouts.
- `front-end-design` (`.cursor/skills/front-end-design/SKILL.md`)
  - TradingBot dashboard UX redesign guidance.

## Candidate Skills

- `supabase` (`.cursor/skills/supabase/` when present via plugin cache)
  - Use for SaaS DB/auth specific migrations and troubleshooting.
- `supabase-postgres-best-practices`
  - Use for query/index/performance improvements.

## Governance Rules

1. Prefer `active` skills unless a task explicitly requires a `candidate`.
2. Never introduce a second skill with the same `name` frontmatter as an
   existing active skill.
3. For overlapping skills, keep one canonical contract and cross-link others.
4. Treat `awesome-claude-skills` and broad `composio-skills` catalogs as
   vendor/reference; do not assume they are project-approved.
