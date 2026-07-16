"""Fetch and normalize Schwab account transactions (TRADE type)."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

LOG = logging.getLogger(__name__)

SCHWAB_BASE = "https://api.schwabapi.com"
# Schwab rejects inclusive 365-day spans (start 00:00Z → end 23:59:59Z).
_MAX_WINDOW_DAYS = 364


def _headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }


def _iso_z(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def fetch_trade_transactions(
    *,
    access_token: str,
    account_hash: str,
    start: date,
    end: date,
    symbol: str | None = None,
) -> list[dict[str, Any]]:
    """GET /trader/v1/accounts/{hash}/transactions for TRADE rows.

    Schwab caps the window at ~1 year and requires startDate/endDate/types.
    """
    if end < start:
        start, end = end, start
    if (end - start).days >= _MAX_WINDOW_DAYS + 1:
        start = end - timedelta(days=_MAX_WINDOW_DAYS)

    url = f"{SCHWAB_BASE}/trader/v1/accounts/{account_hash}/transactions"
    start_dt = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    end_dt = datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=timezone.utc)
    params: dict[str, str] = {
        "startDate": _iso_z(start_dt),
        "endDate": _iso_z(end_dt),
        "types": "TRADE",
    }
    if symbol:
        params["symbol"] = symbol.upper().strip()

    resp = requests.get(url, headers=_headers(access_token), params=params, timeout=45)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        for key in ("transactions", "items", "data"):
            inner = data.get(key)
            if isinstance(inner, list):
                return [row for row in inner if isinstance(row, dict)]
    return []


def fetch_trades_for_skill(
    *,
    skill_dir: Path,
    start: date,
    end: date,
    symbol: str | None = None,
    auth: Any | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Resolve local auth + account hash, then fetch TRADE transactions.

    Returns (raw_rows, meta). On failure, rows are empty and meta["error"] is set.
    """
    meta: dict[str, Any] = {
        "source": "schwab",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "error": None,
        "account_hash": None,
        "count": 0,
    }
    try:
        from circuit_breaker import maybe_trip_breaker, schwab_circuit
        from execution import _get_account_hash_for_orders
        from schwab_auth import DualSchwabAuth

        close_auth = False
        auth_obj = auth
        if auth_obj is None:
            auth_obj = DualSchwabAuth(skill_dir=skill_dir, auto_refresh=False)
            close_auth = True
        try:
            token = auth_obj.get_account_token()
            account_hash = _get_account_hash_for_orders(token, skill_dir, auth=auth_obj)
            if not account_hash:
                meta["error"] = "No Schwab account hash. Set SCHWAB_ACCOUNT_HASH or reconnect."
                return [], meta
            meta["account_hash"] = account_hash
            if not schwab_circuit.connection_stable:
                meta["error"] = "Schwab connection unstable (circuit breaker)"
                return [], meta

            def _pull(tok: str) -> list[dict[str, Any]]:
                return fetch_trade_transactions(
                    access_token=tok,
                    account_hash=account_hash,
                    start=start,
                    end=end,
                    symbol=symbol,
                )

            try:
                rows = _pull(token)
            except requests.HTTPError as http_exc:
                maybe_trip_breaker(http_exc, schwab_circuit)
                status = http_exc.response.status_code if http_exc.response is not None else None
                if status == 401 and hasattr(auth_obj, "account_session") and auth_obj.account_session.force_refresh():
                    rows = _pull(auth_obj.get_account_token())
                else:
                    raise
            except Exception as exc:
                maybe_trip_breaker(exc, schwab_circuit)
                raise

            meta["count"] = len(rows)
            return rows, meta
        finally:
            if close_auth and hasattr(auth_obj, "close"):
                try:
                    auth_obj.close()
                except Exception:
                    pass
    except Exception as exc:
        LOG.warning("Schwab transactions fetch failed: %s", exc)
        meta["error"] = str(exc)
        return [], meta
