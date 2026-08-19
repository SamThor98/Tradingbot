"""User-facing scan catalog: timeframes, strategies, and universes.

Live execution remains the daily Stage 2 + VCP engine. This module is the
normalized metadata layer so the dashboard can group strategies by horizon,
show short descriptions, and let operators pick a universe other than SP1500.
Weekly/monthly sleeves resample that same daily OHLCV; they are not a
separate vendor bar feed.
"""

from __future__ import annotations

from typing import Any

TIMEFRAMES: tuple[dict[str, str], ...] = (
    {
        "id": "intraday",
        "display_name": "Intraday",
        "description": "Same-session confirmation of a daily setup using the live quote (and optional 5m/15m bars). Not a standalone minute-bar book.",
    },
    {
        "id": "daily",
        "display_name": "Daily",
        "description": "Primary live engine: daily bars, Stage 2 + VCP, typical multi-week hold.",
    },
    {
        "id": "weekly",
        "display_name": "Weekly",
        "description": "Multi-week swing horizon. Evaluators resample the daily OHLCV the scanner already fetched (Friday week, 30-week SMA). Not a separate vendor weekly feed.",
    },
    {
        "id": "monthly",
        "display_name": "Monthly",
        "description": "Position-style horizon. Evaluators resample daily bars to month-end (10-month SMA). Not a separate vendor monthly feed.",
    },
)

# plugin / entry-family ids that map onto a catalog strategy
_STRATEGY_ALIASES: dict[str, tuple[str, ...]] = {
    "trend_breakout": ("trend_breakout", "breakout", "stage2_vcp", "stage2"),
    "pullback": ("pullback",),
    "pead_primary": ("pead_primary", "pead"),
    "breakout_confirm": ("breakout_confirm",),
    "gap_and_go": ("gap_and_go",),
    "range_expansion": ("range_expansion",),
    "donchian_20": ("donchian_20", "donchian"),
    "nr7_breakout": ("nr7_breakout", "nr7"),
    "weekly_swing": ("weekly_swing", "weekly_stage2"),
    "weekly_vcp": ("weekly_vcp",),
    "weekly_breakout": ("weekly_breakout",),
    "monthly_position": ("monthly_position", "ten_month_sma", "faber"),
    "monthly_52w_high": ("monthly_52w_high",),
    "monthly_pullback": ("monthly_pullback",),
    "opening_range_breakout": ("opening_range_breakout", "orb", "orb_5m"),
    "st_reversal_5d": ("st_reversal_5d", "jegadeesh_reversal", "weekly_loser"),
    "overnight_gap_fade": ("overnight_gap_fade", "gap_fade"),
    "weekly_reversal": ("weekly_reversal", "lehmann_reversal"),
    "momentum_12_1": ("momentum_12_1", "cs_momentum", "jegadeesh_titman"),
    "tsmom_12m": ("tsmom_12m", "time_series_momentum"),
}

