"""
Market data pipeline using Market Session (Schwab OHLCV) with yfinance fallback.

Fetches daily historical data with exponential backoff on HTTP 429.
When PREFER_SCHWAB_DATA=true (default), logs warning when yfinance fallback is used.
Set SCHWAB_ONLY_DATA=true to disable all non-Schwab fallbacks.

Yahoo history honors ``HISTORY_YFINANCE_ADJUSTED`` (default true) so fallback bars
stay on a split/dividend-adjusted basis comparable to typical TA workflows.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from circuit_breaker import maybe_trip_breaker, schwab_circuit
from config import get_schwab_only_data
from schwab_auth import DualSchwabAuth

LOG = logging.getLogger(__name__)

SCHWAB_BASE = "https://api.schwabapi.com"
POLYGON_BASE = "https://api.polygon.io"
MAX_RETRIES = 5
INITIAL_BACKOFF = 1.0
MAX_BACKOFF = 60.0
BACKOFF_MULTIPLIER = 2.0
OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]
SKILL_DIR = Path(__file__).resolve().parent


def _empty_ohlcv() -> pd.DataFrame:
    return pd.DataFrame(columns=OHLCV_COLUMNS).rename_axis("date")


def _get_headers(access_token: str) -> dict:
    return {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}


def _obs_endpoint_label(url: str) -> str:
    """Derive a stable, low-cardinality endpoint label from a Schwab URL."""
    try:
        path = url.split("schwabapi.com", 1)[-1].split("?", 1)[0]
        if "pricehistory" in path:
            return "marketdata.pricehistory"
        if "quotes" in path:
            return "marketdata.quotes"
        if "/orders" in path:
            return "trader.orders"
        if "/accounts" in path:
            return "trader.accounts"
        return path.strip("/")[:60] or "unknown"
    except Exception:
        return "unknown"


def _emit_obs(fn_name: str, *args: Any, **kwargs: Any) -> None:
    """Best-effort observability emit; never affects the request path."""
    try:
        from core import observability

        getattr(observability, fn_name)(*args, **kwargs)
    except Exception:
        pass


class _TokenBucket:
    """Thread-safe token bucket so all scan threads in this process share one
    global Schwab market-data request budget.

    A full-universe scan fans out per-ticker ``pricehistory``/``quotes`` calls
    across worker threads; without a shared limiter the aggregate rate blows
    past Schwab's per-minute cap and every call gets HTTP 429 (then degrades to
    the slower yfinance fallback). Pacing under the cap lets the scan actually
    use Schwab data instead of stampeding into throttling.
    """

    def __init__(self, rate_per_sec: float, capacity: float) -> None:
        self._rate = max(rate_per_sec, 0.0001)
        self._capacity = max(capacity, 1.0)
        self._tokens = self._capacity
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Block until one token is available, refilling by elapsed time."""
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(self._capacity, self._tokens + (now - self._last) * self._rate)
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait_s = (1.0 - self._tokens) / self._rate
            time.sleep(min(max(wait_s, 0.0), 5.0))


def _build_market_rate_limiter() -> "_TokenBucket | None":
    enabled = (os.getenv("SCHWAB_MARKET_RATE_LIMIT_ENABLED", "1") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )
    if not enabled:
        return None

    def _env_float(key: str, default: float) -> float:
        try:
            return float(os.getenv(key, "") or default)
        except (TypeError, ValueError):
            return default

    # Schwab market-data apps are capped near ~120 requests/min; default a bit
    # under that and allow a small burst. Both are env-tunable.
    rpm = max(1.0, _env_float("SCHWAB_MARKET_MAX_RPM", 110.0))
    burst = max(1.0, _env_float("SCHWAB_MARKET_RATE_BURST", 10.0))
    return _TokenBucket(rate_per_sec=rpm / 60.0, capacity=burst)


_MARKET_RATE_LIMITER = _build_market_rate_limiter()


