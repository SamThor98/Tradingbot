# AGENTS.md

This file defines how autonomous coding agents should operate in this repository.

## Repository Scope

- Treat `schwab_skill/` as the repo root.
- Do not modify parent directories.
- Never commit secrets, token files, certs, or local runtime caches.

## Safe Working Rules

- Prefer small, reversible changes grouped by intent.
- Preserve existing behavior unless the task explicitly asks for behavior changes.
- If unexpected unrelated changes appear, stop and ask for direction.
- Never use destructive git commands without explicit approval.

## Standard Delivery Loop

1. **Inspect**: Read relevant files, identify constraints, and confirm assumptions.
2. **Implement**: Make the minimum coherent code/doc/config change.
3. **Verify**: Run `lint`, `test`, and `typecheck` commands when available.
4. **Summarize**: Report changed files, verification results, risks, and next steps.

## Quality Commands

**Fast loop (typical pre-push / IDE):**

- Lint: `python -m ruff check .`
- Format: `python -m ruff format .`
- Test: `python -m pytest -q`
- Typecheck: `python -m mypy .`
- Fixture chain smoke: `python scripts/validate_hypothesis_chain.py`

**Full validation (release / server profile):**

- `python scripts/validate_all.py --profile ci` (runs plugin validators, optional backtest, observability gates, etc.; see script `--help`).

If a tool is not installed, install from `requirements-dev.txt` before rerunning checks.

Frozen Schwab-shaped samples and scanner diagnostics live under `tests/fixtures/` for regression checks; extend `scripts/validate_hypothesis_chain.py` when adding new shapes the pipeline must accept.

## Cursor Cloud specific instructions

All work lives under `schwab_skill/` — run every command from that directory.

### SQLite + Alembic circular-dependency gotcha

`webapp/main.py` calls `Base.metadata.create_all()` then `alembic upgrade head` on import. When the SQLite file doesn't exist yet, the migrations' `batch_alter_table` hits a `CircularDependencyError` (columns `created_at`, `updated_at`, `live_execution_enabled` form a topo-sort cycle). **Workaround before running tests or the dev server for the first time:**

```bash
cd /workspace/schwab_skill
rm -f webapp/webapp.db
python3 -c "from webapp.db import engine, Base; Base.metadata.create_all(bind=engine)"
python3 -c "from alembic.config import Config; from alembic import command; command.stamp(Config('alembic.ini'), 'head')"
```

This creates the schema from the ORM models (which already include all columns) then stamps Alembic to `head` so the migration is never re-run.

### Quick reference (also documented in README and AGENTS.md Quality Commands)

| Action | Command |
|--------|---------|
| Install deps | `pip install -r requirements-dev.txt` |
| Lint | `python3 -m ruff check .` |
| Format | `python3 -m ruff format .` |
| Test | `python3 -m pytest -q` |
| Typecheck | `python3 -m mypy .` |
| Dev server | `python3 -m uvicorn webapp.main:app --reload --port 8000` |

### Notes

- The system has `python3` but no `python` symlink; always use `python3`.
- `mypy` reports ~90 pre-existing type errors (mainly `None`-safety in `forensic_accounting.py` and `signal_scanner.py`); these are known and not blocking.
- External API credentials (Schwab, Discord, OpenAI) are not required for local dev/test; the app and tests run with SQLite and no broker auth.
