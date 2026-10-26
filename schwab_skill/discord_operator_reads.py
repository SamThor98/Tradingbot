"""Read-only phone views for the Discord operator bot.

Reads the local dashboard SQLite file (``app_state.last_scan`` and
``scan_results``) and, for price levels and pick outcomes, daily history
through the existing market-data client (Schwab market token, Yahoo fallback).

Does not place, stage, cancel, or approve orders, and does not call Schwab
account or trading endpoints.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

LOG = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
LOCAL_USER_ID = "local"
MAX_ABOVE_SMA50_PCT = 8.0
ADVISORY_STOP_PCT = 0.07
TOP_LIMIT = 15
PICKS_MAX_DAYS = 90
HORIZONS: tuple[int, ...] = (5, 10, 20)
DISCORD_TEXT_LIMIT = 1900
HISTORY_DAYS = 500
LEVELS_HISTORY_DAYS = 320
STALE_BAR_DAYS = 5

_BUYOUT_KEYS = (
    "buyout",
    "buyout_target",
    "known_buyout",
    "known_buyout_target",
    "is_buyout",
    "is_buyout_target",
    "acquisition_target",
    "merger_target",
    "pending_buyout",
    "takeover_target",
)
_BUYOUT_TOKENS = frozenset(_BUYOUT_KEYS)
_BUYOUT_LIST_KEYS = (
    "buyout_targets",
    "known_buyouts",
    "known_buyout_targets",
    "acquisition_targets",
    "merger_targets",
)


@dataclass(frozen=True)
class OperatorView:
    """Discord payload. ``embed`` is an Embed.from_dict mapping when set."""

    text: str
    embed: dict[str, Any] | None = None


@dataclass(frozen=True)
class _Ranked:
    ticker: str
    setup: str
    price: float
    stop: float | None
    pct: float


@dataclass(frozen=True)
class _Pick:
    ticker: str
    scan_day: date
    entry: float
    entry_day: date
    stretch: float | None
    excess: dict[int, float]
    tercile: str | None = None


def render_top(db_path: Path, *, n: int = 10, now: datetime | None = None) -> str:
    """Today's ranked names from ``app_state.last_scan``, compact for a phone."""
    limit = _clamp_int(n, default=10, lo=1, hi=TOP_LIMIT)
    loaded = _load_last_scan(db_path)
    if loaded.error:
        return loaded.error
    blob = loaded.blob or {}
    signals = _signal_rows(blob.get("signals"))
    scan_dt = _parse_dt(blob.get("at"))
    today = _as_utc(now).astimezone(ET).date()
    when = _format_scan_when(scan_dt)
    stale = scan_dt is None or _et_date(scan_dt) != today
    week_flags, week_note = _week_setup_flags(db_path, scan_dt)

    if not signals:
        shortlist_n = len(_signal_rows(blob.get("shortlist_signals")))
        extra = f" Shortlist has {shortlist_n} near-miss row(s)." if shortlist_n else ""
        prefix = _stale_prefix(stale, when, today)
        return _fit(
            f"{prefix}**No ranked names** in the latest scan"
            f"{f' ({when})' if when else ''}.{extra} Run a scan from the dashboard."
        )

    ranked, dropped = _rank_signals(signals, week_flags, _marked_tickers(blob))
    prefix = _stale_prefix(stale, when, today)
    if not ranked:
        return _fit(
            f"{prefix}**No names passed the phone screen** "
            f"({len(signals)} in the latest scan{f', {when}' if when else ''}). "
            f"{_drop_sentence(dropped)} "
            "Kept names are at most 8% above the 50-day, with Altman-distress "
            "and marked buyout targets removed, tightest to the 50-day first."
        )

    shown = ranked[:limit]
    lines = [
        f"{prefix}**Top {len(shown)}**"
        + (f" · {when}" if when else "")
        + (f" · {len(ranked)} passed" if len(ranked) != len(shown) else ""),
        "```",
        "#  Tkr    Set  Px      Stop    vs50",
    ]
    for idx, row in enumerate(shown, start=1):
        stop_s = f"{row.stop:7.2f}" if row.stop is not None else "      —"
        lines.append(f"{idx:>2} {row.ticker:<6} {row.setup:<4} {row.price:7.2f} {stop_s} {row.pct:+5.1f}%")
    lines.append("```")
    lines.append(_drop_sentence(dropped))
    lines.append("Sorted by % vs 50d, tightest first. Stop is the stored scan stop, else 7% below price.")
    if week_note:
        lines.append(week_note)
    lines.append("Read-only.")
    return _fit("\n".join(lines))