def _request_with_backoff(
    auth: DualSchwabAuth,
    method: str,
    url: str,
    params: dict | None = None,
    **kwargs: Any,
) -> requests.Response:
    # Prevent per-ticker thrashing when DNS/reads are failing.
    if not schwab_circuit.connection_stable:
        _emit_obs("set_circuit_breaker_state", None, "schwab", True)
        raise RuntimeError("Schwab connection unstable (circuit breaker)")

    token = auth.get_market_token()
    kwargs.setdefault("headers", {}).update(_get_headers(token))
    kwargs.setdefault("timeout", 30)
    backoff = INITIAL_BACKOFF
    refreshed_on_401 = False
    endpoint = _obs_endpoint_label(url)
    for attempt in range(MAX_RETRIES):
        # Global pacing: keep the process-wide Schwab request rate under the
        # provider cap so concurrent scan threads don't trigger 429 storms.
        if _MARKET_RATE_LIMITER is not None:
            _MARKET_RATE_LIMITER.acquire()
        _t0 = time.perf_counter()
        try:
            resp = requests.request(method, url, params=params, **kwargs)
        except Exception as e:
            _emit_obs("record_request_latency", None, endpoint, "market", (time.perf_counter() - _t0) * 1000.0)
            _emit_obs("record_request_error", None, endpoint, None)
            maybe_trip_breaker(e, schwab_circuit)
            raise
        _emit_obs("record_request_latency", None, endpoint, "market", (time.perf_counter() - _t0) * 1000.0)
        if resp.status_code >= 400:
            _emit_obs("record_request_error", None, endpoint, resp.status_code)
        if resp.status_code == 401 and not refreshed_on_401:
            if auth.market_session.force_refresh():
                refreshed_on_401 = True
                token = auth.get_market_token()
                kwargs["headers"] = dict(kwargs.get("headers", {}))
                kwargs["headers"].update(_get_headers(token))
                continue
        if resp.status_code == 429 and attempt < MAX_RETRIES - 1:
            time.sleep(backoff)
            backoff = min(backoff * BACKOFF_MULTIPLIER, MAX_BACKOFF)
            continue
        if resp.status_code in (502, 503, 504) and attempt < MAX_RETRIES - 1:
            time.sleep(backoff)
            backoff = min(backoff * BACKOFF_MULTIPLIER, MAX_BACKOFF)
            continue
        return resp
    return resp


def _polygon_api_key() -> str:
    return (os.getenv("POLYGON_API_KEY") or "").strip()


def _get_polygon_quote_fallback(ticker: str) -> tuple[dict | None, dict[str, Any]]:
    key = _polygon_api_key()
    meta: dict[str, Any] = {"provider": "polygon", "reason": None, "http_status": None}
    if not key:
        meta["reason"] = "polygon_api_key_missing"
        return None, meta
    symbol = ticker.upper().strip()
    try:
        trade_url = f"{POLYGON_BASE}/v2/last/trade/{symbol}"
        resp = requests.get(trade_url, params={"apiKey": key}, timeout=8)
        meta["http_status"] = resp.status_code
        if resp.ok:
            body = resp.json()
            px = body.get("results", {}).get("p")
            if px is not None:
                return {"symbol": symbol, "lastPrice": float(px), "source": "polygon"}, meta
        prev_url = f"{POLYGON_BASE}/v2/aggs/ticker/{symbol}/prev"
        prev = requests.get(prev_url, params={"adjusted": "true", "apiKey": key}, timeout=8)
        meta["http_status"] = prev.status_code
        if prev.ok:
            body = prev.json()
            rows = body.get("results") or []
            if rows:
                close_px = rows[0].get("c")
                if close_px is not None:
                    return {"symbol": symbol, "lastPrice": float(close_px), "source": "polygon_prev_close"}, meta
        meta["reason"] = "polygon_no_price"
        return None, meta
    except Exception as exc:
        meta["reason"] = f"polygon_error:{type(exc).__name__}"
        meta["error_detail"] = str(exc)[:220]
        return None, meta


def _maybe_polygon_quote_fallback(ticker: str) -> tuple[dict | None, dict[str, Any]]:
    if get_schwab_only_data():
        return None, {"provider": "schwab", "reason": "schwab_only_data_mode"}
    return _get_polygon_quote_fallback(ticker)


