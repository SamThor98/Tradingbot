"""Point-in-time setup features for the offline rule search.

The trade chunks do not carry ``pct_below_52w_high``, ``atr_pct``, or
``sector_breadth50``. This module reads the lab bar cache and writes a
sidecar. It does not rewrite chunk files, call the network, or change the
live scan.

Formulas match ``core.shadow_setup_filter.bar_features`` and
``stage_analysis.add_indicators``:

- ``pct_below_52w_high`` is ``(max high - close) / max high`` on the last
  ``min(252, n)`` bars whose date is on or before the entry date.
- ``atr_pct`` is ATR(14) / close * 100. ATR comes from ``add_indicators``.
- ``sector_breadth50`` is the share of bar-cache names in that sector whose
  close is above their SMA50 on that date. A name's sector is the last
  ``sector_etf`` on a pool trade on or before the date, or that name's modal
  sector when it has no earlier label. The trade itself uses its own
  ``sector_etf``. A blank sector leaves breadth null.

A trade whose entry date is not a bar in the cache stays null. A later bar
is never used. Live R2 uses whatever names that scan loaded, so this breadth
is a reconstruction from the cache, not a copy of the live ratio.
"""

from __future__ import annotations

import json
import logging
import os
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

LOG = logging.getLogger(__name__)

LOOKBACK = 252
SIDECAR_DIR = "setup_rule_features"
BARS_RELATIVE = ".tab_agent_rebuild/bars.pkl"
FEATURE_FIELDS = (
    "pct_below_52w_high",
    "atr_pct",
    "sector_breadth50",
    "high_52w",
    "atr_14",
    "sector_breadth_n",
    "sector_breadth_above",
)
BREADTH_NOTE = (
    "sector_breadth50 is reconstructed from the bar cache: on each entry date, "
    "the share of cache names in that sector whose close is above their SMA50. "
    "Live R2 uses the names loaded in that scan, so the two ratios can differ."
)


class BarsFormatError(ValueError):
    """The bar cache is not a ticker map or a long OHLCV frame."""


@dataclass
class PreparedBars:
    dates: np.ndarray
    close: np.ndarray
    high52: np.ndarray
    atr: np.ndarray
    above: np.ndarray
    sma_ok: np.ndarray


def bars_path(skill_dir: Path, override: str | None = None) -> Path:
    if override:
        return Path(override)
    return Path(skill_dir) / BARS_RELATIVE


def default_sidecar(pool: Path) -> Path:
    """``validation_artifacts/setup_rule_features/<pool>.jsonl`` for a chunk dir."""
    name = f"{pool.name}.jsonl"
    if pool.parent.name == "multi_era_chunks":
        return pool.parent.parent / SIDECAR_DIR / name
    return pool.parent / SIDECAR_DIR / name


def discover_sidecar(pool: Path) -> Path | None:
    if not pool.is_dir():
        return None
    candidate = default_sidecar(pool)
    if candidate.is_file():
        return candidate
    return None


def _round(value: float | None, places: int) -> float | None:
    if value is None or not np.isfinite(value):
        return None
    return round(float(value), places)


def _day(value: Any) -> np.datetime64 | None:
    if value is None or value == "":
        return None
    try:
        stamp = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(stamp):
        return None
    if stamp.tzinfo is not None:
        stamp = stamp.tz_convert("UTC").tz_localize(None)
    try:
        day = np.datetime64(stamp.to_datetime64(), "D")
    except (TypeError, ValueError):
        return None
    if np.isnat(day):
        return None
    return day


def _day_text(day: np.datetime64) -> str:
    return str(np.datetime_as_string(day, unit="D"))


def load_bars(path: Path) -> dict[str, pd.DataFrame]:
    """Read ``bars.pkl``.

    Accepted shapes, matching ``core.tab_agent_rebuild.BarCache`` and a
    long table:

    - ``dict[ticker, DataFrame]`` with high, low, close
    - one DataFrame with ``ticker`` plus ``date`` (or a datetime index) and OHLC
    """
    if not path.is_file():
        raise FileNotFoundError(f"Bar cache is not on disk: {path}")
    try:
        import pickle

        with path.open("rb") as handle:
            loaded = pickle.load(handle)
    except (OSError, pickle.UnpicklingError, EOFError) as exc:
        raise BarsFormatError(f"Could not read bar cache {path}: {exc}") from exc
    frames = _frames_from_loaded(loaded)
    if not frames:
        raise BarsFormatError(
            f"{path} has no ticker frames. Expected a dict[ticker]->DataFrame "
            "or a long DataFrame with ticker and date columns."
        )
    return frames