STRATEGIES: tuple[dict[str, Any], ...] = (
    {
        "id": "breakout_confirm",
        "display_name": "Intraday breakout confirm",
        "timeframe": "intraday",
        "status": "live_overlay",
        "runnable": True,
        "match_ids": _STRATEGY_ALIASES["breakout_confirm"],
        "proxy_ids": (),
        "env_on_select": {"BREAKOUT_CONFIRM_ENABLED": "true"},
        "description": "Requires the daily Stage 2 name to trade through the breakout on the live quote before it stays on the shortlist. Overlay on the daily engine, not a separate minute-bar scanner.",
    },
    {
        "id": "gap_and_go",
        "display_name": "Gap and go",
        "timeframe": "intraday",
        "status": "shadow",
        "runnable": True,
        "match_ids": _STRATEGY_ALIASES["gap_and_go"],
        "proxy_ids": (),
        "env_on_select": {},
        "description": "Session-structure proxy on the latest daily bar: ≥1% opening gap that holds, close in the upper half of the range, volume vs the 50-day average, trend filter above the 200-day SMA. Shadow only — not a 1-minute gap-and-go book.",
    },
    {
        "id": "range_expansion",
        "display_name": "Range expansion",
        "timeframe": "intraday",
        "status": "shadow",
        "runnable": True,
        "match_ids": _STRATEGY_ALIASES["range_expansion"],
        "proxy_ids": (),
        "env_on_select": {},
        "description": "Opening-drive proxy: close through the prior high, today's true range ≥ 1.25× ATR(14), close in the top quartile, above the 50-day SMA. Shadow only.",
    },
    {
        "id": "trend_breakout",
        "display_name": "Stage 2 / VCP breakout",
        "timeframe": "daily",
        "status": "live",
        "runnable": True,
        "match_ids": _STRATEGY_ALIASES["trend_breakout"],
        "proxy_ids": (),
        "env_on_select": {},
        "description": "Weinstein Stage 2 uptrend plus volume contraction, ranked as a momentum breakout. This is the live book.",
    },
    {
        "id": "pullback",
        "display_name": "Trend pullback",
        "timeframe": "daily",
        "status": "shadow",
        "runnable": True,
        "match_ids": _STRATEGY_ALIASES["pullback"],
        "proxy_ids": (),
        "env_on_select": {},
        "description": "Uptrend names pulling back toward the 50-day SMA. Evaluated in shadow unless an operator has promoted STRATEGY_PULLBACK_MODE; selecting it here filters the scan, it does not go LIVE.",
    },
    {
        "id": "pead_primary",
        "display_name": "PEAD earnings drift",
        "timeframe": "daily",
        "status": "shadow",
        "runnable": True,
        "match_ids": _STRATEGY_ALIASES["pead_primary"],
        "proxy_ids": (),
        "env_on_select": {},
        "description": "Post-earnings announcement drift sleeve. Paper / canary only — Stage 2 still owns executable entries.",
    },
    {
        "id": "donchian_20",
        "display_name": "Donchian 20-day breakout",
        "timeframe": "daily",
        "status": "shadow",
        "runnable": True,
        "match_ids": _STRATEGY_ALIASES["donchian_20"],
        "proxy_ids": (),
        "env_on_select": {},
        "description": "Close through the prior 20-day high (channel excludes today) with a 200-day SMA trend filter. Classic Turtle / channel breakout. Shadow only — selecting it does not go LIVE.",
    },
    {
        "id": "nr7_breakout",
        "display_name": "NR7 breakout",
        "timeframe": "daily",
        "status": "shadow",
        "runnable": True,
        "match_ids": _STRATEGY_ALIASES["nr7_breakout"],
        "proxy_ids": (),
        "env_on_select": {},
        "description": "Mark Fisher NR7: yesterday was the narrowest range of the last 7 sessions, today closes above that bar's high, above the 50-day SMA. Shadow only.",
    },
    {
        "id": "weekly_swing",
        "display_name": "Weekly Stage 2",
        "timeframe": "weekly",
        "status": "research",
        "runnable": True,
        "match_ids": _STRATEGY_ALIASES["weekly_swing"],
        "proxy_ids": (),
        "env_on_select": {},
        "description": "Weinstein-style weekly Stage 2 on resampled Friday bars: close > 10-week SMA > 30-week SMA, 30-week SMA rising, within 15% of the 52-week high. Research — not the live book.",
    },
    {
        "id": "weekly_vcp",
        "display_name": "Weekly VCP",
        "timeframe": "weekly",
        "status": "research",
        "runnable": True,
        "match_ids": _STRATEGY_ALIASES["weekly_vcp"],
        "proxy_ids": (),
        "env_on_select": {},
        "description": "Multi-week volume contraction: last 5 weekly bars each print below the 10-week average volume, close above the 30-week SMA. Research only.",
    },
    {
        "id": "weekly_breakout",
        "display_name": "Weekly breakout",
        "timeframe": "weekly",
        "status": "research",
        "runnable": True,
        "match_ids": _STRATEGY_ALIASES["weekly_breakout"],
        "proxy_ids": (),
        "env_on_select": {},
        "description": "Weekly close through the prior week's high while above the 30-week SMA. Research only.",
    },
    {
        "id": "monthly_position",
        "display_name": "10-month SMA (Faber)",
        "timeframe": "monthly",
        "status": "research",
        "runnable": True,
        "match_ids": _STRATEGY_ALIASES["monthly_position"],
        "proxy_ids": (),
        "env_on_select": {},
        "description": "Meb Faber GTAA timing on month-end bars: monthly close above a rising 10-month SMA. Research — not a live monthly engine.",
    },
    {
        "id": "monthly_52w_high",
        "display_name": "Monthly 52-week high",
        "timeframe": "monthly",
        "status": "research",
        "runnable": True,
        "match_ids": _STRATEGY_ALIASES["monthly_52w_high"],
        "proxy_ids": (),
        "env_on_select": {},
        "description": "Position-style strength: month-end close within 5% of the 52-week high and above the 10-month SMA. Research only.",
    },
    {
        "id": "monthly_pullback",
        "display_name": "Monthly SMA pullback",
        "timeframe": "monthly",
        "status": "research",
        "runnable": True,
        "match_ids": _STRATEGY_ALIASES["monthly_pullback"],
        "proxy_ids": (),
        "env_on_select": {},
        "description": "Uptrend pullback toward the 10-month SMA: average rising, this month's low tagged it, close still holds above. Research only.",
    },
    {
        "id": "opening_range_breakout",
        "display_name": "Opening range breakout (RVOL proxy)",
        "timeframe": "intraday",
        "status": "research",
        "runnable": True,
        "match_ids": _STRATEGY_ALIASES["opening_range_breakout"],
        "proxy_ids": (),
        "env_on_select": {},
        "origin": "literature",
        "evidence": {
            "strength": "high",
            "citations": ["Zarattini, Barbon & Aziz 2024 (Swiss Finance Institute / SSRN)"],
            "caveat": "The paper uses 5-minute ORB on the top-20 relative-volume 'stocks in play'. This sleeve does not fetch 5-minute bars for the full universe; it is the daily RVOL + close-through-open/prior-high screen.",
        },
        "description": "Daily-bar stocks-in-play proxy of 5-minute opening-range breakout: relative volume ≥ 1.5×, close above the open and prior high, close in the upper half of the range. Research only — not a 5-minute ORB engine.",
    },
    {
        "id": "st_reversal_5d",
        "display_name": "5-day short-term reversal",
        "timeframe": "daily",
        "status": "research",
        "runnable": True,
        "match_ids": _STRATEGY_ALIASES["st_reversal_5d"],
        "proxy_ids": (),
        "env_on_select": {},
        "origin": "literature",
        "evidence": {
            "strength": "high",
            "citations": ["Jegadeesh 1990", "Lehmann 1990", "Avramov, Chordia, Goyal 2006"],
            "caveat": "Academic profits concentrate in small, high-turnover, illiquid names. This long-only loser-bounce adds a 50-day volume floor; paper Sharpes will not survive your fills.",
        },
        "description": "Long-only 1-week reversal: 5-session return ≤ −8%, today closes above yesterday (turn), 50-day average volume ≥ 200k. Research — not the live book.",
    },
    {
        "id": "overnight_gap_fade",
        "display_name": "Overnight gap fade",
        "timeframe": "daily",
        "status": "research",
        "runnable": True,
        "match_ids": _STRATEGY_ALIASES["overnight_gap_fade"],
        "proxy_ids": (),
        "env_on_select": {},
        "origin": "literature",
        "evidence": {
            "strength": "medium",
            "citations": ["Bogousslavsky 2021", "NY Fed / Liberty Street Economics overnight drift series"],
            "caveat": "Overnight premium is close-to-open hold, and has been decaying since 2020. This sleeve is the long gap-down fade, distinct from gap-and-go continuation.",
        },
        "description": "Long-only fade of a ≥1.5% opening gap down: close recovers at least half the gap. Distinct from gap-and-go (gap-up continuation). Research only.",
    },
    {
        "id": "weekly_reversal",
        "display_name": "Weekly loser reversal",
        "timeframe": "weekly",
        "status": "research",
        "runnable": True,
        "match_ids": _STRATEGY_ALIASES["weekly_reversal"],
        "proxy_ids": (),
        "env_on_select": {},
        "origin": "literature",
        "evidence": {
            "strength": "medium",
            "citations": ["Jegadeesh 1990", "Lehmann 1990", "Avramov et al. 2006"],
            "caveat": "Same liquidity warning as the daily 5-day reversal: costs eat the edge in microcaps.",
        },
        "description": "Prior week's return ≤ −6% and this week closes above last week's close (loser bounce on Friday bars). Research only.",
    },
    {
        "id": "momentum_12_1",
        "display_name": "12-1 momentum",
        "timeframe": "monthly",
        "status": "research",
        "runnable": True,
        "match_ids": _STRATEGY_ALIASES["momentum_12_1"],
        "proxy_ids": (),
        "env_on_select": {},
        "origin": "literature",
        "evidence": {
            "strength": "high",
            "citations": ["Jegadeesh & Titman 1993", "Jegadeesh & Titman 2001 replication"],
            "caveat": "The paper is cross-sectional winner-minus-loser. This is a single-name 12-1 strength screen (skip last month, ≥20% formation return, above 200-day SMA), not a WML portfolio.",
        },
        "description": "12-month formation skipping the most recent month: return from 252 to 21 days ago ≥ 20%, last close above the 200-day SMA. Most replicated equity anomaly. Research — not LIVE.",
    },
    {
        "id": "tsmom_12m",
        "display_name": "12-month time-series momentum",
        "timeframe": "monthly",
        "status": "research",
        "runnable": True,
        "match_ids": _STRATEGY_ALIASES["tsmom_12m"],
        "proxy_ids": (),
        "env_on_select": {},
        "origin": "literature",
        "evidence": {
            "strength": "high",
            "citations": ["Moskowitz, Ooi & Pedersen 2012"],
            "caveat": "Original TSMOM is a diversified futures overlay (crisis alpha). This is the single-name analog: month-end close above the close 12 months earlier.",
        },
        "description": "Month-end close above the close 12 months ago. Distinct from the 10-month SMA Faber sleeve. Research only.",
    },
)

