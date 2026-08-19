"""Cache-busted static-asset helpers.

Browsers cling to cached JS far too aggressively, which means that a stale
``app.js`` from a previous deploy can break the dashboard (silent button
no-ops, init crashes) even after Render has rolled out a fresh build. We
solve that here in two layers:

1. ``render_versioned_html(path)`` reads an HTML file, substitutes
   ``__APP_VERSION__`` with the current deploy version, and returns it with
   ``Cache-Control: no-cache, must-revalidate``. Each deploy bumps the version
   string, so the entry-point ``<script src="/static/app.js?v=...">`` URL
   changes and the browser refetches the bundle.
2. ``NoCacheStaticFiles`` is a thin subclass of ``StaticFiles`` that adds
   ``Cache-Control: no-cache, must-revalidate`` to ``.js``/``.css``/``.html``
   responses. Combined with the ETag / Last-Modified headers the base class
   already emits, browsers issue conditional GETs and pick up new modules
   immediately after deploy (304 when unchanged, 200 when changed).

Version resolution priority:
  - explicit ``APP_VERSION`` env var
  - Render's ``RENDER_GIT_COMMIT``
  - other common deploy SHAs (``GIT_COMMIT``, ``SOURCE_VERSION``)
  - local ``git rev-parse`` (dev mode)
  - boot-time epoch seconds (last resort, still unique per process)
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

LOG = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def app_version() -> str:
    """Stable per-process version string used for cache-busting URLs."""
    for env in ("APP_VERSION", "RENDER_GIT_COMMIT", "GIT_COMMIT", "SOURCE_VERSION"):
        val = (os.getenv(env) or "").strip()
        if val:
            return val[:12]
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short=12", "HEAD"],
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
        sha = out.decode("ascii").strip()
        if sha:
            return sha
    except Exception:
        pass
    return f"t{int(time.time())}"


_HTML_NO_CACHE_HEADERS = {
    "Cache-Control": "no-cache, must-revalidate",
    "Pragma": "no-cache",
}


def render_versioned_html(
    path: Path,
    extra_replacements: dict[str, str] | None = None,
) -> HTMLResponse:
    """Read an HTML file, inject the deploy version, return no-cache HTML.

    The HTML must include ``__APP_VERSION__`` as a literal token in any
    asset URL it wants cache-busted (e.g. ``/static/app.js?v=__APP_VERSION__``).
    Files without the token are still served with no-cache headers so the
    browser always re-evaluates them.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        LOG.warning("render_versioned_html: missing %s", path)
        return HTMLResponse(status_code=404, content="Not Found")
    text = text.replace("__APP_VERSION__", app_version())
    for token, value in (extra_replacements or {}).items():
        text = text.replace(token, value)
    return HTMLResponse(content=text, headers=_HTML_NO_CACHE_HEADERS)


def render_dashboard_html(path: Path) -> HTMLResponse:
    """Dashboard HTML with cache-busted assets plus an inline Scan studio catalog.

    Restarting uvicorn is not enough if the browser keeps a cached ES module
    that never called ``/api/scan-catalog``. Embedding the catalog in the
    no-cache HTML means a refresh after restart paints all sleeves immediately.
    """
    from core.scan_catalog import build_scan_catalog_payload

    payload = json.dumps(build_scan_catalog_payload(), separators=(",", ":"), ensure_ascii=True)
    payload = payload.replace("<", "\\u003c")
    return render_versioned_html(path, extra_replacements={"__SCAN_CATALOG_JSON__": payload})


class NoCacheStaticFiles(StaticFiles):
    """``StaticFiles`` that forces browser revalidation on JS/CSS/HTML.

    Falls back to default behavior for fonts, images, etc. so they keep
    benefiting from heuristic caching.
    """

    _REVALIDATE_SUFFIXES = (".js", ".mjs", ".css", ".html", ".map")

    async def get_response(self, path: str, scope: Any):  # type: ignore[override]
        response = await super().get_response(path, scope)
        if path.lower().endswith(self._REVALIDATE_SUFFIXES):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


__all__ = ["app_version", "render_versioned_html", "render_dashboard_html", "NoCacheStaticFiles"]