def _frames_from_loaded(loaded: Any) -> dict[str, pd.DataFrame]:
    if isinstance(loaded, pd.DataFrame):
        return _frames_from_long(loaded)
    if isinstance(loaded, dict):
        frames: dict[str, pd.DataFrame] = {}
        rejected = 0
        for key, value in loaded.items():
            ticker = str(key or "").upper().strip()
            if not ticker:
                continue
            if isinstance(value, pd.DataFrame):
                frames[ticker] = value
            else:
                rejected += 1
        if frames:
            return frames
        if rejected:
            raise BarsFormatError(
                "Bar cache values are not DataFrames. Expected dict[ticker]->DataFrame "
                "or a long DataFrame with ticker and date columns."
            )
        return {}
    raise BarsFormatError(
        f"Bar cache type {type(loaded).__name__} is not supported. "
        "Expected dict[ticker]->DataFrame or a long DataFrame with ticker and date columns."
    )


def _frames_from_long(frame: pd.DataFrame) -> dict[str, pd.DataFrame]:
    columns = {str(column).strip().lower(): column for column in frame.columns}
    ticker_col = columns.get("ticker") or columns.get("symbol")
    if ticker_col is None:
        raise BarsFormatError("Long bar frame needs a ticker column.")
    work = frame.copy()
    work.columns = [str(column).strip().lower() for column in work.columns]
    if "date" in work.columns:
        work = work.set_index("date")
    elif "datetime" in work.columns:
        work = work.set_index("datetime")
    elif not isinstance(work.index, pd.DatetimeIndex):
        raise BarsFormatError("Long bar frame needs a date column or a datetime index.")
    out: dict[str, pd.DataFrame] = {}
    ticker_key = "ticker" if "ticker" in work.columns else "symbol"
    for ticker, part in work.groupby(ticker_key, sort=False):
        name = str(ticker or "").upper().strip()
        if not name:
            continue
        out[name] = part.drop(columns=[ticker_key])
    return out


def _normalize_ohlc(frame: pd.DataFrame) -> pd.DataFrame | None:
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        return None
    work = frame.copy()
    work.columns = [str(column).strip().lower() for column in work.columns]
    if "date" in work.columns:
        work = work.set_index("date")
    elif "datetime" in work.columns:
        work = work.set_index("datetime")
    if "close" not in work.columns or "high" not in work.columns or "low" not in work.columns:
        return None
    idx = pd.to_datetime(work.index, errors="coerce", utc=True).tz_convert(None)
    work.index = pd.DatetimeIndex(idx).normalize()
    work = work[~work.index.isna()]
    work = work.sort_index()
    work = work[~work.index.duplicated(keep="last")]
    for column in ("high", "low", "close"):
        work[column] = pd.to_numeric(work[column], errors="coerce")
    if "volume" not in work.columns:
        work["volume"] = 0.0
    else:
        work["volume"] = pd.to_numeric(work["volume"], errors="coerce").fillna(0.0)
    work = work.dropna(subset=["high", "low", "close"])
    if work.empty:
        return None
    return work


def prepare_bars(frame: pd.DataFrame) -> PreparedBars | None:
    """Indicators on the full cache. Trailing windows do not use later bars."""
    from stage_analysis import add_indicators

    normalized = _normalize_ohlc(frame)
    if normalized is None:
        return None
    enriched = add_indicators(normalized)
    high = enriched["high"].astype(float)
    close = enriched["close"].astype(float)
    high52 = high.rolling(LOOKBACK, min_periods=1).max()
    atr = pd.to_numeric(enriched["atr_14"], errors="coerce")
    sma = pd.to_numeric(enriched["sma_50"], errors="coerce")
    dates = enriched.index.values.astype("datetime64[D]")
    close_np = close.to_numpy(dtype=float)
    sma_np = sma.to_numpy(dtype=float)
    sma_ok = np.isfinite(sma_np) & (sma_np > 0) & np.isfinite(close_np)
    above = sma_ok & (close_np > sma_np)
    return PreparedBars(
        dates=dates,
        close=close_np,
        high52=high52.to_numpy(dtype=float),
        atr=atr.to_numpy(dtype=float),
        above=above,
        sma_ok=sma_ok,
    )