UNIVERSES: tuple[dict[str, Any], ...] = (
    {
        "id": "sp1500",
        "display_name": "S&P 1500",
        "description": "S&P 500 + MidCap 400 + SmallCap 600. Default live universe.",
        "available": True,
        "approx_size": 1500,
        "watchlist_source": "sp1500_default",
    },
    {
        "id": "sp500",
        "display_name": "S&P 500",
        "description": "Large-cap US names only — faster than the full 1500.",
        "available": True,
        "approx_size": 500,
        "watchlist_source": "universe_sp500",
    },
    {
        "id": "nasdaq100",
        "display_name": "Nasdaq-100",
        "description": "Nasdaq-100 constituents (Wikipedia, cached daily).",
        "available": True,
        "approx_size": 100,
        "watchlist_source": "universe_nasdaq100",
    },
    {
        "id": "sector_etfs",
        "display_name": "Sector ETFs",
        "description": "Liquid sector and index ETFs (SPY, QQQ, IWM, XL*). Useful for a tiny smoke test.",
        "available": True,
        "approx_size": 15,
        "watchlist_source": "universe_sector_etfs",
    },
    {
        "id": "focused",
        "display_name": "S&P 1500 sample",
        "description": "Deterministic subset of S&P 1500 for a faster scan. Not the full index.",
        "available": True,
        "approx_size": 250,
        "watchlist_source": "sp1500_focused",
    },
    {
        "id": "custom",
        "display_name": "Custom tickers",
        "description": "Paste your own symbol list. Capped per SAAS_SCAN_MAX_CUSTOM_TICKERS (default 40).",
        "available": True,
        "approx_size": None,
        "watchlist_source": "explicit_tickers_override",
    },
    {
        "id": "russell2000",
        "display_name": "Russell 2000",
        "description": "Small-cap index. Constituent feed is not wired yet — use custom tickers or Nasdaq-100.",
        "available": False,
        "approx_size": 2000,
        "watchlist_source": None,
    },
)

