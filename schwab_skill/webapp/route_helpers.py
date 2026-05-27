"""Shared FastAPI route helpers used by ``webapp.main`` (local),
``webapp.tenant_dashboard`` (SaaS tenant routes), and ``webapp.main_saas``
(SaaS top-level app).

Background
----------
Before this module existed, the same set of small helpers
(``_ok``, ``_err``, ``_apply_profile_to_runtime``, ``_request_origin``,
``_is_loopback_host``, ``_resolve_schwab_redirect_uri``, ``_request_id``,
and the SaaS error-response builder) was copy-pasted across the three
files. That made it easy for the local and SaaS surfaces to drift on
behaviour the frontend depends on (response envelope, redirect URI
rules, request-id propagation).

This module is intentionally narrow: only pure-function helpers that
take primitive arguments or a ``Request``. Anything that needs DB
sessions, Celery, or per-tenant runtime state stays in the caller
(those concerns are not symmetric between local and SaaS).

Companion to ``webapp/_shared.py`` which already extracted the
non-route shared helpers (``trade_to_dict``, ``build_portfolio_summary``,
``quote_health_hint``, ``manual_jwt_entry_enabled``).
"""

from __future__ import annotations

import os
import urllib.parse
from typing import Any

from fastapi import Header, HTTPException, Request

from .preset_catalog import PRESET_PROFILES
from .recovery_map import map_failure
from .redaction import safe_exception_message
from .response_helpers import api_err, api_ok
from .schemas import ApiResponse

# Mirror of the default preset name kept in ``main.py`` / ``tenant_dashboard.py``.
# Defined here (not imported from ``preset_catalog``) because the catalog
# module deliberately stays a flat data dict — the "default" choice belongs
# to the application layer, not the catalog.
DEFAULT_PROFILE = "balanced"


def ok(data: Any = None) -> ApiResponse:
    """Return the standard ``ApiResponse(ok=True, data=...)`` envelope.

    Single source of truth previously duplicated as ``_ok`` in
    ``webapp/main.py``, ``webapp/tenant_dashboard.py``, and
    ``webapp/main_saas.py``. All three definitions delegated (or were
    equivalent) to ``response_helpers.api_ok``; we keep that delegation
    so callers that import this thin wrapper get identical behaviour.
    """
    return api_ok(data)


def simple_err(message: str, data: Any = None) -> ApiResponse:
    """Return a plain ``ApiResponse(ok=False, error=..., data=...)`` envelope.

    Matches the SaaS-style ``_err(message, data)`` previously duplicated
    in ``webapp/tenant_dashboard.py`` and ``webapp/main_saas.py``. The
    local ``webapp.main`` still has its own ``_err(endpoint, exc)``
    wrapper because it additionally records endpoint-level error
    counters and runs ``map_failure`` — that path stays local.
    """
    return api_err(message, data)


def saas_error_response(
    exc: Exception,
    *,
    source: str,
    fallback: str,
) -> ApiResponse:
    """Build the SaaS-style mapped-error envelope used by tenant routes.

    Previously defined as ``_saas_error_response`` inside
    ``webapp/tenant_dashboard.py``. Kept here so SaaS routes elsewhere
    can render the same error shape (``{"recovery": ..., "error_excerpt": ...}``)
    without copying the helper.
    """
    safe = safe_exception_message(exc, fallback=fallback)
    mapped = map_failure(safe, source=source)
    return simple_err(
        fallback,
        {"recovery": mapped, "error_excerpt": mapped.get("raw_error")},
    )


def apply_profile_to_runtime(profile: str) -> dict[str, str]:
    """Activate one of the canned strategy presets in ``os.environ``.

    Centralised so the local dashboard, the SaaS tenant routes, and any
    future call site (e.g. a CLI) all promote the same set of env vars
    in the same order. Returns a copy of the applied payload.
    """
    payload = PRESET_PROFILES.get(profile, PRESET_PROFILES[DEFAULT_PROFILE])
    for key, value in payload.items():
        os.environ[key] = str(value)
    return dict(payload)