def features_at(prep: PreparedBars, day: np.datetime64) -> dict[str, float | None]:
    """Geometry on the entry bar only. A missing bar stays null."""
    idx = int(np.searchsorted(prep.dates, day))
    if idx >= prep.dates.size or prep.dates[idx] != day:
        return {"pct_below_52w_high": None, "atr_pct": None, "high_52w": None, "atr_14": None}
    close = float(prep.close[idx])
    high52 = float(prep.high52[idx])
    atr = float(prep.atr[idx])
    if not np.isfinite(close) or close <= 0 or not np.isfinite(high52) or high52 <= 0:
        pct = None
        high_out = None
    else:
        pct = (high52 - close) / high52
        high_out = high52
    if not np.isfinite(atr) or atr < 0 or not np.isfinite(close) or close <= 0:
        atr_pct = None
        atr_out = None
    else:
        atr_pct = (atr / close) * 100.0
        atr_out = atr
    return {
        "pct_below_52w_high": _round(pct, 6),
        "atr_pct": _round(atr_pct, 4),
        "high_52w": _round(high_out, 4),
        "atr_14": _round(atr_out, 6),
    }


def _sector_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().upper()
    if text in {"", "NONE", "NAN", "NULL"}:
        return ""
    return text


@dataclass
class SectorHist:
    dates: np.ndarray
    labels: np.ndarray
    modal: str | None

    def on(self, day: np.datetime64) -> str | None:
        if self.dates.size:
            idx = int(np.searchsorted(self.dates, day, side="right")) - 1
            if idx >= 0:
                return str(self.labels[idx])
        return self.modal


def sector_histories(trades: Iterable[dict[str, Any]]) -> dict[str, SectorHist]:
    """Last ``sector_etf`` on or before a date, with a modal fallback."""
    grouped: dict[str, list[tuple[np.datetime64, str]]] = {}
    counts: dict[str, Counter[str]] = {}
    for trade in trades:
        if not isinstance(trade, dict):
            continue
        ticker = str(trade.get("ticker") or "").upper().strip()
        sector = _sector_text(trade.get("sector_etf"))
        day = _day(trade.get("entry_date"))
        if not ticker or not sector or day is None:
            continue
        grouped.setdefault(ticker, []).append((day, sector))
        counts.setdefault(ticker, Counter())[sector] += 1
    out: dict[str, SectorHist] = {}
    for ticker, pairs in grouped.items():
        pairs.sort(key=lambda item: (item[0], item[1]))
        dates_list: list[np.datetime64] = []
        labels: list[str] = []
        for day, sector in pairs:
            if dates_list and dates_list[-1] == day:
                labels[-1] = sector
            else:
                dates_list.append(day)
                labels.append(sector)
        modal = _modal(counts.get(ticker) or Counter())
        out[ticker] = SectorHist(
            dates=np.array(dates_list, dtype="datetime64[D]"),
            labels=np.array(labels, dtype=object),
            modal=modal,
        )
    return out


def _modal(counts: Counter[str]) -> str | None:
    if not counts:
        return None
    best = max(counts.values())
    winners = sorted(label for label, count in counts.items() if count == best)
    return winners[0]


def breadth_by_day(
    prepared: dict[str, PreparedBars],
    histories: dict[str, SectorHist],
    days: set[np.datetime64],
) -> dict[tuple[np.datetime64, str], tuple[int, int]]:
    """``(above, n)`` for each sector on each needed entry date."""
    totals: dict[tuple[np.datetime64, str], list[int]] = {}
    empty = SectorHist(dates=np.array([], dtype="datetime64[D]"), labels=np.array([], dtype=object), modal=None)
    for ticker, prep in prepared.items():
        hist = histories.get(ticker, empty)
        if hist.modal is None and hist.dates.size == 0:
            continue
        for pos, day in enumerate(prep.dates):
            if day not in days or not bool(prep.sma_ok[pos]):
                continue
            sector = hist.on(day)
            if not sector:
                continue
            bucket = totals.setdefault((day, sector), [0, 0])
            bucket[1] += 1
            if bool(prep.above[pos]):
                bucket[0] += 1
    return {key: (above, total) for key, (above, total) in totals.items() if total > 0}