DEFAULT_TIMEFRAME = "daily"
DEFAULT_UNIVERSE = "sp1500"
DEFAULT_STRATEGY_IDS: tuple[str, ...] = ("trend_breakout",)

# Operator-iterated sleeves. Literature rows are additive — never remove these ids.
ITERATED_STRATEGY_IDS: frozenset[str] = frozenset(
    {
        "breakout_confirm",
        "gap_and_go",
        "range_expansion",
        "trend_breakout",
        "pullback",
        "pead_primary",
        "donchian_20",
        "nr7_breakout",
        "weekly_swing",
        "weekly_vcp",
        "weekly_breakout",
        "monthly_position",
        "monthly_52w_high",
        "monthly_pullback",
    }
)

_UNIVERSE_IDS = {str(u["id"]) for u in UNIVERSES}
_AVAILABLE_UNIVERSE_IDS = {str(u["id"]) for u in UNIVERSES if u.get("available")}
_STRATEGY_BY_ID = {str(s["id"]): s for s in STRATEGIES}
_TIMEFRAME_IDS = {str(t["id"]) for t in TIMEFRAMES}


def known_universe_ids(*, available_only: bool = False) -> frozenset[str]:
    return frozenset(_AVAILABLE_UNIVERSE_IDS if available_only else _UNIVERSE_IDS)


