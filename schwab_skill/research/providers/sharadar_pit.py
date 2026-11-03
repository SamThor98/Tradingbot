"""Sharadar direct API PIT adapter (https://api.sharadar.com/v1.0).

Not Nasdaq Data Link. Fail-closed on missing key, HTTP errors, free-tier 403,
or unusable as-of reconstruction. Does not fall back to yfinance.
Wikipedia-now is fetched separately for the survivorship delta only.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from research.pit_protocols import PIT_FUNDAMENTAL_FIELDS

LOG = logging.getLogger(__name__)

_ADD_ACTIONS = frozenset({"added", "add", "addition"})
_REMOVE_ACTIONS = frozenset({"removed", "remove", "removal", "deletion", "deleted"})


def sp500_members_asof(
    current: set[str],
    events: list[tuple[str, str, str]],
    asof: str,
) -> set[str]:
    """Walk Sharadar SP500 events backward from today to ``asof``.

    ``events`` are ``(date, action, ticker)``. Additions after as-of are dropped;
    removals after as-of are restored. Does not invent names.
    """
    members = {str(t).upper() for t in current if str(t).strip()}
    asof_s = str(asof).strip()[:10]
    later = [(str(d)[:10], str(a).strip().lower(), str(t).upper()) for d, a, t in events if str(d)[:10] > asof_s]
    later.sort(key=lambda row: row[0], reverse=True)
    for _date, action, ticker in later:
        if not ticker:
            continue
        if action in _ADD_ACTIONS:
            members.discard(ticker)
        elif action in _REMOVE_ACTIONS:
            members.add(ticker)
    return members


class SharadarPITAdapter:
    """Live Sharadar client. Empty membership + data_quality error when unavailable."""

    provider_id = "sharadar"

    def __init__(self, *, skill_dir: Path | None = None) -> None:
        self._skill_dir = skill_dir
        self._quality: dict[str, Any] = {
            "status": "unavailable",
            "reason": "not_queried",
            "provider": self.provider_id,
        }

    def _key(self) -> str:
        from config import get_sharadar_api_key

        return get_sharadar_api_key(self._skill_dir)

    def _base(self) -> str:
        from config import get_sharadar_api_base

        return get_sharadar_api_base(self._skill_dir).rstrip("/")

    def _fail(self, reason: str, **extra: Any) -> None:
        payload = {
            "status": "error",
            "reason": reason,
            "provider": self.provider_id,
            "data_quality": reason,
        }
        payload.update(extra)
        self._quality = payload
        LOG.warning("Sharadar PIT fail-closed: %s", reason)

    def _get(self, table: str, params: dict[str, Any]) -> tuple[int, Any]:
        import requests

        key = self._key()
        url = f"{self._base()}/data/{table}"
        query = {k: v for k, v in params.items() if v is not None}
        query["format"] = "json"
        resp = requests.get(
            url,
            params=query,
            headers={"x-api-key": key, "Accept": "application/json"},
            timeout=45,
            allow_redirects=True,
        )
        body: Any
        try:
            body = resp.json()
        except Exception:
            body = (resp.text or "")[:240]
        return int(resp.status_code), body

    def _rows(self, body: Any) -> list[dict[str, Any]]:
        if isinstance(body, list):
            return [r for r in body if isinstance(r, dict)]
        if isinstance(body, dict):
            data = body.get("data") or body.get("rows") or body.get("result")
            if isinstance(data, list):
                cols = body.get("columns") or body.get("fields")
                if cols and data and isinstance(data[0], (list, tuple)):
                    names = [str(c).lower() if not isinstance(c, dict) else str(c.get("name") or "").lower() for c in cols]
                    out: list[dict[str, Any]] = []
                    for raw in data:
                        out.append({names[i]: raw[i] for i in range(min(len(names), len(raw)))})
                    return out
                return [r for r in data if isinstance(r, dict)]
            if body.get("error") or body.get("message"):
                return []
        return []

    def _page(self, table: str, params: dict[str, Any], *, max_pages: int = 20) -> tuple[int, list[dict[str, Any]], str | None]:
        rows: list[dict[str, Any]] = []
        skip = 0
        limit = int(params.get("limit") or 10000)
        last_status = 0
        for _ in range(max_pages):
            page_params = dict(params)
            page_params["limit"] = limit
            page_params["skip"] = skip
            try:
                status, body = self._get(table, page_params)
            except Exception as exc:
                return 0, rows, str(exc)
            last_status = status
            if status == 401:
                return status, rows, "sharadar_http_401"
            if status == 403:
                return status, rows, "sharadar_http_403_exceeds_free_tier"
            if status != 200:
                return status, rows, f"sharadar_http_status_{status}"
            batch = self._rows(body)
            rows.extend(batch)
            if len(batch) < limit:
                break
            skip += limit
        return last_status, rows, None

    def universe_asof(self, asof: str) -> list[str]:
        asof_s = str(asof).strip()[:10]
        if not self._key():
            self._fail("missing_sharadar_api_key", asof=asof_s)
            return []
        # Pin to the latest "current" snapshot date. Omitting `from` defaults to
        # 1 year ago; that can mix older current snapshots into the union.
        try:
            status_p, probe_body = self._get(
                "sp500",
                {
                    "action": "current",
                    "fields": "date,action,ticker",
                    "sort": "date.desc",
                    "limit": 1,
                },
            )
        except Exception as exc:
            self._fail("sharadar_http_error", asof=asof_s, detail=str(exc)[:240])
            return []
        if status_p == 401:
            self._fail("sharadar_http_401", asof=asof_s, http_status=401)
            return []
        if status_p == 403:
            self._fail("sharadar_http_403_exceeds_free_tier", asof=asof_s, http_status=403)
            return []
        if status_p != 200:
            self._fail(f"sharadar_http_status_{status_p}", asof=asof_s, http_status=status_p)
            return []
        probe_rows = self._rows(probe_body)
        latest = str((probe_rows[0].get("date") if probe_rows else "") or "")[:10]
        if len(latest) < 10:
            self._fail("sharadar_sp500_current_date_missing", asof=asof_s)
            return []
        status_c, current_rows, err_c = self._page(
            "sp500",
            {
                "action": "current",
                "fields": "date,action,ticker",
                "from": latest,
                "to": latest,
            },
        )
        if err_c:
            extra: dict[str, Any] = {"asof": asof_s, "http_status": status_c, "current_date": latest}
            self._fail(err_c, **extra)
            return []
        current = {
            str(r.get("ticker") or "").upper()
            for r in current_rows
            if str(r.get("ticker") or "").strip() and str(r.get("date") or "")[:10] == latest
        }
        if len(current) < 50:
            self._fail(
                "sharadar_sp500_current_too_small",
                asof=asof_s,
                n=len(current),
                current_date=latest,
                sample=sorted(current)[:12],
                likely_free_tier_sample=len(current) <= 32,
                hint=(
                    "Free-tier Sharadar keys return a DJIA-sized sample and 403 "
                    "Exceeds free tier on other names. Full sp500 history is on "
                    "paid Fundamentals/Prices/Bundle plans: https://sharadar.com/subscribe"
                ),
            )
            return []
        status_e, event_rows, err_e = self._page(
            "sp500",
            {
                "from": "1998-01-01",
                "to": "2099-01-01",
                "fields": "date,action,ticker",
                "sort": "date.asc",
            },
        )
        if err_e:
            self._fail(err_e, asof=asof_s, http_status=status_e)
            return []
        events: list[tuple[str, str, str]] = []
        known = _ADD_ACTIONS | _REMOVE_ACTIONS
        for r in event_rows:
            action = str(r.get("action") or "").strip().lower()
            if action not in known:
                continue
            events.append((str(r.get("date") or "")[:10], action, str(r.get("ticker") or "").upper()))
        members = sp500_members_asof(current, events, asof_s)
        if len(members) < 50:
            self._fail("sharadar_sp500_asof_too_small", asof=asof_s, n=len(members), n_current=len(current))
            return []
        out = sorted(members)
        self._quality = {
            "status": "ok",
            "reason": "sharadar_sp500_asof",
            "provider": self.provider_id,
            "asof": asof_s,
            "n": len(out),
            "universe_kind": "sp500",
            "n_current": len(current),
            "n_events": len(events),
            "current_date": latest,
            "api_base": self._base(),
        }
        LOG.info("Sharadar SP500 as-of %s: n=%s (current=%s events=%s)", asof_s, len(out), len(current), len(events))
        return out

    def wikipedia_now(self) -> list[str]:
        """Today's Wikipedia S&P 500 (survivorship-biased). Does not clobber PIT data_quality."""
        try:
            from watchlist_loader import _fetch_sp500

            names = [str(t).upper() for t in (_fetch_sp500() or []) if str(t).strip()]
        except Exception as exc:
            LOG.warning("Wikipedia SP500 fetch failed: %s", exc)
            names = []
        if len(names) < 50:
            LOG.warning("Wikipedia SP500 too small: %s", len(names))
        return sorted(set(names))

    def fundamentals_asof(self, ticker: str, asof: str) -> dict[str, float | None]:
        empty = {k: None for k in PIT_FUNDAMENTAL_FIELDS}
        symbol = str(ticker or "").strip().upper()
        asof_s = str(asof)[:10]
        if not self._key():
            self._fail("missing_sharadar_api_key", ticker=symbol, asof=asof_s)
            return empty
        from sharadar_data import (
            DEFAULT_ART_FIELDS,
            DEFAULT_DAILY_FIELDS,
            art_row_asof,
            map_daily_row,
            map_holder_row,
            pit_fields_from_parts,
        )

        status_f, fund_rows, err_f = self._page(
            "fundamentals",
            {
                "ticker": symbol,
                "dimension": "ART",
                "to": asof_s,
                "sort": "date.desc",
                "limit": 8,
                "fields": DEFAULT_ART_FIELDS,
            },
            max_pages=1,
        )
        if err_f:
            self._fail(err_f, ticker=symbol, asof=asof_s, http_status=status_f)
            return empty
        status_d, daily_rows, err_d = self._page(
            "daily",
            {
                "ticker": symbol,
                "to": asof_s,
                "sort": "date.desc",
                "limit": 1,
                "fields": DEFAULT_DAILY_FIELDS,
            },
            max_pages=1,
        )
        if err_d:
            LOG.warning("Sharadar daily as-of failed for %s: %s", symbol, err_d)
        status_h, holder_rows, err_h = self._page(
            "holdings_ticker",
            {"ticker": symbol, "to": asof_s, "sort": "date.desc", "limit": 12},
            max_pages=1,
        )
        if err_h:
            LOG.warning("Sharadar holdings as-of failed for %s: %s", symbol, err_h)
        fundamentals = art_row_asof(fund_rows, asof_s)
        daily = map_daily_row(daily_rows[0] if daily_rows else None)
        holders = [map_holder_row(r) for r in holder_rows]
        fields = pit_fields_from_parts(daily=daily, fundamentals=fundamentals, holders=holders)
        if not any(v is not None for v in fields.values()):
            self._fail("sharadar_fundamentals_empty", ticker=symbol, asof=asof_s)
            return empty
        self._quality = {
            "status": "ok",
            "reason": "sharadar_art_asof",
            "provider": self.provider_id,
            "ticker": symbol,
            "asof": asof_s,
            "datekey": fundamentals.get("datekey"),
            "daily_date": daily.get("date"),
        }
        return fields

    def data_quality(self) -> dict[str, Any]:
        return dict(self._quality)
