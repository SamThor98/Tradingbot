# SaaS deployment notes

## Stack

- **API:** FastAPI `webapp.main_saas:app`
- **Workers:** Celery `webapp.tasks` with queues `scan`, `orders`, and default `celery`
- **Broker / cache:** Redis (`REDIS_URL`)
- **Database:** PostgreSQL recommended (`DATABASE_URL`, e.g. `postgresql+psycopg2://user:pass@host:5432/dbname`)
- **Auth:** Supabase JWT — set `SUPABASE_JWT_SECRET` (HS256)

## Required secrets (API + workers)

| Variable | Purpose |
|----------|---------|
| `CREDENTIAL_ENCRYPTION_KEY` | URL-safe base64, 32 bytes — encrypts rows in `user_credentials` |
| `SUPABASE_JWT_SECRET` | Validates `Authorization: Bearer` tokens |
| `SCHWAB_MARKET_APP_KEY` / `SCHWAB_MARKET_APP_SECRET` | Market API app |
| `SCHWAB_ACCOUNT_APP_KEY` / `SCHWAB_ACCOUNT_APP_SECRET` | Account/trading app |
| `SCHWAB_CALLBACK_URL` | Must match Schwab app registration |
| `DATABASE_URL` | SQLAlchemy URL |
| `REDIS_URL` | Celery + rate limits + scan cooldown |

Optional: `SCHWAB_TOKEN_ENCRYPTION_KEY` — Fernet key for Schwab token files (see `schwab_auth.py`).

## Stripe subscriber billing

Point Stripe’s webhook endpoint at **`POST /api/billing/webhook/stripe`** on your public API URL (same path in test and live dashboards; use separate webhook signing secrets per mode).

| Variable | Purpose |
|----------|---------|
| `STRIPE_SECRET_KEY` | Secret API key (`sk_test_...` / `sk_live_...`) — API + checkout/portal |
| `STRIPE_WEBHOOK_SECRET` | Signing secret from the Stripe webhook endpoint (`whsec_...`) |
| `STRIPE_PRICE_ID` | Recurring **Price** id for Checkout (`price_...`) |
| `STRIPE_CHECKOUT_SUCCESS_URL` | Redirect after successful checkout (if not sent in request body) |
| `STRIPE_CHECKOUT_CANCEL_URL` | Redirect if user cancels checkout |
| `STRIPE_PORTAL_RETURN_URL` | Return URL after Customer Portal (`POST /api/billing/portal-session`) |
| `SAAS_BILLING_ENFORCE` | Set to `1` / `true` to require **`trialing`** or **`active`** subscription for scans, order execution, and position sync (API + Celery workers). Default off for backward compatibility. |

**JWT-authenticated billing routes:** `POST /api/billing/checkout-session` (optional JSON body `success_url`, `cancel_url`), `POST /api/billing/portal-session` (requires existing Stripe customer). **`GET /api/me`** includes `subscription_status`, `subscription_current_period_end`, `has_stripe_customer`, `billing_enforced`, and `subscription_active`.

Workers need the same `SAAS_BILLING_ENFORCE` and database visibility as the API so queued jobs respect cancellation.

## Per-user OAuth

Users POST `/api/credentials/schwab` with:

- `account_oauth_json` — JSON string from the **account** app token response (access + refresh).
- `market_oauth_json` — JSON string from the **market** app token response.

Alternatively: legacy `access_token` + `refresh_token` for the account app **and** set `SAAS_PLATFORM_MARKET_SKILL_DIR` to a directory on the worker/API host that contains a valid `tokens_market.enc` for the market app (shared platform session).

## Migrations

**Existing database** (already had webapp tables):

```bash
cd schwab_skill
alembic upgrade head
```

**Empty Postgres** (first deploy): either run once:

```bash
python scripts/saas_bootstrap.py
```

or set `SAAS_BOOTSTRAP_SCHEMA=1` on the API for a single boot (runs `create_all` + `alembic stamp saas002`), then unset and use `SAAS_RUN_ALEMBIC=1` or manual `alembic upgrade head` for future revisions.

For containers, run `python scripts/saas_bootstrap.py` in an init container or set bootstrap env once on the API process.

## Celery

Workers **must** listen to `scan` and `orders`:

```bash
celery -A webapp.tasks worker -Q scan,orders,celery --loglevel=info
```

## Tunables

| Env | Default | Meaning |
|-----|---------|---------|
| `SAAS_SCAN_COOLDOWN_SEC` | `60` | Min seconds between scan enqueue per user |
| `SAAS_RATE_SCAN_PER_MIN` | `12` | Scans per user per window |
| `SAAS_RATE_ORDER_PER_MIN` | `30` | Order enqueue per user per window |
| `SAAS_RATE_LIMIT_WINDOW_SEC` | `60` | Fixed window for rate limits |
| `SAAS_HEALTH_REQUIRE_REDIS` | `1` | If `0`, readiness skips Redis |
| `WEB_ALLOWED_ORIGINS` | localhost | CORS allowlist (comma-separated) |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` / `DB_POOL_TIMEOUT` | `5` / `10` / `30` | Postgres pool (non-SQLite) |

## Docker

From `schwab_skill/`:

```bash
docker compose -f docker-compose.saas.yml up --build
```

Set secrets via environment file or your host’s secret manager — **never** commit real values.

## Hosting fit

- **Fly.io / Railway / Render:** Docker + managed Postgres + Redis; scale API replicas statelessly; scale Celery processes for queue depth.
- **Supabase:** Use hosted Postgres + Auth; point `DATABASE_URL` and `SUPABASE_JWT_SECRET` at your project.

## Render (Blueprint)

**Two layouts:**

- **Standalone repo:** repository root is the `schwab_skill` folder (`Dockerfile.saas` and `render.yaml` there). Push and connect that repo to Render.
- **Monorepo (e.g. `Tradingbot` on GitHub with a `schwab_skill/` subfolder):** use the `render.yaml` at the **repository root** (it sets `rootDir: schwab_skill` on the Docker services). Connect that repo to Render.

1. Push to GitHub/GitLab/Bitbucket.
2. In [Render](https://dashboard.render.com/): **New** → **Blueprint** → select the repo → apply `render.yaml`.
3. When prompted, set the `sync: false` variables (Schwab, Supabase JWT, encryption key, callback URL, CORS).
4. Set **`WEB_ALLOWED_ORIGINS`** to your public site origin (comma-separated if needed), e.g. `https://<your-web-service>.onrender.com`.
5. **First deploy on an empty database:** either set **`SAAS_BOOTSTRAP_SCHEMA=1`** on the web service for one deploy, then remove it; or run `python scripts/saas_bootstrap.py` against `DATABASE_URL` once. After that, keep **`SAAS_RUN_ALEMBIC=1`** on the web service (already in the Blueprint) so migrations apply on boot, or run `alembic upgrade head` in CI.
6. Register the same **`SCHWAB_CALLBACK_URL`** in the Schwab developer portal as in your environment.
7. Optional Stripe: add the billing env vars from the table above and point Stripe’s webhook to `POST /api/billing/webhook/stripe` on your public API URL.

The API serves the UI at `/` and static assets under `/static`; your live URL is the web service’s HTTPS URL on Render.