def known_strategy_ids() -> frozenset[str]:
    return frozenset(_STRATEGY_BY_ID)


def known_timeframe_ids() -> frozenset[str]:
    return frozenset(_TIMEFRAME_IDS)


def get_strategy(strategy_id: str) -> dict[str, Any] | None:
    return _STRATEGY_BY_ID.get(str(strategy_id or "").strip().lower())


def get_universe(universe_id: str) -> dict[str, Any] | None:
    key = str(universe_id or "").strip().lower()
    for row in UNIVERSES:
        if str(row["id"]) == key:
            return row
    return None


def lookup_strategy(raw_id: str | None) -> dict[str, Any] | None:
    """Resolve a plugin / entry-family / catalog id to a catalog row."""
    key = str(raw_id or "").strip().lower()
    if not key:
        return None
    direct = _STRATEGY_BY_ID.get(key)
    if direct is not None:
        return direct
    for row in STRATEGIES:
        aliases = {str(a).lower() for a in (row.get("match_ids") or ())}
        if key in aliases:
            return row
    return None


def default_strategy_ids_for_timeframe(timeframe: str | None) -> list[str]:
    tf = str(timeframe or "").strip().lower()
    if tf not in _TIMEFRAME_IDS:
        return list(DEFAULT_STRATEGY_IDS)
    ids = [str(s["id"]) for s in STRATEGIES if s.get("timeframe") == tf and s.get("runnable")]
    return ids or list(DEFAULT_STRATEGY_IDS)


