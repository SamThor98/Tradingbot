"""FRED SHADOW-only regime features (rates / credit spreads). Not a live hard gate."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

LOG = logging.getLogger(__name__)

# Publication lag: use observations strictly before asof (PIT).
FRED_SERIES = {
    "yield_curve_spread": "T10Y2Y",
    "credit_spread_baa_aaa": "BAA10Y",  # Moody's BAA less 10Y; fail-closed if missing
}


def fetch_fred_series(series_id: str, *, api_key: str, asof: str) -> float | None:
    """Latest observation with date < asof. None if unavailable — never invent 0."""
    if not api_key:
        return None
    try:
        import requests
    except Exception:
        return None
    url = "https://api.stlouisfed.org/fred/series/observations"
    try:
        resp = requests.get(
            url,
            params={
                "series_id": series_id,
                "api_key": api_key,
                "file_type": "json",
                "observation_start": "2010-01-01",
                "observation_end": str(asof)[:10],
                "sort_order": "desc",
                "limit": 8,
            },
            timeout=20,
        )
    except Exception as exc:
        LOG.warning("FRED request failed: %s", exc)
        return None
    if resp.status_code != 200:
        LOG.warning("FRED http_status=%s series=%s", resp.status_code, series_id)
        return None
    try:
        payload = resp.json()
    except Exception:
        return None
    asof_s = str(asof)[:10]
    for obs in payload.get("observations") or []:
        d = str(obs.get("date") or "")[:10]
        if not d or d >= asof_s:
            continue  # PIT: no same-day or future print
        raw = obs.get("value")
        if raw in (None, "."):
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return None


def attach_fred_shadow(
    df: pd.DataFrame,
    *,
    skill_dir: Path | None = None,
    asof_col: str = "asof_date",
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Join FRED features when FRED_REGIME_MODE=shadow. Never a live gate."""
    from config import get_fred_api_key, get_fred_regime_mode

    mode = get_fred_regime_mode(skill_dir)
    quality: dict[str, Any] = {"mode": mode, "live_gate": False}
    if mode != "shadow":
        quality["status"] = "skipped"
        quality["reason"] = f"fred_regime_mode={mode}"
        return df, quality
    key = get_fred_api_key(skill_dir)
    if not key:
        quality["status"] = "error"
        quality["reason"] = "missing_fred_api_key"
        quality["data_quality"] = "missing_fred_api_key"
        LOG.warning("FRED SHADOW fail-closed: %s", quality["reason"])
        return df, quality
    work = df.copy()
    if asof_col not in work.columns:
        quality["status"] = "error"
        quality["reason"] = "missing_asof_date"
        return work, quality
    cache: dict[tuple[str, str], float | None] = {}
    for col, series_id in FRED_SERIES.items():
        values: list[float | None] = []
        for asof in pd.to_datetime(work[asof_col]).dt.strftime("%Y-%m-%d"):
            ck = (series_id, asof)
            if ck not in cache:
                cache[ck] = fetch_fred_series(series_id, api_key=key, asof=asof)
            values.append(cache[ck])
        work[col] = values
    quality["status"] = "ok"
    quality["reason"] = "fred_shadow_joined"
    return work, quality