def render_picks(
    db_path: Path,
    *,
    days: int = 10,
    now: datetime | None = None,
    history_loader: Callable[[str], pd.DataFrame] | None = None,
    skill_dir: Path | None = None,
) -> str:
    """Past scan picks vs SPY at 5/10/20-day horizons. Entry is the prior close."""
    lookback = _clamp_int(days, default=10, lo=1, hi=PICKS_MAX_DAYS)
    moment = _as_utc(now)
    rows, err = _load_scan_rows(db_path)
    if err:
        return err
    start_day = moment.astimezone(ET).date() - timedelta(days=lookback - 1)
    picks_raw = _dedupe_picks(rows, start_day=start_day, end_day=moment.astimezone(ET).date())
    if not picks_raw:
        return _fit(
            f"**No scan picks** in the last {lookback} day(s) "
            f"(through {moment.astimezone(ET).date().isoformat()} ET). "
            "The dashboard stores them in scan_results after a local scan."
        )

    tickers = sorted({ticker for ticker, _payload, _day in picks_raw})
    try:
        histories, spy = _load_pick_histories(tickers, history_loader=history_loader, skill_dir=skill_dir)
    except Exception as exc:
        LOG.warning("Pick history load failed: %s", exc)
        return _fit(
            "**Could not load market history** for pick outcomes. "
            "The market-data client (Schwab market token, Yahoo fallback) did not return prices. "
            "Try again in a moment."
        )
    if spy.empty or "close" not in spy.columns:
        return _fit(
            "**No SPY history**, so excess returns are unavailable. Check the market-data token and try `/picks` again."
        )

    measured, skipped = _measure_picks(picks_raw, histories, spy)
    if not measured:
        return _fit(
            f"**{len(picks_raw)} pick(s)** in the last {lookback} day(s), "
            "but none have a prior close plus enough daily bars to score. "
            f"Skipped {skipped} with missing history."
        )

    labeled = _assign_terciles(measured)
    header = f"**Picks vs SPY** · last {lookback}d · entry = prior close\n{len(measured)} scored" + (
        f" · skipped {skipped} (no history)" if skipped else ""
    )
    lines = [header, "```", "H     n   mean xs   beat"]
    any_window = False
    for horizon in HORIZONS:
        stats = _horizon_stats(labeled, horizon)
        lines.append(_stats_line(f"{horizon}d", stats))
        if stats[0]:
            any_window = True
    lines.append("```")
    if not any_window:
        lines.append("No completed 5/10/20-day window yet. Those sessions have to print after the prior-close entry.")
    tercile_block = _tercile_block(labeled)
    if tercile_block:
        lines.append(tercile_block)
    else:
        lines.append("Stretch terciles need at least 3 picks with a 50-day average at entry.")
    lines.append("Excess = pick return minus SPY over the same dates. Read-only.")
    return _fit("\n".join(lines))