def resolve_strategy_ids(
    strategy_ids: list[str] | None,
    *,
    scan_timeframe: str | None = None,
) -> list[str]:
    """Return validated catalog ids. Empty means 'no filter' (show the full book)."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in strategy_ids or []:
        key = str(raw or "").strip().lower()
        if key in _STRATEGY_BY_ID and key not in seen:
            seen.add(key)
            cleaned.append(key)
    if cleaned:
        return cleaned
    if scan_timeframe:
        return default_strategy_ids_for_timeframe(scan_timeframe)
    return []


def env_overrides_for_strategies(strategy_ids: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for sid in strategy_ids:
        row = _STRATEGY_BY_ID.get(sid)
        extra = row.get("env_on_select") if isinstance(row, dict) else None
        if isinstance(extra, dict):
            for k, v in extra.items():
                out[str(k)] = str(v)
    return out


def watchlist_source_for_preset(universe_preset: str | None) -> str:
    row = get_universe(universe_preset or DEFAULT_UNIVERSE)
    src = (row or {}).get("watchlist_source")
    if isinstance(src, str) and src.strip():
        return src
    return "sp1500_default"


def catalog_fields_for_signal(signal: dict[str, Any] | None) -> dict[str, Any]:
    """Metadata to attach onto strategy_attribution for UI labels/tooltips."""
    attr = signal.get("strategy_attribution") if isinstance(signal, dict) else None
    top_live = None
    top_shadow = None
    if isinstance(attr, dict):
        top_live = attr.get("top_live")
        top_shadow = attr.get("top_shadow")
    family = None
    triggered_name = None
    if isinstance(signal, dict):
        family = signal.get("entry_family")
        plugins = signal.get("strategy_plugins") if isinstance(signal.get("strategy_plugins"), list) else []
        for plugin in plugins:
            if not isinstance(plugin, dict) or not plugin.get("triggered"):
                continue
            name = str(plugin.get("name") or "").strip().lower()
            if name and name != "trend_breakout":
                triggered_name = name
                if str(plugin.get("mode") or "").lower() != "live":
                    break
    row = None
    if str(family or "") == "horizon":
        row = lookup_strategy(str(triggered_name or "")) or lookup_strategy(str(top_shadow or ""))
    if row is None:
        row = lookup_strategy(str(top_live or "")) or lookup_strategy(str(family or ""))
    if row is None:
        row = _STRATEGY_BY_ID["trend_breakout"]
    return {
        "catalog_id": row["id"],
        "display_name": row["display_name"],
        "timeframe": row["timeframe"],
        "description": row["description"],
        "status": row["status"],
    }


def _signal_match_tokens(signal: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    attr = signal.get("strategy_attribution") if isinstance(signal.get("strategy_attribution"), dict) else {}
    for key in ("top_live", "top_shadow", "catalog_id"):
        val = str(attr.get(key) or "").strip().lower()
        if val:
            tokens.add(val)
    family = str(signal.get("entry_family") or "").strip().lower()
    if family:
        tokens.add(family)
        if family == "both":
            tokens.update({"stage2", "pead_primary", "trend_breakout"})
        elif family == "stage2":
            tokens.add("trend_breakout")
    if signal.get("breakout_confirmed"):
        tokens.add("breakout_confirm")
    plugins = signal.get("strategy_plugins") if isinstance(signal.get("strategy_plugins"), list) else []
    for plugin in plugins:
        if not isinstance(plugin, dict) or not plugin.get("triggered"):
            continue
        name = str(plugin.get("name") or "").strip().lower()
        if name:
            tokens.add(name)
    return tokens


def signal_matches_strategy_ids(signal: dict[str, Any], strategy_ids: list[str]) -> bool:
    if not strategy_ids:
        return True
    tokens = _signal_match_tokens(signal)
    for sid in strategy_ids:
        row = _STRATEGY_BY_ID.get(sid)
        if row is None:
            continue
        wanted = {str(a).lower() for a in (row.get("match_ids") or ())}
        wanted.add(str(row["id"]))
        for proxy in row.get("proxy_ids") or ():
            proxy_row = _STRATEGY_BY_ID.get(str(proxy))
            if proxy_row is not None:
                wanted.update(str(a).lower() for a in (proxy_row.get("match_ids") or ()))
                wanted.add(str(proxy_row["id"]))
        if tokens & wanted:
            return True
    return False


def filter_signals_for_scan_selection(
    signals: list[dict[str, Any]] | None,
    strategy_ids: list[str] | None,
) -> list[dict[str, Any]]:
    rows = [s for s in (signals or []) if isinstance(s, dict)]
    ids = [str(s) for s in (strategy_ids or []) if str(s)]
    if not ids:
        return rows
    return [s for s in rows if signal_matches_strategy_ids(s, ids)]


def _strategy_payload_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    sid = str(out.get("id") or "")
    out.setdefault("origin", "iterated" if sid in ITERATED_STRATEGY_IDS else "literature")
    if not isinstance(out.get("evidence"), dict):
        out["evidence"] = {}
    return out


def build_scan_catalog_payload() -> dict[str, Any]:
    return {
        "timeframes": [dict(t) for t in TIMEFRAMES],
        "strategies": [_strategy_payload_row(dict(s)) for s in STRATEGIES],
        "universes": [dict(u) for u in UNIVERSES],
        "defaults": {
            "timeframe": DEFAULT_TIMEFRAME,
            "universe_preset": DEFAULT_UNIVERSE,
            "strategy_ids": list(DEFAULT_STRATEGY_IDS),
        },
        "notes": {
            "bar_engine": "daily",
            "weekly_monthly": "Weekly/monthly sleeves resample the daily OHLCV already fetched (Friday weeks, month-end). They are research/shadow evaluators, not a separate vendor bar feed, and they do not promote to LIVE.",
            "plugin_promotion": "Selecting a shadow strategy filters results for this scan; it does not promote plugins to LIVE.",
            "strategy_merge": "Literature sleeves are additive. Iterated ids (Yours) are never removed when paper strategies are added.",
        },
    }