def _coerce_history_bound(value: date | datetime | str | None) -> datetime | None:
    """Normalize optional absolute history bounds to timezone-aware UTC datetimes."""
    if value is None:
        return None
    if isinstance(value, datetime):
        ts = value
    elif isinstance(value, date):
        ts = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    else:
        try:
            parsed = pd.Timestamp(value)
        except Exception:
            return None
        if pd.isna(parsed):
            return None
        ts = parsed.to_pydatetime()
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _get_daily_history_yfinance(
    ticker: str,
    days: int,
    skill_dir: Path | None = None,
    *,
    start_date: date | datetime | str | None = None,
    end_date: date | datetime | str | None = None,
) -> pd.DataFrame:
    """Fallback when Schwab fails (401, etc.). Returns same format as get_daily_history."""
    df, _reason = _get_daily_history_yfinance_with_reason(
        ticker,
        days,
        skill_dir=skill_dir,
        start_date=start_date,
        end_date=end_date,
    )
    return df


def _get_daily_history_yfinance_with_reason(
    ticker: str,
    days: int,
    *,
    skill_dir: Path | None = None,
    start_date: date | datetime | str | None = None,
    end_date: date | datetime | str | None = None,
) -> tuple[pd.DataFrame, str]:
    """Like _get_daily_history_yfinance, but returns explicit reason for empty/missing output."""
    try:
        import yfinance as yf

        from _io_utils import yfinance_call

        start_dt = _coerce_history_bound(start_date)
        end_dt = _coerce_history_bound(end_date)
        with yfinance_call():
            t = yf.Ticker(ticker.upper())
            from config import get_history_yfinance_adjusted

            auto_adj = bool(get_history_yfinance_adjusted(skill_dir))
            if start_dt is not None:
                # yfinance end is exclusive; nudge forward one day to include end_dt.
                yf_end = (end_dt or datetime.now(timezone.utc)) + timedelta(days=1)
                raw = t.history(
                    start=start_dt.date().isoformat(),
                    end=yf_end.date().isoformat(),
                    auto_adjust=auto_adj,
                )
            else:
                period = "2y" if days > 365 else "1y"
                raw = t.history(period=period, auto_adjust=auto_adj)
        if raw is None:
            return _empty_ohlcv(), "yfinance_history_none"
        if not isinstance(raw, pd.DataFrame):
            return _empty_ohlcv(), "yfinance_history_invalid_type"
        if raw.empty:
            return _empty_ohlcv(), "yfinance_history_empty"
        if len(raw) < 2:
            return _empty_ohlcv(), "yfinance_history_too_short"
        df = raw
        df = df.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"})
        df = df[OHLCV_COLUMNS].sort_index().drop_duplicates()
        idx = pd.to_datetime(df.index)
        if getattr(idx, "tz", None) is not None:
            idx = idx.tz_localize(None)
        df.index = idx.normalize()
        df.index.name = "date"
        return df, "yfinance_ok"
    except Exception as exc:
        return _empty_ohlcv(), f"yfinance_exception:{type(exc).__name__}"


def _yf_meta_adjusted_flag(skill_dir: Path | str | None) -> bool:
    try:
        from config import get_history_yfinance_adjusted

        return bool(get_history_yfinance_adjusted(skill_dir))
    except Exception:
        return True


def _yfinance_adjusted_raw_close_gap_pct(ticker: str, *, skill_dir: Path | str | None = None) -> float | None:
    """Diagnostic: on the latest overlapping Yahoo daily bar, |adj−raw|/raw close."""
    try:
        import yfinance as yf

        from _io_utils import yfinance_call

        sym = str(ticker or "").upper().strip()
        if not sym:
            return None
        with yfinance_call():
            t = yf.Ticker(sym)
            adj_df = t.history(period="10d", auto_adjust=True)
            raw_df = t.history(period="10d", auto_adjust=False)
        if adj_df is None or raw_df is None or adj_df.empty or raw_df.empty:
            return None
        adj_c = adj_df["Close"].dropna()
        raw_c = raw_df["Close"].dropna()
        common = adj_c.index.intersection(raw_c.index)
        if len(common) == 0:
            return None
        d = common.max()
        a = float(adj_c.loc[d])
        r = float(raw_c.loc[d])
        if r <= 0 or a != a or r != r:
            return None
        return abs(a - r) / r
    except Exception:
        return None