def enrich_records(
    trades: list[dict[str, Any]],
    bars: dict[str, pd.DataFrame],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One sidecar row per ticker and entry date. Chunks are not modified."""
    prepared: dict[str, PreparedBars] = {}
    for ticker, frame in bars.items():
        name = str(ticker or "").upper().strip()
        if not name:
            continue
        prep = prepare_bars(frame)
        if prep is not None:
            prepared[name] = prep
    if bars and not prepared:
        raise BarsFormatError("None of the bar frames had high, low, and close columns.")
    histories = sector_histories(trades)
    needed: set[np.datetime64] = set()
    keys: list[tuple[str, np.datetime64]] = []
    seen: set[tuple[str, np.datetime64]] = set()
    trade_sector: dict[tuple[str, np.datetime64], str] = {}
    for trade in trades:
        if not isinstance(trade, dict):
            continue
        ticker = str(trade.get("ticker") or "").upper().strip()
        day = _day(trade.get("entry_date"))
        if not ticker or day is None:
            continue
        key = (ticker, day)
        if key not in seen:
            seen.add(key)
            keys.append(key)
            needed.add(day)
        sector = _sector_text(trade.get("sector_etf"))
        if sector and key not in trade_sector:
            trade_sector[key] = sector
    breadth = breadth_by_day(prepared, histories, needed)
    rows: list[dict[str, Any]] = []
    for ticker, day in keys:
        geom = (
            features_at(prepared[ticker], day)
            if ticker in prepared
            else {"pct_below_52w_high": None, "atr_pct": None, "high_52w": None, "atr_14": None}
        )
        sector = trade_sector.get((ticker, day), "")
        above_n = breadth.get((day, sector)) if sector else None
        if above_n is None:
            breadth_value = None
            n_value = None
            above_value = None
        else:
            above_value, n_value = above_n
            breadth_value = _round(above_value / n_value, 6) if n_value else None
        rows.append(
            {
                "ticker": ticker,
                "entry_date": _day_text(day),
                "pct_below_52w_high": geom["pct_below_52w_high"],
                "atr_pct": geom["atr_pct"],
                "high_52w": geom["high_52w"],
                "atr_14": geom["atr_14"],
                "sector_breadth50": breadth_value,
                "sector_breadth_n": n_value,
                "sector_breadth_above": above_value,
            }
        )
    meta = {
        "universe_n": len(prepared),
        "rows": len(rows),
        "breadth_note": BREADTH_NOTE,
    }
    return rows, meta


def write_sidecar(path: Path, rows: list[dict[str, Any]], meta: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = {
        "record_type": "meta",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "live_scan_changed": False,
        "orders_touched": False,
        **meta,
    }
    lines = [json.dumps(header, sort_keys=True)]
    ordered = sorted(rows, key=lambda row: (str(row.get("ticker")), str(row.get("entry_date"))))
    lines.extend(json.dumps(row, sort_keys=True) for row in ordered)
    payload = "\n".join(lines) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, path)
    return path


def read_sidecar(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not path.is_file():
        return [], {}
    rows: list[dict[str, Any]] = []
    meta: dict[str, Any] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("record_type") == "meta":
            meta = payload
        else:
            rows.append(payload)
    return rows, meta


def merge_sidecar(records: list[dict[str, Any]], rows: list[dict[str, Any]]) -> int:
    """Fill missing feature fields. Existing values and the chunk files stay put."""
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        ticker = str(row.get("ticker") or "").upper().strip()
        day = _day(row.get("entry_date"))
        if ticker and day is not None:
            index[(ticker, _day_text(day))] = row
    filled = 0
    for record in records:
        if not isinstance(record, dict):
            continue
        ticker = str(record.get("ticker") or "").upper().strip()
        day = _day(record.get("entry_date"))
        if not ticker or day is None:
            continue
        side = index.get((ticker, _day_text(day)))
        if side is None:
            continue
        for field in FEATURE_FIELDS:
            if _missing(record.get(field)) and not _missing(side.get(field)):
                record[field] = side.get(field)
                filled += 1
    return filled


def _missing(value: Any) -> bool:
    if value is None or value == "":
        return True
    if isinstance(value, float) and not np.isfinite(value):
        return True
    return False


def feature_coverage(records: list[dict[str, Any]]) -> dict[str, Any]:
    n = sum(1 for record in records if isinstance(record, dict))
    out: dict[str, Any] = {"n": n}
    for field in ("pct_below_52w_high", "atr_pct", "sector_breadth50"):
        have = sum(1 for record in records if isinstance(record, dict) and not _missing(record.get(field)))
        out[field] = {"n": have, "pct": round(100.0 * have / n, 2) if n else None}
    return out


def _newest_mtime(path: Path) -> float:
    if not path.exists():
        return 0.0
    if path.is_file():
        return path.stat().st_mtime
    latest = path.stat().st_mtime
    for chunk in path.rglob("chunk_*.json"):
        if chunk.name.endswith("_tickers.json"):
            continue
        latest = max(latest, chunk.stat().st_mtime)
    return latest


def sidecar_is_fresh(sidecar: Path, bars: Path, pool: Path) -> bool:
    if not sidecar.is_file():
        return False
    stamp = sidecar.stat().st_mtime
    if bars.is_file() and bars.stat().st_mtime > stamp:
        return False
    return _newest_mtime(pool) <= stamp


def enrich_pool(pool: Path, bars_file: Path, sidecar: Path) -> dict[str, Any]:
    """Build the sidecar from chunks and bars. Raises if the inputs are unusable."""
    from core.setup_rule_search import load_trade_records

    if not pool.exists():
        raise FileNotFoundError(f"Trade pool is not on disk: {pool}")
    trades = load_trade_records(pool, merge_features=False)
    if not trades:
        raise FileNotFoundError(f"Trade pool has no trades: {pool}")
    bars = load_bars(bars_file)
    rows, meta = enrich_records(trades, bars)
    write_sidecar(sidecar, rows, meta)
    merged = [dict(trade) for trade in trades]
    merge_sidecar(merged, rows)
    LOG.info(
        "Setup-rule features wrote %s rows for %s names to %s",
        len(rows),
        meta.get("universe_n"),
        sidecar,
    )
    return {
        "status": "written",
        "path": str(sidecar),
        "rows": len(rows),
        "universe_n": meta.get("universe_n"),
        "coverage": feature_coverage(merged),
        "breadth_note": BREADTH_NOTE,
        "orders_touched": False,
        "live_scan_changed": False,
    }


def ensure_setup_features(
    skill_dir: Path,
    pool: Path,
    *,
    bars_file: Path | None = None,
    sidecar: Path | None = None,
) -> dict[str, Any]:
    """Refresh the sidecar when bars exist. A missing cache does not raise."""
    from config import get_setup_rule_features_bars, get_setup_rule_search_features_path

    bars = bars_file or bars_path(skill_dir, get_setup_rule_features_bars(skill_dir) or None)
    target = sidecar or _configured_sidecar(skill_dir, pool, get_setup_rule_search_features_path(skill_dir))
    if not bars.is_file():
        return {
            "status": "features_missing",
            "path": str(target),
            "bars": str(bars),
            "note": (
                f"features_missing: bar cache {bars} is not on disk, so "
                "pct_below_52w_high, atr_pct, and sector_breadth50 were not enriched."
            ),
            "orders_touched": False,
            "live_scan_changed": False,
        }
    if sidecar_is_fresh(target, bars, pool):
        _rows, meta = read_sidecar(target)
        return {
            "status": "fresh",
            "path": str(target),
            "bars": str(bars),
            "universe_n": meta.get("universe_n"),
            "breadth_note": meta.get("breadth_note") or BREADTH_NOTE,
            "orders_touched": False,
            "live_scan_changed": False,
        }
    try:
        result = enrich_pool(pool, bars, target)
    except (OSError, BarsFormatError, FileNotFoundError, ValueError) as exc:
        LOG.warning("Setup-rule feature enricher skipped: %s", exc)
        return {
            "status": "features_missing",
            "path": str(target),
            "bars": str(bars),
            "note": f"features_missing: {exc}",
            "orders_touched": False,
            "live_scan_changed": False,
        }
    result["bars"] = str(bars)
    return result


def _configured_sidecar(skill_dir: Path, pool: Path, override: str) -> Path:
    if override:
        return Path(override)
    if pool.is_dir():
        return default_sidecar(pool)
    return Path(skill_dir) / "validation_artifacts" / SIDECAR_DIR / f"{pool.stem}.jsonl"