def is_loopback_host(hostname: str | None) -> bool:
    """Return True for the standard loopback host names."""
    host = str(hostname or "").strip().lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def request_origin(request: Request) -> str:
    """Resolve the externally-visible origin (``proto://host``) of a request.

    Honours ``X-Forwarded-Proto`` / ``X-Forwarded-Host`` (set by Render's
    proxy) before falling back to the URL the app received. Trailing
    slash stripped so callers can append paths without doubling up.
    """
    proto = (
        request.headers.get("x-forwarded-proto")
        or request.url.scheme
        or "http"
    ).split(",")[0].strip()
    host = (
        request.headers.get("x-forwarded-host")
        or request.url.netloc
        or ""
    ).split(",")[0].strip()
    if host:
        return f"{proto}://{host}".rstrip("/")
    return str(request.base_url).rstrip("/")


def resolve_schwab_redirect_uri(request: Request, *, market: bool) -> str:
    """Pick the right Schwab OAuth callback URL for the current request.

    Behaviour, in order:

    1. If no env override is set, return the inferred URL based on the
       request origin and the canonical callback path.
    2. If the env override points at a loopback host but the request
       arrived from a non-loopback host (typical of hosted SaaS where
       a stale local ``.env`` leaked into the deploy), prefer the
       inferred URL.
    3. Otherwise, honour the env override.

    Combines the slightly-different copies that previously lived in
    ``webapp/main.py`` and ``webapp/tenant_dashboard.py``; the local
    variant did the suffix check and the SaaS variant did the loopback
    swap. Both checks are now applied uniformly.
    """
    env_key = "SCHWAB_MARKET_CALLBACK_URL" if market else "SCHWAB_CALLBACK_URL"
    configured = (os.getenv(env_key) or "").strip()
    suffix = (
        "/api/oauth/schwab/market/callback"
        if market
        else "/api/oauth/schwab/callback"
    )
    inferred = f"{request_origin(request)}{suffix}"
    if not configured:
        return inferred

    parsed = urllib.parse.urlparse(configured)
    configured_host = str(parsed.hostname or "").strip().lower()
    inferred_host = str(
        urllib.parse.urlparse(inferred).hostname or ""
    ).strip().lower()
    if is_loopback_host(configured_host) and not is_loopback_host(inferred_host):
        return inferred
    return configured


def request_id(request: Request) -> str | None:
    """Return the per-request id stamped onto ``request.state`` by the
    request-id middleware (or ``None`` if the middleware didn't run)."""
    return getattr(request.state, "request_id", None)


def env_flag(name: str, default: bool = False) -> bool:
    """Parse standard boolean env values using project-wide conventions."""
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on"}


def require_api_key_if_set(
    request: Request,
    x_api_key: str | None = Header(default=None),
    x_user: str | None = Header(default=None),
) -> dict[str, str]:
    """Shared API-key dependency for local/sensitive web routes.

    Behavior matches existing local semantics:
    - When ``WEB_API_KEY`` is set, require exact ``X-API-Key``.
    - When unset, allow loopback/non-production usage.
    - On Render production-like hosts, require an explicit API key unless
      ``WEB_ALLOW_UNSAFE_LOCAL_WRITES`` is enabled.
    """
    configured = (os.getenv("WEB_API_KEY") or "").strip()
    if not configured:
        if not (os.getenv("RENDER") or "").strip():
            return {"actor": (x_user or "unsafe-local-user").strip() or "unsafe-local-user"}
        env = (os.getenv("ENV") or os.getenv("APP_ENV") or "").strip().lower()
        production_like = env in {"prod", "production", "staging"} or bool((os.getenv("RENDER") or "").strip())
        unsafe = env_flag("WEB_ALLOW_UNSAFE_LOCAL_WRITES", default=False)
        host = (request.url.hostname or "").strip()
        if not host:
            host = str(request.headers.get("host") or "").split(":")[0].strip()
        if not host and request.client is not None:
            host = str(request.client.host or "").strip()
        loopback = is_loopback_host(host)
        if unsafe or (not production_like) or loopback:
            return {"actor": (x_user or "unsafe-local-user").strip() or "unsafe-local-user"}
        raise HTTPException(
            status_code=503,
            detail="WEB_API_KEY is required for write operations. Configure WEB_API_KEY on the server.",
        )
    if not x_api_key or x_api_key != configured:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key.")
    return {"actor": (x_user or "web-user").strip() or "web-user"}