def get_daily_history_with_meta(
    ticker: str,
    days: int = 300,
    auth: DualSchwabAuth | None = None,
    skill_dir: Path | str | None = None,
    *,
    start_date: date | datetime | str | None = None,
    end_date: date | datetime | str | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """
    Fetch daily OHLCV and return provider lineage metadata.

    When ``start_date`` / ``end_date`` are set they define an absolute window
    (UTC); otherwise the window is ``now - days`` through now.

    Metadata fields:
    - provider: "schwab" or "yfinance"
    - used_fallback: bool
    - fallback_reason: short reason when fallback is used
    - rows: number of rows returned
    - history_price_basis: lineage tag (schwab_vendor_daily / yfinance_adjusted / …)
    - adjusted_vs_raw_close_gap_pct: optional Yahoo QA metric when cross-check is on
    """
    auth = auth or DualSchwabAuth(skill_dir=skill_dir or SKILL_DIR)
    ticker = ticker.upper().strip()
    skill_dir = Path(skill_dir or SKILL_DIR)
    end_dt = _coerce_history_bound(end_date) or datetime.now(timezone.utc)
    start_dt = _coerce_history_bound(start_date) or (end_dt - timedelta(days=days))
    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    url = f"{SCHWAB_BASE}/marketdata/v1/pricehistory"
    params = {
        "symbol": ticker,
        "periodType": "month",
        "frequencyType": "daily",
        "startDate": start_ms,
        "endDate": end_ms,
    }
    meta: dict[str, Any] = {
        "provider": "schwab",
        "used_fallback": False,
        "fallback_reason": None,
        "rows": 0,
        "start_date": start_dt.date().isoformat(),
        "end_date": end_dt.date().isoformat(),
    }

    try:
        resp = _request_with_backoff(auth, "GET", url, params=params)
        resp.raise_for_status()
        data = resp.json()
        candles = data.get("candles")
        if not candles:
            out = _empty_ohlcv()
            meta["rows"] = 0
            return out, meta

        df = pd.DataFrame(candles)
        dt_series = pd.to_datetime(df["datetime"], unit="ms", utc=True) if "datetime" in df.columns else pd.NaT
        required = ["open", "high", "low", "close", "volume"]
        for c in required:
            if c not in df.columns:
                raise ValueError(f"API missing column: {c}")
        df = df[required].copy().astype({c: float for c in required})
        df.index = pd.DatetimeIndex(dt_series).tz_localize(None).normalize()
        df.index.name = "date"
        out = df[OHLCV_COLUMNS].sort_index().drop_duplicates()
        meta["rows"] = int(len(out))
        meta["history_price_basis"] = "schwab_vendor_daily"
        try:
            from config import get_data_crosscheck_enabled, get_history_yfinance_adjusted

            if (
                get_data_crosscheck_enabled(skill_dir)
                and get_history_yfinance_adjusted(skill_dir)
                and not get_schwab_only_data()
            ):
                gap = _yfinance_adjusted_raw_close_gap_pct(ticker, skill_dir=skill_dir)
                if gap is not None:
                    meta["adjusted_vs_raw_close_gap_pct"] = gap
        except Exception as exc:
            LOG.debug(
                "adjusted-vs-raw crosscheck metadata unavailable for %s: %s",
                ticker,
                exc,
            )
        return out, meta
    except Exception as e:
        meta["fallback_reason"] = f"{type(e).__name__}"
        if get_schwab_only_data():
            LOG.warning("Schwab data failed for %s (%s); SCHWAB_ONLY_DATA enabled, no fallback", ticker, e)
            meta["provider"] = "schwab"
            meta["used_fallback"] = False
            meta["rows"] = 0
            return _empty_ohlcv(), meta
        meta["provider"] = "yfinance"
        meta["used_fallback"] = True
        # If the circuit breaker is unstable, we'll very likely hit this path.
        # Keep the fallback behavior safe and non-crashing.
        try:
            from config import get_prefer_schwab_data
            if get_prefer_schwab_data(skill_dir):
                LOG.warning("Schwab data failed for %s (%s), using yfinance fallback", ticker, e)
        except ImportError:
            pass
        out, yf_reason = _get_daily_history_yfinance_with_reason(
            ticker,
            days,
            skill_dir=skill_dir,
            start_date=start_dt,
            end_date=end_dt,
        )
        meta["rows"] = int(len(out))
        meta["fallback_reason"] = f"{meta['fallback_reason']}|{yf_reason}"
        meta["history_price_basis"] = (
            "yfinance_adjusted" if _yf_meta_adjusted_flag(skill_dir) else "yfinance_raw_close"
        )
        _emit_obs("observe_lineage", skill_dir, "market", meta)
        return out, meta


def get_daily_history(
    ticker: str,
    days: int = 300,
    auth: DualSchwabAuth | None = None,
    skill_dir: Path | str | None = None,
    *,
    start_date: date | datetime | str | None = None,
    end_date: date | datetime | str | None = None,
) -> pd.DataFrame:
    """
    Fetch daily OHLCV using Schwab Market Session. Falls back to yfinance on 401/errors.
    When PREFER_SCHWAB_DATA=true, logs warning when fallback is used.
    Returns DataFrame with DatetimeIndex and columns: open, high, low, close, volume.
    """
    df, _meta = get_daily_history_with_meta(
        ticker=ticker,
        days=days,
        auth=auth,
        skill_dir=skill_dir,
        start_date=start_date,
        end_date=end_date,
    )
    return df


# Native Schwab minute frequencies usable for intraday forecasting. 1H/4H are
# intentionally excluded: they are non-native (would need resampling) and too
# sparse within Schwab's ~10 trading-day minute window to forecast credibly.
INTRADAY_INTERVALS: dict[str, int] = {"5m": 5, "15m": 15}


def get_intraday_history_with_meta(
    ticker: str,
    interval: str = "5m",
    days: int = 10,
    auth: DualSchwabAuth | None = None,
    skill_dir: Path | str | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Fetch intraday OHLCV from Schwab (5m/15m only). No yfinance fallback.

    Schwab serves minute history for ~10 trading days, which is dense enough at
    5m (~780 bars) and 15m (~260 bars) to fill the model context. Returns a
    tz-naive (UTC wall-clock) intraday DatetimeIndex so time-of-day is preserved
    for the model's temporal embedding and for charting.

    On any failure this degrades to an empty frame (advisory feature; never
    raises into the request path).
    """
    if interval not in INTRADAY_INTERVALS:
        raise ValueError(f"Unsupported intraday interval: {interval!r} (use 5m or 15m)")
    frequency = INTRADAY_INTERVALS[interval]
    auth = auth or DualSchwabAuth(skill_dir=skill_dir or SKILL_DIR)
    ticker = ticker.upper().strip()
    skill_dir = Path(skill_dir or SKILL_DIR)
    period = max(1, min(10, int(days)))

    url = f"{SCHWAB_BASE}/marketdata/v1/pricehistory"
    params = {
        "symbol": ticker,
        "periodType": "day",
        "period": period,
        "frequencyType": "minute",
        "frequency": frequency,
        "needExtendedHoursData": "false",
    }
    meta: dict[str, Any] = {
        "provider": "schwab",
        "used_fallback": False,
        "fallback_reason": None,
        "rows": 0,
        "interval": interval,
    }

    try:
        resp = _request_with_backoff(auth, "GET", url, params=params)
        resp.raise_for_status()
        data = resp.json()
        candles = data.get("candles")
        if not candles:
            meta["fallback_reason"] = "no_candles"
            return _empty_ohlcv(), meta

        df = pd.DataFrame(candles)
        required = ["open", "high", "low", "close", "volume"]
        for c in required:
            if c not in df.columns:
                raise ValueError(f"API missing column: {c}")
        dt_series = pd.to_datetime(df["datetime"], unit="ms", utc=True)
        df = df[required].copy().astype({c: float for c in required})
        # Keep intraday time-of-day; drop tz so .timestamp() is consistent with
        # the daily path (pandas treats tz-naive as UTC).
        df.index = pd.DatetimeIndex(dt_series).tz_localize(None)
        df.index.name = "datetime"
        out = df[OHLCV_COLUMNS].sort_index().drop_duplicates()
        meta["rows"] = int(len(out))
        meta["history_price_basis"] = f"schwab_vendor_{interval}"
        return out, meta
    except Exception as e:
        meta["fallback_reason"] = f"{type(e).__name__}"
        meta["rows"] = 0
        LOG.warning("Schwab intraday (%s) failed for %s: %s", interval, ticker, e)
        return _empty_ohlcv(), meta


# Quote payload keys we consider "live" (today's print) vs "stale" (prior
# close). Keeping `extract_schwab_last_price` permissive preserves the
# existing call sites that explicitly want a best-effort price for display
# / position marking. New code that drives **decisions** (breakout confirm,
# stop placement, sizing) should use ``extract_schwab_live_price`` so a
# stale ``closePrice`` substitution can't silently anchor a fresh trade.
_LIVE_QUOTE_PATHS: tuple[tuple[str, ...], ...] = (
    ("lastPrice",),
    ("quote", "lastPrice"),
    ("quote", "mark"),
    ("regular", "regularMarketLastPrice"),
    ("extended", "lastPrice"),
    ("extended", "mark"),
)
_PRIOR_CLOSE_QUOTE_PATHS: tuple[tuple[str, ...], ...] = (
    ("quote", "closePrice"),
)


def _extract_quote_path(quote: dict[str, Any] | None, paths: tuple[tuple[str, ...], ...]) -> float | None:
    if not isinstance(quote, dict):
        return None
    for path in paths:
        ptr: Any = quote
        ok = True
        for part in path:
            if not isinstance(ptr, dict) or part not in ptr:
                ok = False
                break
            ptr = ptr[part]
        if ok:
            try:
                value = float(ptr)
                if value > 0:
                    return value
            except (TypeError, ValueError):
                pass
    return None


def extract_schwab_live_price(quote: dict[str, Any] | None) -> float | None:
    """Return ONLY a fresh print (last/mark/regular). Never substitutes prior close.

    Use this on any code path where a stale anchor would corrupt a decision:
    breakout confirmation, stop placement, entry sizing, etc. After-hours
    when no fresh print exists, this returns ``None`` and callers should
    treat the data as stale.
    """
    return _extract_quote_path(quote, _LIVE_QUOTE_PATHS)


def extract_schwab_last_price(quote: dict[str, Any] | None) -> float | None:
    """Best-effort last trade / mark / prior close (flat or nested).

    Falls through to ``closePrice`` (yesterday's close) when no live print is
    available. Acceptable for display, marking-to-market, and position
    monitoring — **not** for decision logic (use ``extract_schwab_live_price``).
    """
    live = _extract_quote_path(quote, _LIVE_QUOTE_PATHS)
    if live is not None:
        return live
    return _extract_quote_path(quote, _PRIOR_CLOSE_QUOTE_PATHS)


def _select_schwab_quote_payload(data: Any, ticker: str) -> dict | None:
    """Pick the per-symbol quote dict from Schwab /marketdata/v1/quotes JSON."""
    t = ticker.upper().strip()
    if isinstance(data, dict):
        if t in data and isinstance(data[t], dict):
            return data[t]
        for k, v in data.items():
            if isinstance(k, str) and k.upper() == t and isinstance(v, dict):
                return v
        sym = data.get("symbol")
        if isinstance(sym, str) and sym.upper() == t:
            return data
        if "lastPrice" in data:
            return data
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and str(item.get("symbol", "")).upper() == t:
                return item
        if data and isinstance(data[0], dict):
            return data[0]
    return None


def get_current_quote_with_status(
    ticker: str,
    auth: DualSchwabAuth | None = None,
    skill_dir: Path | str | None = None,
) -> tuple[dict | None, dict[str, Any]]:
    """
    Fetch quote via Market Session. Returns (quote_dict_or_none, meta) where meta explains failures
    for dashboards and operators (HTTP status, reason codes, key names).
    """
    ticker_u = ticker.upper().strip()
    meta: dict[str, Any] = {
        "symbol": ticker_u,
        "http_status": None,
        "reason": None,
        "top_level_keys": None,
        "quote_keys": None,
        "error_detail": None,
    }
    auth = auth or DualSchwabAuth(skill_dir=skill_dir or SKILL_DIR)
    url = f"{SCHWAB_BASE}/marketdata/v1/quotes"
    try:
        resp = _request_with_backoff(auth, "GET", url, params={"symbols": ticker_u})
        meta["http_status"] = resp.status_code
        try:
            resp.raise_for_status()
        except requests.HTTPError as e:
            meta["reason"] = "http_error"
            try:
                body = (resp.text or "").strip()[:400]
            except Exception:
                body = ""
            meta["error_detail"] = body or str(e)
            LOG.warning(
                "Schwab quotes HTTP %s for %s: %s",
                resp.status_code,
                ticker_u,
                meta["error_detail"],
            )
            fallback_quote, fallback_meta = _maybe_polygon_quote_fallback(ticker_u)
            if fallback_quote is not None:
                meta["fallback_provider"] = fallback_meta.get("provider")
                meta["reason"] = "schwab_fallback_polygon"
                px = extract_schwab_last_price(fallback_quote)
                if px is not None:
                    meta["last_price"] = round(px, 6)
                return fallback_quote, meta
            return None, meta
        data = resp.json()
        if isinstance(data, dict):
            meta["top_level_keys"] = sorted(str(k) for k in data.keys())[:32]
        quote = _select_schwab_quote_payload(data, ticker_u)
        if quote is None:
            meta["reason"] = "no_matching_symbol_in_response"
            fallback_quote, fallback_meta = _maybe_polygon_quote_fallback(ticker_u)
            if fallback_quote is not None:
                meta["fallback_provider"] = fallback_meta.get("provider")
                meta["reason"] = "schwab_fallback_polygon"
                px = extract_schwab_last_price(fallback_quote)
                if px is not None:
                    meta["last_price"] = round(px, 6)
                return fallback_quote, meta
            return None, meta
        meta["quote_keys"] = sorted(str(k) for k in quote.keys())[:32]
        price = extract_schwab_last_price(quote)
        if price is None:
            meta["reason"] = "last_price_not_parseable"
            fallback_quote, fallback_meta = _maybe_polygon_quote_fallback(ticker_u)
            if fallback_quote is not None:
                meta["fallback_provider"] = fallback_meta.get("provider")
                meta["reason"] = "schwab_fallback_polygon"
                fpx = extract_schwab_last_price(fallback_quote)
                if fpx is not None:
                    meta["last_price"] = round(fpx, 6)
                return fallback_quote, meta
        else:
            meta["last_price"] = round(price, 6)
        return quote, meta
    except Exception as e:
        meta["reason"] = type(e).__name__
        meta["error_detail"] = str(e)[:400]
        LOG.warning("get_current_quote failed for %s: %s", ticker_u, e)
    fallback_quote, fallback_meta = _maybe_polygon_quote_fallback(ticker_u)
    if fallback_quote is not None:
        meta["fallback_provider"] = fallback_meta.get("provider")
        meta["reason"] = "schwab_fallback_polygon"
        px = extract_schwab_last_price(fallback_quote)
        if px is not None:
            meta["last_price"] = round(px, 6)
        return fallback_quote, meta
    return None, meta


def get_current_quote(
    ticker: str,
    auth: DualSchwabAuth | None = None,
    skill_dir: Path | str | None = None,
) -> dict | None:
    """Fetch real-time quote using Market Session."""
    quote, _meta = get_current_quote_with_status(ticker, auth=auth, skill_dir=skill_dir)
    return quote


# --------------------------------------------------------------------------- #
# Phase 2: expanded Schwab market-data surfaces (flag-gated; default OFF).
# Each returns (payload | None, meta). Callers gate via the provider layer;
# the inline mode check prevents accidental network calls when disabled.
# --------------------------------------------------------------------------- #
def _ensure_auth(auth: DualSchwabAuth | None, skill_dir: Path | str | None) -> DualSchwabAuth:
    return auth or DualSchwabAuth(skill_dir=Path(skill_dir) if skill_dir else None)


def get_market_movers_with_status(
    index: str = "$SPX",
    *,
    sort: str = "PERCENT_CHANGE_UP",
    auth: DualSchwabAuth | None = None,
    skill_dir: Path | str | None = None,
) -> tuple[dict | None, dict[str, Any]]:
    """Schwab /marketdata/v1/movers/{index} — market internals / movers.

    Gated by ``MARKET_MOVERS_MODE`` (default off). Returns raw screener JSON.
    """
    meta: dict[str, Any] = {"provider": "schwab", "endpoint": "marketdata.movers", "index": index}
    try:
        from config import get_market_movers_mode

        if get_market_movers_mode(skill_dir) == "off":
            meta["reason"] = "mode_off"
            return None, meta
    except Exception:
        meta["reason"] = "mode_off"
        return None, meta
    try:
        auth = _ensure_auth(auth, skill_dir)
        url = f"{SCHWAB_BASE}/marketdata/v1/movers/{index}"
        resp = _request_with_backoff(auth, "GET", url, params={"sort": sort})
        meta["http_status"] = resp.status_code
        resp.raise_for_status()
        return resp.json(), meta
    except Exception as e:
        meta["reason"] = type(e).__name__
        meta["error_detail"] = str(e)[:200]
        _emit_obs("record_request_error", skill_dir, "marketdata.movers", None)
        return None, meta


def get_options_chain_with_status(
    symbol: str,
    *,
    contract_type: str = "ALL",
    strike_count: int = 10,
    auth: DualSchwabAuth | None = None,
    skill_dir: Path | str | None = None,
) -> tuple[dict | None, dict[str, Any]]:
    """Schwab /marketdata/v1/chains — options-chain intelligence (IV, skew).

    Gated by ``OPTIONS_INTEL_MODE`` (default off). Returns raw chain JSON.
    """
    sym = symbol.upper().strip()
    meta: dict[str, Any] = {"provider": "schwab", "endpoint": "marketdata.options.chains", "symbol": sym}
    try:
        from config import get_options_intel_mode

        if get_options_intel_mode(skill_dir) == "off":
            meta["reason"] = "mode_off"
            return None, meta
    except Exception:
        meta["reason"] = "mode_off"
        return None, meta
    try:
        auth = _ensure_auth(auth, skill_dir)
        url = f"{SCHWAB_BASE}/marketdata/v1/chains"
        params = {"symbol": sym, "contractType": contract_type, "strikeCount": strike_count}
        resp = _request_with_backoff(auth, "GET", url, params=params)
        meta["http_status"] = resp.status_code
        resp.raise_for_status()
        return resp.json(), meta
    except Exception as e:
        meta["reason"] = type(e).__name__
        meta["error_detail"] = str(e)[:200]
        _emit_obs("record_request_error", skill_dir, "marketdata.options.chains", None)
        return None, meta


def get_instrument_with_status(
    symbol: str,
    *,
    projection: str = "fundamental",
    auth: DualSchwabAuth | None = None,
    skill_dir: Path | str | None = None,
) -> tuple[dict | None, dict[str, Any]]:
    """Schwab /marketdata/v1/instruments — fundamentals / symbol metadata.

    Gated by ``INSTRUMENTS_MODE`` (default off). Returns raw instrument JSON.
    """
    sym = symbol.upper().strip()
    meta: dict[str, Any] = {"provider": "schwab", "endpoint": "marketdata.instruments", "symbol": sym}
    try:
        from config import get_instruments_mode

        if get_instruments_mode(skill_dir) == "off":
            meta["reason"] = "mode_off"
            return None, meta
    except Exception:
        meta["reason"] = "mode_off"
        return None, meta
    try:
        auth = _ensure_auth(auth, skill_dir)
        url = f"{SCHWAB_BASE}/marketdata/v1/instruments"
        resp = _request_with_backoff(auth, "GET", url, params={"symbol": sym, "projection": projection})
        meta["http_status"] = resp.status_code
        resp.raise_for_status()
        return resp.json(), meta
    except Exception as e:
        meta["reason"] = type(e).__name__
        meta["error_detail"] = str(e)[:200]
        _emit_obs("record_request_error", skill_dir, "marketdata.instruments", None)
        return None, meta