def render_levels(
    ticker: str,
    db_path: Path,
    *,
    now: datetime | None = None,
    history_loader: Callable[[str], pd.DataFrame] | None = None,
    quote_loader: Callable[[str], Any] | None = None,
    skill_dir: Path | None = None,
) -> OperatorView:
    """Price, SMA50/200, ATR14, % vs 50d, and the scan stop when the name is in last_scan."""
    symbol = _clean_ticker(ticker)
    if not symbol:
        return OperatorView("Usage: `/levels TICKER` (example: AAPL).")

    moment = _as_utc(now)
    if history_loader is None or quote_loader is None:
        frame, quote = _fetch_levels_market(symbol, skill_dir or _default_skill_dir())
    else:
        try:
            frame = history_loader(symbol)
            quote = quote_loader(symbol)
        except Exception as exc:
            LOG.warning("Levels fetch failed for %s: %s", symbol, exc)
            return OperatorView(f"**{symbol}**: market data failed. Check the Schwab market token and try again.")

    hist = _normalize_history(frame)
    if hist.empty or len(hist) < 15:
        return OperatorView(
            f"**{symbol}**: not enough daily bars "
            f"({0 if hist.empty else len(hist)}). "
            "The market-data client returned nothing usable."
        )

    from stage_analysis import add_indicators

    ind = add_indicators(hist)
    last_bar_ts = ind.index[-1]
    last_bar = last_bar_ts.date() if hasattr(last_bar_ts, "date") else moment.astimezone(ET).date()
    last_close = _float(ind["close"].iloc[-1])
    sma50 = _float(ind["sma_50"].iloc[-1]) if len(ind) >= 50 else None
    sma200 = _float(ind["sma_200"].iloc[-1]) if len(ind) >= 200 else None
    atr = _float(ind["atr_14"].iloc[-1]) if len(ind) >= 14 else None
    price, price_label = _display_price(quote, last_close, last_bar)
    pct = ((price / sma50) - 1.0) * 100.0 if price and sma50 and sma50 > 0 else None

    scan = _load_last_scan(db_path)
    stop, stop_note = _stop_for_symbol(symbol, scan.blob)

    age_days = (moment.astimezone(ET).date() - last_bar).days
    stale_note = ""
    if age_days > STALE_BAR_DAYS:
        stale_note = f"History looks stale (last bar {last_bar.isoformat()}, {age_days}d ago)."

    description_bits = [f"Price **{_money(price)}** ({price_label})"]
    if pct is None:
        description_bits.append("% vs 50d **unavailable**")
    else:
        description_bits.append(f"% vs 50d **{pct:+.1f}%**")
    if stale_note:
        description_bits.append(stale_note)
    if scan.error and "No dashboard database" in scan.error:
        description_bits.append("No dashboard database, so the scan stop is unknown.")
    elif scan.error:
        description_bits.append(scan.error)

    embed = {
        "title": f"{symbol} levels",
        "description": "\n".join(description_bits),
        "color": 0x3498DB,
        "fields": [
            {"name": "SMA 50", "value": _money(sma50) if sma50 else "Need 50 bars", "inline": True},
            {"name": "SMA 200", "value": _money(sma200) if sma200 else "Need 200 bars", "inline": True},
            {"name": "ATR 14", "value": _money(atr) if atr else "—", "inline": True},
            {"name": "Stop", "value": stop_note, "inline": False},
        ],
    }
    text = (
        f"**{symbol}** {_money(price)} ({price_label}) · "
        f"50d {_money(sma50)} · 200d {_money(sma200)} · "
        f"ATR {_money(atr)} · vs50 {pct:+.1f}%"
        if pct is not None
        else f"**{symbol}** {_money(price)} ({price_label}) · "
        f"50d {_money(sma50)} · 200d {_money(sma200)} · ATR {_money(atr)}"
    )
    text = f"{text}\n{stop_note}"
    if stale_note:
        text = f"{text}\n{stale_note}"
    return OperatorView(_fit(text), embed)


def _stop_for_symbol(symbol: str, blob: dict[str, Any] | None) -> tuple[float | None, str]:
    if not isinstance(blob, dict):
        return None, "Not in the latest scan."
    for key, label in (("signals", "latest scan"), ("shortlist_signals", "latest shortlist")):
        for row in _signal_rows(blob.get(key)):
            ticker = _clean_ticker(str(row.get("ticker") or row.get("symbol") or ""))
            if ticker != symbol:
                continue
            explicit = _explicit_stop(row)
            if explicit is not None:
                return explicit, f"{_money(explicit)} (stored on the {label})"
            price = _price_of(row)
            if price is None:
                return None, f"In the {label}, but that row has no price to derive a stop."
            derived = round(price * (1.0 - ADVISORY_STOP_PCT), 2)
            return derived, f"{_money(derived)} (7% below the {label} price)"
    return None, "Not in the latest scan."


def _display_price(quote: Any, last_close: float | None, last_bar: date) -> tuple[float | None, str]:
    live = None
    last = None
    try:
        from market_data import extract_schwab_last_price, extract_schwab_live_price

        if isinstance(quote, dict):
            live = extract_schwab_live_price(quote)
            last = extract_schwab_last_price(quote)
    except Exception as exc:
        LOG.debug("Quote parse skipped: %s", exc)
        if isinstance(quote, dict):
            live = _float(quote.get("lastPrice") or quote.get("last_price"))
            last = live
    if isinstance(quote, (int, float)) and not isinstance(quote, bool):
        live = _float(quote)
        last = live
    if live is not None and live > 0:
        return live, "quote"
    if last is not None and last > 0:
        return last, "quote prior close"
    if last_close is not None and last_close > 0:
        return last_close, f"last close {last_bar.isoformat()}"
    return None, "unavailable"


