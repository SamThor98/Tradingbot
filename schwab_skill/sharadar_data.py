"""Sharadar Bundle snapshots for the research dashboard.

Uses the direct API (``https://api.sharadar.com/v1.0``), not Nasdaq Data Link.
Fail-closed without a key or on HTTP 401/403. Missing fields stay ``None`` —
never filled with zeros. Does not change scan ranking, sizing, or advisory.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from research.pit_protocols import PIT_FUNDAMENTAL_FIELDS

LOG = logging.getLogger(__name__)
SKILL_DIR = Path(__file__).resolve().parent
SHARADAR_CACHE_FILE = ".sharadar_cache.json"
FAILED_PAYLOAD_TTL_HOURS = 0.25
DEFAULT_ART_FIELDS = (
    "ticker,dimension,calendardate,datekey,date,reportperiod,"
    "revenue,revenueusd,netinc,netincusd,equity,equityusd,assets,assetsusd,"
    "ncfo,fcf,eps,epsdil,roe,roa,grossmargin,netmargin,currentratio,de,"
    "debt,debtusd,shareswa,sharesbas,shareswadil,"
    "workingcapital,assetsc,liabilitiesc,liabilities,retearn,"
    "ebit,ebitda,opinc,cor,gp,sgna,receivables,ppnenet,depamor"
)
DEFAULT_DAILY_FIELDS = "ticker,date,marketcap,ev,evebitda,pe,ps,pb"


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        fval = float(value)
    except (TypeError, ValueError):
        return None
    if fval != fval:
        return None
    return fval


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _pick(row: dict[str, Any], *keys: str) -> Any:
    lowered = {str(k).strip().lower(): v for k, v in row.items()}
    for key in keys:
        val = lowered.get(key.lower())
        if val is None:
            continue
        if isinstance(val, str) and not val.strip():
            continue
        return val
    return None


def parse_table_rows(body: Any) -> list[dict[str, Any]]:
    """Normalize Sharadar JSON (list, {data, columns}, or {rows})."""
    if isinstance(body, list):
        return [r for r in body if isinstance(r, dict)]
    if not isinstance(body, dict):
        return []
    data = body.get("data") or body.get("rows") or body.get("result")
    if isinstance(data, list):
        cols = body.get("columns") or body.get("fields")
        if cols and data and isinstance(data[0], (list, tuple)):
            names = [
                str(c).lower() if not isinstance(c, dict) else str(c.get("name") or "").lower() for c in cols
            ]
            out: list[dict[str, Any]] = []
            for raw in data:
                out.append({names[i]: raw[i] for i in range(min(len(names), len(raw)))})
            return out
        return [r for r in data if isinstance(r, dict)]
    return []


def map_fundamentals_row(row: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(row, dict) or not row:
        return {}
    revenue = _safe_float(_pick(row, "revenueusd", "revenue"))
    netinc = _safe_float(_pick(row, "netincusd", "netinc"))
    equity = _safe_float(_pick(row, "equityusd", "equity"))
    assets = _safe_float(_pick(row, "assetsusd", "assets"))
    debt = _safe_float(_pick(row, "debtusd", "debt"))
    mapped = {
        "ticker": _safe_str(_pick(row, "ticker")).upper(),
        "dimension": _safe_str(_pick(row, "dimension")).upper() or "ART",
        "calendardate": _safe_str(_pick(row, "calendardate"))[:10],
        "datekey": _safe_str(_pick(row, "datekey", "date", "lastupdated"))[:10],
        "reportperiod": _safe_str(_pick(row, "reportperiod"))[:10],
        "revenue": revenue,
        "netinc": netinc,
        "equity": equity,
        "assets": assets,
        "ncfo": _safe_float(_pick(row, "ncfo")),
        "fcf": _safe_float(_pick(row, "fcf")),
        "eps": _safe_float(_pick(row, "eps", "epsdil")),
        "epsdil": _safe_float(_pick(row, "epsdil", "eps")),
        "roe": _safe_float(_pick(row, "roe")),
        "roa": _safe_float(_pick(row, "roa")),
        "grossmargin": _safe_float(_pick(row, "grossmargin")),
        "netmargin": _safe_float(_pick(row, "netmargin")),
        "currentratio": _safe_float(_pick(row, "currentratio")),
        "de": _safe_float(_pick(row, "de")),
        "debt": debt,
        "shareswa": _safe_float(_pick(row, "shareswa")),
        "sharesbas": _safe_float(_pick(row, "sharesbas")),
        "shareswadil": _safe_float(_pick(row, "shareswadil")),
        "marketcap": _safe_float(_pick(row, "marketcap")),
        "workingcapital": _safe_float(_pick(row, "workingcapital")),
        "assetsc": _safe_float(_pick(row, "assetsc")),
        "liabilitiesc": _safe_float(_pick(row, "liabilitiesc")),
        "liabilities": _safe_float(_pick(row, "liabilities")),
        "retearn": _safe_float(_pick(row, "retearn")),
        "ebit": _safe_float(_pick(row, "ebit", "opinc")),
        "ebitda": _safe_float(_pick(row, "ebitda")),
        "opinc": _safe_float(_pick(row, "opinc", "ebit")),
        "cor": _safe_float(_pick(row, "cor")),
        "gp": _safe_float(_pick(row, "gp")),
        "sgna": _safe_float(_pick(row, "sgna")),
        "receivables": _safe_float(_pick(row, "receivables")),
        "ppnenet": _safe_float(_pick(row, "ppnenet")),
        "depamor": _safe_float(_pick(row, "depamor")),
    }
    return mapped


def art_row_asof(rows: list[dict[str, Any]], asof: str) -> dict[str, Any]:
    """Latest as-reported TTM row with filing date (datekey) on/before ``asof``."""
    asof_s = str(asof or "")[:10]
    eligible: list[dict[str, Any]] = []
    for raw in rows:
        mapped = map_fundamentals_row(raw)
        if not mapped:
            continue
        pit_date = mapped.get("datekey") or mapped.get("calendardate") or ""
        if asof_s and pit_date and pit_date > asof_s:
            continue
        eligible.append(mapped)
    eligible.sort(key=lambda r: (r.get("datekey") or "", r.get("calendardate") or ""), reverse=True)
    return dict(eligible[0]) if eligible else {}


def _usd_millions_to_units(value: Any) -> float | None:
    """Sharadar daily EV / market cap are USD millions. Ratios are not scaled."""
    n = _safe_float(value)
    if n is None:
        return None
    return n * 1_000_000.0


def map_daily_row(row: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(row, dict) or not row:
        return {}
    return {
        "ticker": _safe_str(_pick(row, "ticker")).upper(),
        "date": _safe_str(_pick(row, "date"))[:10],
        "marketcap": _usd_millions_to_units(_pick(row, "marketcap")),
        "ev": _usd_millions_to_units(_pick(row, "ev")),
        "evebitda": _safe_float(_pick(row, "evebitda")),
        "pe": _safe_float(_pick(row, "pe")),
        "ps": _safe_float(_pick(row, "ps")),
        "pb": _safe_float(_pick(row, "pb")),
    }


def map_profile_row(row: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(row, dict) or not row:
        return {}
    isdelisted = _pick(row, "isdelisted")
    return {
        "ticker": _safe_str(_pick(row, "ticker")).upper(),
        "name": _safe_str(_pick(row, "name")),
        "exchange": _safe_str(_pick(row, "exchange")),
        "isdelisted": bool(isdelisted) if isdelisted not in (None, "") else None,
        "sector": _safe_str(_pick(row, "sector", "sicsector")),
        "industry": _safe_str(_pick(row, "industry", "sicindustry")),
        "location": _safe_str(_pick(row, "location")),
        "scalemarketcap": _safe_str(_pick(row, "scalemarketcap")),
        "currency": _safe_str(_pick(row, "currency")) or "USD",
        "companysite": _safe_str(_pick(row, "companysite")),
        "firstpricedate": _safe_str(_pick(row, "firstpricedate"))[:10],
        "lastpricedate": _safe_str(_pick(row, "lastpricedate"))[:10],
    }


def map_insider_row(row: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(row, dict) or not row:
        return {}
    return {
        "filingdate": _safe_str(_pick(row, "filingdate", "date"))[:10],
        "ownername": _safe_str(_pick(row, "ownername", "name")),
        "officertitle": _safe_str(_pick(row, "officertitle", "title")),
        "formtype": _safe_str(_pick(row, "formtype")),
        "transactioncode": _safe_str(_pick(row, "transactioncode", "code")),
        "transactiondate": _safe_str(_pick(row, "transactiondate"))[:10],
        "transactionshares": _safe_float(_pick(row, "transactionshares", "shares")),
        "transactionprice": _safe_float(_pick(row, "transactionprice", "transactionpricepershare", "price")),
        "transactionvalue": _safe_float(_pick(row, "transactionvalue", "value")),
        "sharesownedafter": _safe_float(_pick(row, "sharesownedafter", "sharesowned")),
    }


def map_holder_row(row: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(row, dict) or not row:
        return {}
    return {
        "calendardate": _safe_str(_pick(row, "calendardate", "date"))[:10],
        "name": _safe_str(_pick(row, "investorname", "holdername", "name", "investor", "investorid")),
        "units": _safe_float(_pick(row, "units", "shares", "shrheld")),
        "value": _safe_float(_pick(row, "value")),
        "percent": _safe_float(
            _pick(row, "percent", "percentoftotal", "shrholdings", "sharesoutstandingpercent")
        ),
        "securitytype": _safe_str(_pick(row, "securitytype")),
    }


def map_event_row(row: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(row, dict) or not row:
        return {}
    return {
        "date": _safe_str(_pick(row, "date"))[:10],
        "event": _safe_str(_pick(row, "event", "action", "eventcodes", "name")),
        "value": _safe_float(_pick(row, "value")),
        "ticker": _safe_str(_pick(row, "ticker")).upper(),
    }


def is_sharadar_usable(payload: dict[str, Any] | None) -> bool:
    """True when ART/daily may overlay research surfaces (ok or degraded)."""
    if not isinstance(payload, dict):
        return False
    if payload.get("enabled") is False:
        return False
    status = str(payload.get("status") or "").strip().lower()
    return status in {"ok", "degraded"}


def pit_fields_from_parts(
    *,
    daily: dict[str, Any],
    fundamentals: dict[str, Any],
    holders: list[dict[str, Any]],
) -> dict[str, float | None]:
    """Map Bundle tables onto PIT registry fields. Unknown stays None."""
    out: dict[str, float | None] = {k: None for k in PIT_FUNDAMENTAL_FIELDS}
    mcap = _safe_float(daily.get("marketcap"))
    if mcap is None:
        mcap = _safe_float(fundamentals.get("marketcap"))
    out["market_cap"] = mcap
    out["roe"] = _safe_float(fundamentals.get("roe"))
    percents = [h.get("percent") for h in holders if _safe_float(h.get("percent")) is not None]
    if percents:
        # Sum only when Sharadar actually supplied a percent column.
        total = sum(float(p) for p in percents if p is not None)
        out["institutional_ownership"] = total
    return out


def _empty_payload(ticker: str, *, errors: list[str], enabled: bool, status: str = "disabled") -> dict[str, Any]:
    symbol = str(ticker or "").strip().upper()
    return {
        "ok": False,
        "enabled": enabled,
        "status": status,
        "ticker": symbol,
        "as_of": datetime.now(tz=UTC).date().isoformat(),
        "from_cache": False,
        "errors": list(errors),
        "warnings": [],
        "data_quality": errors[0] if errors else status,
        "profile": {},
        "sp500": {"is_member": None, "asof": None},
        "daily": {},
        "fundamentals": {},
        "fundamentals_history": [],
        "insiders": [],
        "holders": [],
        "actions": [],
        "events": [],
        "pit_fields": {k: None for k in PIT_FUNDAMENTAL_FIELDS},
    }


class SharadarClient:
    """Thin REST client for Sharadar tables. Shares no state with Finnhub."""

    def __init__(
        self,
        *,
        api_key: str,
        api_base: str,
        timeout_sec: float = 30.0,
    ) -> None:
        self._api_key = api_key
        self._base = api_base.rstrip("/")
        self._timeout = timeout_sec
        self._lock = threading.Lock()
        self._last = 0.0
        self._min_interval = 0.35

    def _pace(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._min_interval - (now - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()

    def get_table(self, table: str, params: dict[str, Any]) -> tuple[int, Any]:
        import requests

        self._pace()
        query = {k: v for k, v in params.items() if v is not None}
        query["format"] = "json"
        url = f"{self._base}/data/{table}"
        resp = requests.get(
            url,
            params=query,
            headers={"x-api-key": self._api_key, "Accept": "application/json"},
            timeout=self._timeout,
            allow_redirects=True,
        )
        try:
            body: Any = resp.json()
        except Exception:
            body = (resp.text or "")[:240]
        return int(resp.status_code), body

    def rows(self, table: str, params: dict[str, Any]) -> tuple[int, list[dict[str, Any]], str | None]:
        try:
            status, body = self.get_table(table, params)
        except Exception as exc:
            return 0, [], f"sharadar_http_error:{type(exc).__name__}"
        if status == 401:
            return status, [], "sharadar_http_401"
        if status == 403:
            return status, [], "sharadar_http_403_exceeds_free_tier"
        if status != 200:
            return status, [], f"sharadar_http_status_{status}"
        return status, parse_table_rows(body), None


def _cache_path(skill_dir: Path) -> Path:
    return skill_dir / SHARADAR_CACHE_FILE


def _load_cache(skill_dir: Path) -> dict[str, Any]:
    path = _cache_path(skill_dir)
    try:
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        LOG.debug("Sharadar cache read failed: %s", exc)
        return {}


def _save_cache(skill_dir: Path, cache: dict[str, Any]) -> None:
    path = _cache_path(skill_dir)
    try:
        with path.open("w", encoding="utf-8") as fh:
            json.dump(cache, fh, indent=2, sort_keys=True)
    except Exception as exc:
        LOG.debug("Sharadar cache write failed: %s", exc)


def _cached_payload(skill_dir: Path, ticker: str, *, success_ttl_hours: float) -> dict[str, Any] | None:
    cache = _load_cache(skill_dir)
    entry = cache.get(str(ticker).upper())
    if not isinstance(entry, dict):
        return None
    stored_at = float(entry.get("stored_at") or 0.0)
    payload = entry.get("payload")
    if not isinstance(payload, dict) or stored_at <= 0:
        return None
    age_h = (time.time() - stored_at) / 3600.0
    ttl = success_ttl_hours if payload.get("ok") else FAILED_PAYLOAD_TTL_HOURS
    if age_h > ttl:
        return None
    out = dict(payload)
    out["from_cache"] = True
    return out


def _remember_payload(skill_dir: Path, ticker: str, payload: dict[str, Any]) -> None:
    cache = _load_cache(skill_dir)
    cache[str(ticker).upper()] = {"stored_at": time.time(), "payload": payload}
    _save_cache(skill_dir, cache)


def sharadar_preflight_item(skill_dir: Path | None = None) -> dict[str, Any]:
    from config import get_sharadar_api_key, get_sharadar_cache_hours, get_sharadar_enabled

    sd = skill_dir or SKILL_DIR
    key = get_sharadar_api_key(sd)
    enabled = get_sharadar_enabled(sd)
    if not key or not enabled:
        return {
            "id": "sharadar",
            "label": "Sharadar Bundle",
            "status": "warn",
            "detail": "SHARADAR_API_KEY missing or SHARADAR_ENABLED=false; PIT fundamentals stay empty.",
        }
    return {
        "id": "sharadar",
        "label": "Sharadar Bundle",
        "status": "ok",
        "detail": (
            f"Configured; cache ttl={get_sharadar_cache_hours(sd):.1f}h. "
            "Research-only — does not change scan ranking."
        ),
    }


def get_sharadar_research_snapshot(
    ticker: str,
    *,
    skill_dir: Path | None = None,
    asof: str | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """Stable dashboard payload. Never raises."""
    from config import (
        get_sharadar_api_base,
        get_sharadar_api_key,
        get_sharadar_cache_enabled,
        get_sharadar_cache_hours,
        get_sharadar_enabled,
        get_sharadar_timeout_sec,
    )

    sd = skill_dir or SKILL_DIR
    symbol = str(ticker or "").strip().upper()
    asof_s = (asof or datetime.now(tz=UTC).date().isoformat())[:10]
    if not get_sharadar_enabled(sd):
        return _empty_payload(symbol, errors=["sharadar_disabled"], enabled=False)
    key = get_sharadar_api_key(sd)
    if not key:
        return _empty_payload(symbol, errors=["missing_sharadar_api_key"], enabled=False)
    if not symbol:
        return _empty_payload("", errors=["invalid_ticker"], enabled=True, status="error")

    cache_enabled = get_sharadar_cache_enabled(sd) and not refresh
    cache_hours = get_sharadar_cache_hours(sd)
    if cache_enabled:
        cached = _cached_payload(sd, symbol, success_ttl_hours=cache_hours)
        if cached is not None:
            return cached

    client = SharadarClient(
        api_key=key,
        api_base=get_sharadar_api_base(sd),
        timeout_sec=get_sharadar_timeout_sec(sd),
    )
    errors: list[str] = []
    warnings: list[str] = []

    def _fetch(table: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        _status, rows, err = client.rows(table, params)
        if err:
            errors.append(f"{table}:{err}")
            LOG.warning("Sharadar %s failed for %s: %s", table, symbol, err)
            return []
        return rows

    profile_rows = _fetch("tickers", {"ticker": symbol, "limit": 1})
    daily_rows = _fetch(
        "daily",
        {
            "ticker": symbol,
            "to": asof_s,
            "sort": "date.desc",
            "limit": 1,
            "fields": DEFAULT_DAILY_FIELDS,
        },
    )
    art_rows = _fetch(
        "fundamentals",
        {
            "ticker": symbol,
            "dimension": "ART",
            "to": asof_s,
            "sort": "date.desc",
            "limit": 8,
            "fields": DEFAULT_ART_FIELDS,
        },
    )
    insider_rows = _fetch(
        "insiders",
        {"ticker": symbol, "sort": "date.desc", "limit": 12},
    )
    holder_summary = _fetch(
        "holdings_ticker",
        {"ticker": symbol, "sort": "date.desc", "limit": 1},
    )
    hold_asof = ""
    if holder_summary:
        hold_asof = _safe_str(_pick(holder_summary[0], "date", "calendardate"))[:10]
    holder_params: dict[str, Any] = {
        "ticker": symbol,
        "securitytype": "SHR",
        "sort": "units.desc",
        "limit": 24,
    }
    if hold_asof:
        holder_params["date"] = hold_asof
    holder_detail = _fetch("holdings", holder_params)
    holder_ids: list[str] = []
    for raw in holder_detail:
        iid = _safe_str(_pick(raw, "investorid"))
        if iid and iid not in holder_ids:
            holder_ids.append(iid)
    investor_names: dict[str, str] = {}
    if holder_ids:
        inv_params: dict[str, Any] = {"investorid": ",".join(holder_ids[:12]), "limit": 48}
        for raw in _fetch("holdings_investor", inv_params):
            iid = _safe_str(_pick(raw, "investorid"))
            name = _safe_str(_pick(raw, "investorname", "name"))
            if iid and name and iid not in investor_names:
                investor_names[iid] = name
    holder_rows = []
    seen_holders: set[str] = set()
    for raw in holder_detail:
        if not isinstance(raw, dict):
            continue
        iid = _safe_str(_pick(raw, "investorid"))
        if iid and iid in seen_holders:
            continue
        if iid:
            seen_holders.add(iid)
        row = dict(raw)
        if iid and investor_names.get(iid):
            row["investorname"] = investor_names[iid]
        holder_rows.append(row)
    if not holder_rows:
        holder_rows = [
            r
            for r in holder_summary
            if isinstance(r, dict) and (_pick(r, "units", "percent", "shrheld") is not None)
        ]
    action_rows = _fetch(
        "actions",
        {"ticker": symbol, "sort": "date.desc", "limit": 8},
    )
    event_rows = _fetch(
        "events",
        {"ticker": symbol, "sort": "date.desc", "limit": 8},
    )
    sp500_rows = _fetch(
        "sp500",
        {"ticker": symbol, "action": "current", "limit": 5, "fields": "date,action,ticker"},
    )

    profile = map_profile_row(profile_rows[0] if profile_rows else None)
    daily = map_daily_row(daily_rows[0] if daily_rows else None)
    fundamentals = art_row_asof(art_rows, asof_s)
    history = []
    for raw in art_rows:
        mapped = map_fundamentals_row(raw)
        if mapped:
            history.append(mapped)
    history.sort(key=lambda r: r.get("datekey") or r.get("calendardate") or "", reverse=True)
    insiders = [r for r in (map_insider_row(x) for x in insider_rows) if r.get("ownername") or r.get("filingdate")]
    holders = [r for r in (map_holder_row(x) for x in holder_rows) if r.get("name")]
    actions = [r for r in (map_event_row(x) for x in action_rows) if r.get("date") or r.get("event")]
    events = [r for r in (map_event_row(x) for x in event_rows) if r.get("date") or r.get("event")]
    sp_member = None
    if errors and any("sp500:" in e for e in errors):
        sp_member = None
    elif sp500_rows:
        sp_member = any(_safe_str(_pick(r, "ticker")).upper() == symbol for r in sp500_rows)
    elif "sp500:sharadar_http_403_exceeds_free_tier" not in errors:
        sp_member = False

    if not daily and not fundamentals and not profile:
        warnings.append("No Sharadar rows returned for this ticker.")

    auth_fail = any("401" in e or "missing_sharadar" in e for e in errors)
    forbidden = any("403" in e for e in errors)
    has_core = bool(daily or fundamentals or profile)
    if auth_fail:
        status = "error"
        ok = False
        dq = "sharadar_http_401"
    elif forbidden and not has_core:
        status = "error"
        ok = False
        dq = "sharadar_http_403_exceeds_free_tier"
    elif errors and has_core:
        status = "degraded"
        ok = True
        dq = "degraded"
    elif has_core:
        status = "ok"
        ok = True
        dq = "ok"
    else:
        status = "error"
        ok = False
        dq = errors[0] if errors else "sharadar_empty"

    payload = {
        "ok": ok,
        "enabled": True,
        "status": status,
        "ticker": symbol,
        "as_of": asof_s,
        "from_cache": False,
        "errors": errors,
        "warnings": warnings,
        "data_quality": dq,
        "profile": profile,
        "sp500": {"is_member": sp_member, "asof": asof_s},
        "daily": daily,
        "fundamentals": fundamentals,
        "fundamentals_history": history[:8],
        "insiders": insiders[:12],
        "holders": holders[:12],
        "actions": actions[:8],
        "events": events[:8],
        "pit_fields": pit_fields_from_parts(daily=daily, fundamentals=fundamentals, holders=holders),
        "tables_used": [
            "tickers",
            "daily",
            "fundamentals",
            "insiders",
            "holdings",
            "holdings_investor",
            "holdings_ticker",
            "actions",
            "events",
            "sp500",
        ],
    }
    if cache_enabled or get_sharadar_cache_enabled(sd):
        _remember_payload(sd, symbol, payload)
    LOG.info("Sharadar snapshot %s status=%s errors=%s", symbol, status, len(errors))
    return payload
