#!/usr/bin/env python3
"""Shared route inventory for production-safe web stress probes."""

from __future__ import annotations

SAFE_READ_ROUTE_INVENTORY: dict[str, list[str]] = {
    # Public, read-only endpoints that should remain cheap and side-effect free.
    "shared": [
        "/",
        "/simple",
        "/login",
        "/healthz",
        "/api/health",
        "/api/public-config",
        "/api/runtime-contract",
    ],
    # SaaS-only liveness/readiness routes. These can be included in read-only
    # probes as long as worker/redis health requirements are understood.
    "saas_only": [
        "/api/health/live",
        "/api/health/ready",
    ],
}


def safe_read_routes(include_saas: bool = True) -> list[str]:
    routes = list(SAFE_READ_ROUTE_INVENTORY["shared"])
    if include_saas:
        routes.extend(SAFE_READ_ROUTE_INVENTORY["saas_only"])
    return routes