def _fetch_levels_market(ticker: str, skill_dir: Path) -> tuple[pd.DataFrame, Any]:
    """History + quote via DataProvider. Market token only; no account session use."""
    import asyncio

    from data_provider import DataProvider
    from schwab_auth import DualSchwabAuth

    auth = DualSchwabAuth(skill_dir=skill_dir, auto_refresh=False)
    provider = DataProvider(skill_dir=skill_dir)

    async def _go() -> tuple[pd.DataFrame, Any]:
        hist = await provider.get_history(ticker, days=LEVELS_HISTORY_DAYS, auth=auth)
        quote = await provider.get_quote(ticker, auth=auth)
        return hist, quote

    try:
        return asyncio.run(_go())
    finally:
        auth.close()


def _load_pick_histories(
    tickers: list[str],
    *,
    history_loader: Callable[[str], pd.DataFrame] | None,
    skill_dir: Path | None,
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    symbols = list(tickers) + ["SPY"]
    if history_loader is None:
        raw = _fetch_histories_market(symbols, skill_dir or _default_skill_dir())
    else:
        raw = {}
        for symbol in symbols:
            try:
                raw[symbol] = history_loader(symbol)
            except Exception as exc:
                LOG.warning("Pick history failed for %s: %s", symbol, exc)
                raw[symbol] = pd.DataFrame()
    histories = {ticker: _normalize_history(raw.get(ticker)) for ticker in tickers}
    return histories, _normalize_history(raw.get("SPY"))


def _fetch_histories_market(tickers: list[str], skill_dir: Path) -> dict[str, pd.DataFrame]:
    """One market-token session for every symbol. No account or trading calls."""
    import asyncio

    from data_provider import DataProvider
    from schwab_auth import DualSchwabAuth

    auth = DualSchwabAuth(skill_dir=skill_dir, auto_refresh=False)
    provider = DataProvider(skill_dir=skill_dir)

    async def _go() -> dict[str, pd.DataFrame]:
        out: dict[str, pd.DataFrame] = {}
        for ticker in tickers:
            try:
                out[ticker] = await provider.get_history(ticker, days=HISTORY_DAYS, auth=auth)
            except Exception as exc:
                LOG.warning("History fetch failed for %s: %s", ticker, exc)
                out[ticker] = pd.DataFrame()
        return out

    try:
        return asyncio.run(_go())
    finally:
        auth.close()


def _default_skill_dir() -> Path:
    return Path(__file__).resolve().parent


@dataclass(frozen=True)
class _ScanLoad:
    blob: dict[str, Any] | None
    error: str | None


def _load_last_scan(db_path: Path) -> _ScanLoad:
    if not db_path.exists():
        return _ScanLoad(
            None,
            "No dashboard database at `webapp/webapp.db`. Run a local scan from the dashboard first.",
        )
    try:
        conn = _open_ro(db_path)
    except sqlite3.Error as exc:
        LOG.warning("last_scan open failed: %s", exc)
        return _ScanLoad(None, "Dashboard database could not be opened. The latest scan is unavailable.")
    try:
        try:
            rows = conn.execute(
                "SELECT user_id, value_json, updated_at FROM app_state WHERE key = ?",
                ("last_scan",),
            ).fetchall()
        except sqlite3.Error as exc:
            LOG.warning("last_scan query failed: %s", exc)
            return _ScanLoad(None, "No `last_scan` table in the dashboard database. Run a local scan first.")
    finally:
        conn.close()
    if not rows:
        return _ScanLoad(None, "No `last_scan` stored yet. Run a scan from the dashboard.")
    chosen = _prefer_local_row(rows)
    blob = _loads(chosen["value_json"])
    if not isinstance(blob, dict):
        return _ScanLoad(None, "`last_scan` is stored but unreadable. Run a fresh dashboard scan.")
    return _ScanLoad(blob, None)


def _load_scan_rows(db_path: Path) -> tuple[list[tuple[str, dict[str, Any], date]], str | None]:
    if not db_path.exists():
        return [], "No dashboard database at `webapp/webapp.db`. Run a local scan from the dashboard first."
    try:
        conn = _open_ro(db_path)
    except sqlite3.Error as exc:
        LOG.warning("scan_results open failed: %s", exc)
        return [], "Dashboard database could not be opened."
    try:
        try:
            fetched = conn.execute("SELECT user_id, ticker, payload_json, created_at FROM scan_results").fetchall()
        except sqlite3.Error as exc:
            LOG.warning("scan_results query failed: %s", exc)
            return [], "No `scan_results` history in the dashboard database yet."
    finally:
        conn.close()
    local = [row for row in fetched if str(row["user_id"] or "") == LOCAL_USER_ID]
    use = local if local else []
    if fetched and not use:
        return [], "Scan history is not for the local dashboard user, so `/picks` has nothing to score."
    out: list[tuple[str, dict[str, Any], date]] = []
    for row in use:
        payload = _loads(row["payload_json"])
        if not isinstance(payload, dict):
            payload = {}
        ticker = _clean_ticker(str(row["ticker"] or payload.get("ticker") or ""))
        created = _parse_dt(row["created_at"])
        if not ticker or created is None:
            continue
        out.append((ticker, payload, _et_date(created)))
    return out, None


def _week_setup_flags(db_path: Path, scan_dt: datetime | None) -> tuple[dict[str, set[str]], str | None]:
    if scan_dt is None:
        return {}, None
    scan_day = _et_date(scan_dt)
    monday = scan_day - timedelta(days=scan_day.weekday())
    sunday = monday + timedelta(days=6)
    rows, err = _load_scan_rows(db_path)
    if err:
        return {}, "Setup is from this scan only; scan history was unreadable."
    flags: dict[str, set[str]] = {}
    for ticker, payload, day in rows:
        if day < monday or day > sunday:
            continue
        flags.setdefault(ticker, set()).update(_setup_flags(payload))
    return flags, None


def _prefer_local_row(rows: list[sqlite3.Row]) -> sqlite3.Row:
    local = [row for row in rows if str(row["user_id"] or "") == LOCAL_USER_ID]
    pool = local or list(rows)

    def _key(row: sqlite3.Row) -> str:
        return str(row["updated_at"] or "")

    return max(pool, key=_key)


def _rank_signals(
    signals: list[dict[str, Any]],
    week_flags: dict[str, set[str]],
    marked_buyouts: set[str],
) -> tuple[list[_Ranked], dict[str, int]]:
    dropped = {"extended": 0, "altman": 0, "buyout": 0, "missing": 0}
    by_ticker: dict[str, _Ranked] = {}
    for signal in signals:
        ticker = _clean_ticker(str(signal.get("ticker") or signal.get("symbol") or ""))
        if not ticker:
            dropped["missing"] += 1
            continue
        if _marked_altman(signal):
            dropped["altman"] += 1
            continue
        if ticker in marked_buyouts or _marked_buyout(signal):
            dropped["buyout"] += 1
            continue
        pct = _pct_above_sma50(signal)
        price = _price_of(signal)
        if pct is None or price is None:
            dropped["missing"] += 1
            continue
        if pct > MAX_ABOVE_SMA50_PCT:
            dropped["extended"] += 1
            continue
        flags = set(_setup_flags(signal))
        flags.update(week_flags.get(ticker, set()))
        row = _Ranked(
            ticker=ticker,
            setup=_setup_label(flags),
            price=price,
            stop=_stop_of(signal),
            pct=pct,
        )
        prev = by_ticker.get(ticker)
        if prev is None or row.pct < prev.pct:
            by_ticker[ticker] = row
    ranked = sorted(by_ticker.values(), key=lambda row: (row.pct, row.ticker))
    return ranked, dropped


def _setup_flags(signal: dict[str, Any]) -> set[str]:
    flags: set[str] = set()
    family = str(signal.get("entry_family") or "").strip().lower()
    if family in {"stage2", "both"}:
        flags.add("TB")
    hits: set[str] = set()
    for key in ("horizon_hits",):
        raw = signal.get(key)
        if isinstance(raw, list):
            hits.update(str(item or "").strip().lower() for item in raw if str(item or "").strip())
    attr = signal.get("strategy_attribution")
    if isinstance(attr, dict):
        for key in ("top_live", "top_shadow", "catalog_id"):
            val = str(attr.get(key) or "").strip().lower()
            if val:
                hits.add(val)
    plugins = signal.get("strategy_plugins")
    if isinstance(plugins, list):
        for plugin in plugins:
            if not isinstance(plugin, dict) or not plugin.get("triggered"):
                continue
            name = str(plugin.get("name") or "").strip().lower()
            if name:
                hits.add(name)
    if "weekly_swing" in hits:
        flags.add("WS")
    if "trend_breakout" in hits or "stage2" in hits or "stage2_vcp" in hits:
        flags.add("TB")
    return flags


def _setup_label(flags: set[str]) -> str:
    has_tb = "TB" in flags
    has_ws = "WS" in flags
    if has_tb and has_ws:
        return "both"
    if has_tb:
        return "TB"
    if has_ws:
        return "WS"
    return "—"


def _marked_altman(signal: dict[str, Any]) -> bool:
    if _truthy(signal.get("altman_distress")):
        return True
    blobs: list[Any] = [
        signal.get("forensic_flags"),
        signal.get("flags"),
        signal.get("reliability_reasons"),
        signal.get("sec_risk_reasons"),
    ]
    event = signal.get("event_risk")
    if isinstance(event, dict):
        blobs.append(event.get("reasons"))
        if _truthy(event.get("altman_distress")):
            return True
    return _blob_has_token(blobs, {"altman_distress"})


def _marked_buyout(signal: dict[str, Any]) -> bool:
    for key in _BUYOUT_KEYS:
        if _truthy(signal.get(key)):
            return True
    blobs: list[Any] = [
        signal.get("forensic_flags"),
        signal.get("flags"),
        signal.get("tags"),
        signal.get("reliability_reasons"),
        signal.get("sec_risk_reasons"),
    ]
    event = signal.get("event_risk")
    if isinstance(event, dict):
        blobs.append(event.get("reasons"))
        for key in _BUYOUT_KEYS:
            if _truthy(event.get(key)):
                return True
    return _blob_has_token(blobs, _BUYOUT_TOKENS, substring="buyout")


def _marked_tickers(blob: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    sources: list[Any] = [blob]
    diagnostics = blob.get("diagnostics")
    if isinstance(diagnostics, dict):
        sources.append(diagnostics)
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in _BUYOUT_LIST_KEYS:
            raw = source.get(key)
            if isinstance(raw, str):
                raw = [part.strip() for part in raw.split(",")]
            if not isinstance(raw, list):
                continue
            for item in raw:
                ticker = _clean_ticker(str(item or ""))
                if ticker:
                    found.add(ticker)
    return found


def _blob_has_token(blobs: list[Any], tokens: set[str], *, substring: str | None = None) -> bool:
    for blob in blobs:
        items = [blob] if isinstance(blob, str) else blob
        if not isinstance(items, list):
            continue
        for item in items:
            token = str(item or "").strip().lower().replace(" ", "_").replace("-", "_")
            if token in tokens:
                return True
            if substring and substring in token:
                return True
    return False


def _pct_above_sma50(signal: dict[str, Any]) -> float | None:
    price = _price_of(signal)
    sma = _float(signal.get("sma_50"))
    if sma is None:
        sma = _float(signal.get("sma50"))
    if price is not None and sma is not None and sma > 0:
        # Round so binary noise (108/100 -> 8.000000000000007) does not
        # drop a name that is exactly 8% above the 50-day.
        return round(((price / sma) - 1.0) * 100.0, 4)
    return None


def _price_of(signal: dict[str, Any]) -> float | None:
    for key in ("price", "current_price", "last_price"):
        val = _float(signal.get(key))
        if val is not None and val > 0:
            return val
    return None


def _explicit_stop(signal: dict[str, Any]) -> float | None:
    for key in ("stop", "stop_price", "stop_level", "suggested_stop", "invalidation_stop"):
        val = _float(signal.get(key))
        if val is not None and val > 0:
            return round(val, 2)
    return None


def _stop_of(signal: dict[str, Any]) -> float | None:
    explicit = _explicit_stop(signal)
    if explicit is not None:
        return explicit
    price = _price_of(signal)
    if price is None:
        return None
    return round(price * (1.0 - ADVISORY_STOP_PCT), 2)


def _dedupe_picks(
    rows: list[tuple[str, dict[str, Any], date]],
    *,
    start_day: date,
    end_day: date,
) -> list[tuple[str, dict[str, Any], date]]:
    chosen: dict[tuple[str, date], tuple[str, dict[str, Any], date]] = {}
    for ticker, payload, day in rows:
        if day < start_day or day > end_day:
            continue
        chosen.setdefault((ticker, day), (ticker, payload, day))
    return [chosen[key] for key in sorted(chosen)]


def _measure_picks(
    picks_raw: list[tuple[str, dict[str, Any], date]],
    histories: dict[str, pd.DataFrame],
    spy: pd.DataFrame,
) -> tuple[list[_Pick], int]:
    measured: list[_Pick] = []
    skipped = 0
    spy_close = spy["close"].astype(float)
    for ticker, _payload, scan_day in picks_raw:
        hist = histories.get(ticker)
        if hist is None or hist.empty:
            skipped += 1
            continue
        pick = _one_pick(ticker, scan_day, hist, spy_close)
        if pick is None:
            skipped += 1
            continue
        measured.append(pick)
    return measured, skipped


def _one_pick(
    ticker: str,
    scan_day: date,
    hist: pd.DataFrame,
    spy_close: pd.Series,
) -> _Pick | None:
    dates = [ts.date() for ts in hist.index]
    closes = hist["close"].astype(float).tolist()
    prior_indexes = [idx for idx, day in enumerate(dates) if day < scan_day]
    if not prior_indexes:
        return None
    entry_idx = prior_indexes[-1]
    entry = closes[entry_idx]
    if entry <= 0:
        return None
    entry_day = dates[entry_idx]
    stretch = None
    if entry_idx >= 49:
        window = closes[entry_idx - 49 : entry_idx + 1]
        sma = sum(window) / 50.0
        if sma > 0:
            stretch = (entry / sma) - 1.0
    future = [(dates[idx], closes[idx]) for idx in range(entry_idx + 1, len(closes))]
    excess: dict[int, float] = {}
    for horizon in HORIZONS:
        if len(future) < horizon:
            continue
        exit_day, exit_px = future[horizon - 1]
        if exit_px <= 0:
            continue
        spy_entry = _close_on_or_before(spy_close, entry_day)
        spy_exit = _spy_close_on(spy_close, exit_day)
        if spy_entry is None or spy_exit is None or spy_entry <= 0:
            continue
        stock_ret = (exit_px / entry) - 1.0
        spy_ret = (spy_exit / spy_entry) - 1.0
        excess[horizon] = stock_ret - spy_ret
    return _Pick(
        ticker=ticker,
        scan_day=scan_day,
        entry=entry,
        entry_day=entry_day,
        stretch=stretch,
        excess=excess,
    )


def _close_on_or_before(series: pd.Series, day: date) -> float | None:
    candidates = [(ts.date(), float(val)) for ts, val in series.items() if ts.date() <= day]
    if not candidates:
        return None
    return candidates[-1][1]


def _spy_close_on(series: pd.Series, day: date) -> float | None:
    for ts, val in series.items():
        if ts.date() == day:
            return float(val)
    return None


def _assign_terciles(picks: list[_Pick]) -> list[_Pick]:
    usable = [pick for pick in picks if pick.stretch is not None]
    if len(usable) < 3:
        return picks
    stretches = pd.Series([float(pick.stretch) for pick in usable], index=[id(pick) for pick in usable])
    try:
        bins = pd.qcut(stretches, 3, labels=["low", "mid", "high"], duplicates="drop")
    except ValueError:
        return picks
    if bins.nunique(dropna=True) < 3:
        return picks
    labels = {int(key): str(val) for key, val in bins.items()}
    out: list[_Pick] = []
    for pick in picks:
        label = labels.get(id(pick))
        if label is None:
            out.append(pick)
            continue
        out.append(
            _Pick(
                ticker=pick.ticker,
                scan_day=pick.scan_day,
                entry=pick.entry,
                entry_day=pick.entry_day,
                stretch=pick.stretch,
                excess=pick.excess,
                tercile=label,
            )
        )
    return out


def _horizon_stats(
    picks: list[_Pick], horizon: int, *, tercile: str | None = None
) -> tuple[int, float | None, float | None]:
    values = [
        pick.excess[horizon]
        for pick in picks
        if horizon in pick.excess and (tercile is None or pick.tercile == tercile)
    ]
    if not values:
        return 0, None, None
    mean = sum(values) / len(values)
    beat = 100.0 * sum(1 for val in values if val > 0) / len(values)
    return len(values), mean, beat


def _stats_line(label: str, stats: tuple[int, float | None, float | None]) -> str:
    n, mean, beat = stats
    if n == 0 or mean is None or beat is None:
        return f"{label:<4} {0:>3}   {'—':>7}   {'—':>5}"
    return f"{label:<4} {n:>3}   {_fmt_return(mean):>7}   {beat:>4.0f}%"


def _tercile_block(picks: list[_Pick]) -> str | None:
    if not any(pick.tercile for pick in picks):
        return None
    lines = ["Stretch terciles (low = closest to the 50d)", "```", "H   band  n   xs     beat"]
    wrote = False
    for horizon in HORIZONS:
        for band in ("low", "mid", "high"):
            stats = _horizon_stats(picks, horizon, tercile=band)
            if stats[0] == 0:
                continue
            wrote = True
            n, mean, beat = stats
            lines.append(f"{horizon}d {band:<5} {n:>2}  {_fmt_return(mean or 0.0):>7}  {beat or 0:>4.0f}%")
    lines.append("```")
    if not wrote:
        return None
    return "\n".join(lines)


def _fmt_return(frac: float) -> str:
    pct = frac * 100.0
    if abs(pct) >= 10:
        return f"{pct:+.1f}%"
    return f"{pct:+.2f}%"


def _drop_sentence(dropped: dict[str, int]) -> str:
    parts: list[str] = []
    if dropped.get("extended"):
        parts.append(f"{dropped['extended']} >8% above 50d")
    if dropped.get("altman"):
        parts.append(f"{dropped['altman']} Altman-distress")
    if dropped.get("buyout"):
        parts.append(f"{dropped['buyout']} buyout")
    if dropped.get("missing"):
        parts.append(f"{dropped['missing']} missing price/SMA50")
    if not parts:
        return "Nothing dropped."
    return "Dropped " + ", ".join(parts) + "."


def _stale_prefix(stale: bool, when: str, today: date) -> str:
    if not stale:
        return ""
    if when:
        return f"**Stale scan** ({when}, not {today.isoformat()} ET).\n"
    return f"**Stale scan** (no timestamp; today is {today.isoformat()} ET).\n"


def _format_scan_when(scan_dt: datetime | None) -> str:
    if scan_dt is None:
        return ""
    local = scan_dt.astimezone(ET)
    hour = local.strftime("%I").lstrip("0") or "12"
    ampm = local.strftime("%p").lower()
    return f"{local.strftime('%b')} {local.day} {hour}:{local.strftime('%M')}{ampm[0]} ET"


def _signal_rows(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    return [row for row in raw if isinstance(row, dict)]


def _normalize_history(df: pd.DataFrame | None) -> pd.DataFrame:
    empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return empty
    out = df.copy()
    out.columns = [str(col).strip().lower() for col in out.columns]
    if "date" in out.columns and not isinstance(out.index, pd.DatetimeIndex):
        idx = pd.to_datetime(out["date"], errors="coerce", utc=True)
    else:
        idx = pd.to_datetime(out.index, errors="coerce", utc=True)
    idx = pd.DatetimeIndex(idx)
    if idx.tz is not None:
        idx = idx.tz_convert("UTC").tz_localize(None)
    out.index = idx.normalize()
    out = out[~out.index.isna()]
    if "close" not in out.columns:
        return empty
    for col in ("open", "high", "low"):
        if col not in out.columns:
            out[col] = out["close"]
    if "volume" not in out.columns:
        out["volume"] = 0.0
    out = out[["open", "high", "low", "close", "volume"]].apply(pd.to_numeric, errors="coerce")
    out = out.dropna(subset=["close"])
    out = out[~out.index.duplicated(keep="last")].sort_index()
    out.index.name = "date"
    return out


def _open_ro(db_path: Path) -> sqlite3.Connection:
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _loads(raw: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except json.JSONDecodeError:
            return parsed
    return parsed


def _parse_dt(raw: Any) -> datetime | None:
    if isinstance(raw, datetime):
        dt = raw
    elif isinstance(raw, str):
        text = raw.strip().replace("Z", "+00:00")
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            dt = None
            for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
                try:
                    dt = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    dt = None
            if dt is None:
                return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _et_date(dt: datetime) -> date:
    return dt.astimezone(ET).date()


def _as_utc(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


def _truthy(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value == 1:
        return True
    if isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "y"}:
        return True
    return False


def _clean_ticker(raw: str) -> str:
    text = "".join(ch for ch in str(raw or "").upper().strip() if ch.isalnum() or ch in ".-")
    if not text or not text[0].isalpha() or len(text) > 10:
        return ""
    return text


def _money(value: float | None) -> str:
    if value is None:
        return "—"
    return f"${value:,.2f}"


def _clamp_int(value: int, *, default: int, lo: int, hi: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, parsed))


def _fit(text: str) -> str:
    if len(text) <= DISCORD_TEXT_LIMIT:
        return text
    clipped = text[: DISCORD_TEXT_LIMIT - 24].rstrip()
    if clipped.count("```") % 2 == 1:
        clipped += "\n```"
    return clipped + "\n…(truncated)"
