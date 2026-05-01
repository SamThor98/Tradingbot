# AGENTS.md

Operating guide for autonomous coding agents in this repository.

## Repository scope

- Treat `schwab_skill/` as the effective project root.
- Do not edit parent directories unless explicitly asked.
- Never commit secrets, token blobs, cert files, local databases, or runtime caches.

## Default working style

1. **Inspect first**: read relevant files and confirm constraints before editing.
2. **Keep changes narrow**: prefer the smallest coherent change set.
3. **Preserve behavior by default**: only change behavior when the task explicitly requires it.
4. **Verify before handoff**: run the most relevant checks for touched areas.
5. **Report clearly**: include changed files, validation run, and any known risks.

## Environment notes (Cursor Cloud + local)

- Typical path: `/workspace/schwab_skill`
- Use `python3` for all commands (do not assume `python` exists).
- Install missing dev tools from `requirements-dev.txt`.

## Validation commands

### Fast loop (typical dev iteration)

- Lint: `python3 -m ruff check .`
- Format: `python3 -m ruff format .`
- Tests: `python3 -m pytest -q`
- Type checks: `python3 -m mypy tests`
- Hypothesis fixture chain: `python3 scripts/validate_hypothesis_chain.py`

### Broader validation pass

- `python3 scripts/validate_all.py --profile ci`

Use script `--help` flags when narrowing or expanding checks for a task.

## Service run commands

| Service | Command | Notes |
|---|---|---|
| FastAPI dashboard | `python3 -m uvicorn webapp.main:app --reload --port 8000` | Local single-user dashboard (SQLite default). |
| SaaS API | `python3 -m uvicorn webapp.main_saas:app --host 0.0.0.0 --port 8000` | Multi-tenant API; see `docker-compose.saas.yml` for dependencies. |
| Celery workers | `celery -A webapp.tasks worker -Q scan,orders,celery --loglevel=info` | Run in same env as SaaS API. |

## SQLite + Alembic bootstrap gotcha

On a fresh SQLite DB, initial `webapp.main` startup can trigger a migration circular dependency. Before first server boot or full pytest run:

```bash
cd /workspace/schwab_skill
rm -f webapp/webapp.db
python3 -c "from webapp.db import Base, engine; Base.metadata.create_all(bind=engine)"
python3 -m alembic stamp head
```

After that, run `python3 -m pytest -q` normally. If you need a lighter pass, use `python3 -m pytest -q --ignore=tests/test_smoke.py`.

## Credentials and external integrations

Schwab, Discord, Stripe, and similar credentials are not required for lint/test/UI iteration. In credential-free environments, integrations may appear disconnected; that is expected.
