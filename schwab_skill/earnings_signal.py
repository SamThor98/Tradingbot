"""
Earnings signal helpers for PEAD-style enrichment.

Price bars remain Schwab-only when ``SCHWAB_ONLY_DATA=true``; earnings are
fetched from ``PEAD_DATA_PROVIDER`` (Finnhub by default when configured).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import pandas as pd

from config import (
    get_pead_cache_enabled,
    get_pead_cache_hours,
    get_pead_data_provider,
    get_pead_warm_history_years,
    get_schwab_only_data,
)

LOG = logging.getLogger(__name__)
SKILL_DIR = Path(__file__).resolve().parent
EARNINGS_CACHE_FILE = ".earnings_cache.json"
EARNINGS_CACHE_LOCK_FILE = ".earnings_cache.json.lock"
EARNINGS_WARM_PROGRESS_FILE = ".earnings_cache_warm_progress.json"

# Process-local memo: avoid reloading/rewriting the JSON cache on every
# day×ticker PEAD check inside a backtest worker.
_MEMO: dict[str, dict[str, Any]] = {}
_MEMO_MTIME: dict[str, float] = {}


def _normalize_ticker(ticker: str) -> str:
    return str(ticker or "").strip().upper()


def _resolve_pead_provider(skill_dir: Path | None = None) -> str:
    provider = get_pead_data_provider(skill_dir)
    if provider == "off":
        return "off"
    if provider == "yfinance" and get_schwab_only_data(skill_dir):
        LOG.debug("PEAD yfinance blocked under SCHWAB_ONLY_DATA; set PEAD_DATA_PROVIDER=finnhub")
        return "off"
    if provider == "alphavantage":
        from config import get_alpha_vantage_api_key

        if not get_alpha_vantage_api_key(skill_dir):
            return "off"
        return "alphavantage"
    if provider == "finnhub":
        from config import get_finnhub_api_key

        if not get_finnhub_api_key(skill_dir):
            LOG.debug("PEAD finnhub provider selected but FINNHUB_API_KEY is missing")
            return "off"
    if provider == "alphavantage":
        from config import get_alpha_vantage_api_key

        if not get_alpha_vantage_api_key(skill_dir):
            LOG.debug("PEAD alphavantage provider selected but ALPHA_VANTAGE_API_KEY is missing")
            return "off"
    return provider


def _cache_path(skill_dir: Path) -> Path:
    return skill_dir / EARNINGS_CACHE_FILE


def _cache_lock_path(skill_dir: Path) -> Path:
    return skill_dir / EARNINGS_CACHE_LOCK_FILE


def _memo_key(skill_dir: Path) -> str:
    return str(skill_dir.resolve())


def clear_earnings_cache_memo(skill_dir: Path | str | None = None) -> None:
    """Drop process-local memo (tests / forced re-warm)."""
    if skill_dir is None:
        _MEMO.clear()
        _MEMO_MTIME.clear()
        return
    key = _memo_key(Path(skill_dir))
    _MEMO.pop(key, None)
    _MEMO_MTIME.pop(key, None)


@contextmanager
def _earnings_cache_lock(skill_dir: Path, *, timeout_s: float = 120.0) -> Iterator[None]:
    """Cross-process exclusive lock for earnings cache read-modify-write."""
    lock_path = _cache_lock_path(skill_dir)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = lock_path.open("a+b")
    deadline = time.time() + max(1.0, float(timeout_s))
    locked = False
    try:
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    fh.seek(0)
                    if fh.read(1) == b"":
                        fh.write(b"\0")
                        fh.flush()
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except OSError:
                if time.time() >= deadline:
                    raise TimeoutError(f"PEAD earnings cache lock timeout: {lock_path}")
                time.sleep(0.05)
        yield
    finally:
        if locked:
            try:
                if os.name == "nt":
                    import msvcrt

                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        fh.close()


def _load_earnings_cache(skill_dir: Path) -> dict[str, Any]:
    path = _cache_path(skill_dir)
    try:
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        LOG.debug("PEAD earnings cache read failed: %s", exc)
        return {}


def _save_earnings_cache(skill_dir: Path, cache: dict[str, Any]) -> None:
    """Atomic replace so readers never observe a truncated JSON file.

    Retries ``os.replace`` for OneDrive/Windows ``Access is denied`` (WinError 5).
    """
    path = _cache_path(skill_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=".earnings_cache.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        last_exc: Exception | None = None
        for attempt in range(8):
            try:
                os.replace(tmp_path, path)
                last_exc = None
                break
            except OSError as exc:
                last_exc = exc
                # WinError 5 / sharing violation while OneDrive holds the target.
                time.sleep(0.05 * (2**attempt))
        if last_exc is not None:
            raise last_exc
        key = _memo_key(skill_dir)
        _MEMO[key] = cache
        try:
            _MEMO_MTIME[key] = float(path.stat().st_mtime)
        except OSError:
            _MEMO_MTIME[key] = time.time()
    except Exception as exc:
        LOG.warning("PEAD earnings cache write failed: %s", exc)
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _cache_entry_rows(
    skill_dir: Path,
    entry: dict[str, Any] | None,
    *,
    provider: str,
) -> list[dict[str, Any]] | None:
    if not isinstance(entry, dict):
        return None
    cached_provider = str(entry.get("provider") or "").lower()
    # Accept merged multi-source caches written as finnhub+* / alphavantage+*.
    if cached_provider != provider and not cached_provider.startswith(f"{provider}+"):
        return None
    stored_at = entry.get("stored_at")
    try:
        age_hours = (time.time() - float(stored_at)) / 3600.0
    except (TypeError, ValueError):
        return None
    if age_hours > get_pead_cache_hours(skill_dir):
        return None
    rows = entry.get("rows")
    if not isinstance(rows, list):
        return None
    out = [dict(r) for r in rows if isinstance(r, dict)]
    # Reject shallow calendar-only caches that break multi-era PEAD.
    from config import get_pead_min_history_rows

    if len(out) < int(get_pead_min_history_rows(skill_dir)):
        return None
    return out


def _memo_snapshot(skill_dir: Path) -> dict[str, Any]:
    """Return process memo, refreshing from disk when the file mtime changes."""
    key = _memo_key(skill_dir)
    path = _cache_path(skill_dir)
    try:
        mtime = float(path.stat().st_mtime) if path.exists() else -1.0
    except OSError:
        mtime = -1.0
    cached = _MEMO.get(key)
    if cached is not None and _MEMO_MTIME.get(key) == mtime:
        return cached
    data = _load_earnings_cache(skill_dir)
    _MEMO[key] = data
    _MEMO_MTIME[key] = mtime
    return data


def _cached_earnings_rows(
    skill_dir: Path,
    ticker: str,
    *,
    provider: str,
) -> list[dict[str, Any]] | None:
    if not get_pead_cache_enabled(skill_dir):
        return None
    tkr = _normalize_ticker(ticker)
    entry = _memo_snapshot(skill_dir).get(tkr)
    return _cache_entry_rows(skill_dir, entry if isinstance(entry, dict) else None, provider=provider)


def _remember_earnings_rows(
    skill_dir: Path,
    ticker: str,
    *,
    provider: str,
    rows: list[dict[str, Any]],
) -> None:
    if not get_pead_cache_enabled(skill_dir):
        return
    tkr = _normalize_ticker(ticker)
    entry = {
        "stored_at": time.time(),
        "provider": provider,
        "rows": rows,
    }
    with _earnings_cache_lock(skill_dir):
        cache = _load_earnings_cache(skill_dir)
        cache[tkr] = entry
        _save_earnings_cache(skill_dir, cache)


def _normalize_earnings_df(df: Any) -> pd.DataFrame:
    if df is None:
        return pd.DataFrame()
    if isinstance(df, pd.DataFrame):
        out = df.copy()
    else:
        return pd.DataFrame()
    if out.empty:
        return out
    if not isinstance(out.index, pd.DatetimeIndex):
        try:
            out.index = pd.to_datetime(out.index, errors="coerce")
        except Exception:
            return pd.DataFrame()
    out = out[~out.index.isna()]
    if out.empty:
        return out
    out.index = out.index.tz_localize(None) if out.index.tz is not None else out.index
    out = out.sort_index(ascending=False)
    return out


def _rows_to_earnings_df(rows: list[dict[str, Any]]) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for row in rows:
        date_raw = row.get("date")
        if not date_raw:
            continue
        try:
            dt = pd.Timestamp(date_raw).tz_localize(None)
        except (TypeError, ValueError):
            continue
        actual_eps = row.get("actual_eps")
        estimate_eps = row.get("estimate_eps")
        try:
            actual_f = float(actual_eps) if actual_eps is not None else None
        except (TypeError, ValueError):
            actual_f = None
        try:
            estimate_f = float(estimate_eps) if estimate_eps is not None else None
        except (TypeError, ValueError):
            estimate_f = None
        records.append(
            {
                "date": dt,
                "actual_eps": actual_f,
                "estimate_eps": estimate_f,
            }
        )
    if not records:
        return pd.DataFrame()
    frame = pd.DataFrame(records).set_index("date").sort_index(ascending=False)
    return frame


def _extract_eps_cols(df: pd.DataFrame) -> tuple[str | None, str | None]:
    cols_lower = {str(c).lower(): c for c in df.columns}
    rep = None
    est = None
    for key, col in cols_lower.items():
        if key in {"actual_eps", "reported eps"} or "reported eps" in key:
            rep = col
        if key in {"estimate_eps", "eps estimate"} or "eps estimate" in key:
            est = col
    if rep is None and "actual_eps" in df.columns:
        rep = "actual_eps"
    if est is None and "estimate_eps" in df.columns:
        est = "estimate_eps"
    return rep, est


# Smallest |estimate_eps| we'll trust as a denominator. EPS estimates of a few
# cents are common for small/mid caps and turn a $0.03 beat into a "+150%
# surprise," firing the large-PEAD boost incorrectly. Floor the denominator
# at $0.10 so the surprise stays interpretable on near-zero estimates.
_SURPRISE_DENOMINATOR_FLOOR = 0.10
# Hard winsorization clamp on the final fractional surprise. ±300% covers
# every legitimate beat/miss while killing pathological ratios from data-
# entry errors or 1-cent estimates.
_SURPRISE_CLAMP = 3.0


def _calc_surprise(actual_eps: float | None, estimate_eps: float | None) -> float | None:
    if actual_eps is None or estimate_eps is None:
        return None
    try:
        actual_f = float(actual_eps)
        est_f = float(estimate_eps)
    except (TypeError, ValueError):
        return None
    if abs(est_f) < 1e-9:
        return None
    denom = max(abs(est_f), _SURPRISE_DENOMINATOR_FLOOR)
    raw = (actual_f - est_f) / denom
    if raw != raw:  # NaN guard
        return None
    return max(-_SURPRISE_CLAMP, min(_SURPRISE_CLAMP, raw))


def _evaluate_earnings_window(
    earnings_df: pd.DataFrame,
    anchor: pd.Timestamp,
    lookback_days: int,
    *,
    earnings_provider: str,
) -> dict[str, Any] | None:
    if earnings_df.empty:
        return None
    window_start = anchor - pd.Timedelta(days=max(1, int(lookback_days)))
    recent = earnings_df[(earnings_df.index <= anchor) & (earnings_df.index >= window_start)]
    if recent.empty:
        return {
            "had_recent_earnings": False,
            "earnings_date": None,
            "actual_eps": None,
            "estimate_eps": None,
            "surprise_pct": None,
            "beat": None,
            "earnings_provider": earnings_provider,
        }

    row = recent.iloc[0]
    rep_col, est_col = _extract_eps_cols(recent)
    actual_eps = float(row[rep_col]) if rep_col and pd.notna(row[rep_col]) else None
    estimate_eps = float(row[est_col]) if est_col and pd.notna(row[est_col]) else None
    surprise = _calc_surprise(actual_eps, estimate_eps)
    beat = None if surprise is None else bool(surprise > 0)
    return {
        "had_recent_earnings": True,
        "earnings_date": str(recent.index[0].date()),
        "actual_eps": actual_eps,
        "estimate_eps": estimate_eps,
        "surprise_pct": surprise,
        "beat": beat,
        "earnings_provider": earnings_provider,
    }


def _df_to_earnings_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    rows: list[dict[str, Any]] = []
    for ts, row in df.iterrows():
        try:
            date_str = pd.Timestamp(ts).strftime("%Y-%m-%d")
        except Exception:
            continue
        actual = row.get("actual_eps") if hasattr(row, "get") else None
        estimate = row.get("estimate_eps") if hasattr(row, "get") else None
        try:
            actual_f = float(actual) if actual is not None and not pd.isna(actual) else None
        except (TypeError, ValueError):
            actual_f = None
        try:
            estimate_f = float(estimate) if estimate is not None and not pd.isna(estimate) else None
        except (TypeError, ValueError):
            estimate_f = None
        if actual_f is None and estimate_f is None:
            continue
        rows.append(
            {
                "date": date_str,
                "actual_eps": actual_f,
                "estimate_eps": estimate_f,
                "source": "yfinance",
            }
        )
    return rows


def _merge_row_lists(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_date: dict[str, dict[str, Any]] = {}
    for group in groups:
        for row in group:
            if not isinstance(row, dict):
                continue
            date_str = str(row.get("date") or "")[:10]
            if not date_str:
                continue
            # Prefer rows that already have both actual and estimate.
            prev = by_date.get(date_str)
            if prev is None:
                by_date[date_str] = dict(row)
                continue
            prev_complete = prev.get("actual_eps") is not None and prev.get("estimate_eps") is not None
            new_complete = row.get("actual_eps") is not None and row.get("estimate_eps") is not None
            if new_complete and not prev_complete:
                by_date[date_str] = dict(row)
    out = list(by_date.values())
    out.sort(key=lambda r: str(r.get("date") or ""), reverse=True)
    return out


def _enrich_thin_history_rows(
    rows: list[dict[str, Any]],
    ticker: str,
    skill_dir: Path,
) -> list[dict[str, Any]]:
    """Backfill thin primary-provider history with yfinance and/or Alpha Vantage."""
    from config import (
        get_alpha_vantage_api_key,
        get_pead_min_history_rows,
        get_pead_yf_history_fallback,
    )

    min_rows = int(get_pead_min_history_rows(skill_dir))
    merged = list(rows)
    if len(merged) >= min_rows:
        return merged
    if get_pead_yf_history_fallback(skill_dir):
        yf_rows = _df_to_earnings_rows(_fetch_earnings_df_yfinance(ticker))
        merged = _merge_row_lists(merged, yf_rows)
    if len(merged) < min_rows and get_alpha_vantage_api_key(skill_dir):
        try:
            from alphavantage_data import get_alphavantage_earnings_history

            av = get_alphavantage_earnings_history(ticker, skill_dir=skill_dir)
            av_rows = [dict(r) for r in (av.get("rows") or []) if isinstance(r, dict)]
            merged = _merge_row_lists(merged, av_rows)
        except Exception as exc:
            LOG.debug("Alpha Vantage PEAD backfill failed for %s: %s", ticker, exc)
    return merged


def _fetch_earnings_df_finnhub(ticker: str, skill_dir: Path) -> pd.DataFrame:
    provider = "finnhub"
    cached_rows = _cached_earnings_rows(skill_dir, ticker, provider=provider)
    if cached_rows is not None:
        return _rows_to_earnings_df(cached_rows)

    warm = warm_earnings_for_ticker(ticker, skill_dir=skill_dir, force=False)
    rows = warm.get("rows") if isinstance(warm, dict) else []
    rows = [dict(r) for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    rows = _enrich_thin_history_rows(rows, ticker, skill_dir)
    if rows:
        _remember_earnings_rows(skill_dir, ticker, provider="finnhub+enriched", rows=rows)
    return _rows_to_earnings_df(rows)


def _fetch_earnings_df_alphavantage(ticker: str, skill_dir: Path) -> pd.DataFrame:
    provider = "alphavantage"
    cached_rows = _cached_earnings_rows(skill_dir, ticker, provider=provider)
    if cached_rows is not None:
        return _rows_to_earnings_df(cached_rows)
    from alphavantage_data import get_alphavantage_earnings_history

    payload = get_alphavantage_earnings_history(ticker, skill_dir=skill_dir)
    rows = [dict(r) for r in (payload.get("rows") or []) if isinstance(r, dict)]
    rows = _enrich_thin_history_rows(rows, ticker, skill_dir)
    if rows:
        _remember_earnings_rows(skill_dir, ticker, provider="alphavantage+enriched", rows=rows)
    return _rows_to_earnings_df(rows)


def _refresh_finnhub_earnings_rows(ticker: str, skill_dir: Path) -> tuple[list[dict[str, Any]], list[str]]:
    from finnhub_data import get_finnhub_earnings_history

    payload = get_finnhub_earnings_history(
        ticker,
        skill_dir=skill_dir,
        history_years=get_pead_warm_history_years(skill_dir),
    )
    rows = payload.get("rows") if isinstance(payload, dict) else []
    rows = [dict(r) for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    errors = list(payload.get("errors") or []) if isinstance(payload, dict) else []
    if rows:
        _remember_earnings_rows(skill_dir, ticker, provider="finnhub", rows=rows)
    return rows, errors


def earnings_cache_summary(
    tickers: list[str],
    skill_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Return fresh/missing counts for a ticker list against the PEAD cache."""
    sd = Path(skill_dir) if skill_dir is not None else SKILL_DIR
    provider = _resolve_pead_provider(sd)
    fresh = 0
    missing = 0
    for raw in tickers:
        tkr = _normalize_ticker(raw)
        if not tkr:
            continue
        if provider != "off" and _cached_earnings_rows(sd, tkr, provider=provider) is not None:
            fresh += 1
        else:
            missing += 1
    return {
        "provider": provider,
        "total": len([t for t in tickers if _normalize_ticker(t)]),
        "fresh": fresh,
        "missing": missing,
    }


def warm_earnings_for_ticker(
    ticker: str,
    skill_dir: Path | str | None = None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Fetch and cache Finnhub earnings rows for one ticker."""
    sd = Path(skill_dir) if skill_dir is not None else SKILL_DIR
    tkr = _normalize_ticker(ticker)
    provider = _resolve_pead_provider(sd)
    if provider == "off":
        return {"ok": False, "ticker": tkr, "skipped": True, "reason": "provider_off", "rows": []}
    if provider == "alphavantage":
        from alphavantage_data import get_alphavantage_earnings_history

        if not force:
            cached = _cached_earnings_rows(sd, tkr, provider=provider)
            if cached is not None:
                return {
                    "ok": True,
                    "ticker": tkr,
                    "skipped": True,
                    "reason": "cache_fresh",
                    "row_count": len(cached),
                    "rows": cached,
                }
        payload = get_alphavantage_earnings_history(tkr, skill_dir=sd)
        rows = [dict(r) for r in (payload.get("rows") or []) if isinstance(r, dict)]
        rows = _enrich_thin_history_rows(rows, tkr, sd)
        if rows:
            _remember_earnings_rows(sd, tkr, provider="alphavantage+enriched", rows=rows)
        return {
            "ok": bool(rows),
            "ticker": tkr,
            "skipped": False,
            "row_count": len(rows),
            "rows": rows,
            "errors": list(payload.get("errors") or []),
        }
    if provider != "finnhub":
        return {
            "ok": False,
            "ticker": tkr,
            "skipped": True,
            "reason": "warm_requires_finnhub_or_alphavantage",
            "rows": [],
        }
    if not force:
        cached = _cached_earnings_rows(sd, tkr, provider=provider)
        if cached is not None:
            return {
                "ok": True,
                "ticker": tkr,
                "skipped": True,
                "reason": "cache_fresh",
                "row_count": len(cached),
                "rows": cached,
            }
    rows, errors = _refresh_finnhub_earnings_rows(tkr, sd)
    rows = _enrich_thin_history_rows(rows, tkr, sd)
    if rows:
        _remember_earnings_rows(sd, tkr, provider="finnhub+enriched", rows=rows)
    return {
        "ok": bool(rows),
        "ticker": tkr,
        "skipped": False,
        "row_count": len(rows),
        "rows": rows,
        "errors": errors,
    }


def _warm_progress_path(skill_dir: Path) -> Path:
    return skill_dir / EARNINGS_WARM_PROGRESS_FILE


def _load_warm_progress(skill_dir: Path) -> dict[str, Any]:
    path = _warm_progress_path(skill_dir)
    try:
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        LOG.debug("PEAD warm progress read failed: %s", exc)
        return {}


def _save_warm_progress(skill_dir: Path, payload: dict[str, Any]) -> None:
    path = _warm_progress_path(skill_dir)
    try:
        with path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
    except Exception as exc:
        LOG.debug("PEAD warm progress write failed: %s", exc)


def warm_earnings_for_tickers(
    tickers: list[str],
    skill_dir: Path | str | None = None,
    *,
    force: bool = False,
    resume: bool = True,
) -> dict[str, Any]:
    """Batch warm Finnhub earnings cache for many tickers."""
    sd = Path(skill_dir) if skill_dir is not None else SKILL_DIR
    provider = _resolve_pead_provider(sd)
    normalized = [_normalize_ticker(t) for t in tickers if _normalize_ticker(t)]
    seen: set[str] = set()
    ordered: list[str] = []
    for tkr in normalized:
        if tkr in seen:
            continue
        seen.add(tkr)
        ordered.append(tkr)

    progress = _load_warm_progress(sd) if resume and not force else {}
    completed = {str(t).upper() for t in (progress.get("completed") or []) if str(t).strip()}
    failed: dict[str, list[str]] = {}
    for item in progress.get("failed") or []:
        if isinstance(item, dict):
            sym = _normalize_ticker(str(item.get("ticker") or ""))
            if sym:
                failed[sym] = list(item.get("errors") or [])

    if force:
        clear_earnings_cache_memo(sd)

    fetched = 0
    skipped = 0
    progress_stale = 0
    errors_total = 0
    for idx, tkr in enumerate(ordered, start=1):
        # Progress file alone is not proof of cache contents (lost writes / races).
        if resume and not force and tkr in completed:
            cached = (
                _cached_earnings_rows(sd, tkr, provider=provider)
                if provider not in {"off", ""}
                else None
            )
            if cached is not None:
                skipped += 1
                continue
            progress_stale += 1
            completed.discard(tkr)
        result = warm_earnings_for_ticker(tkr, sd, force=force)
        if result.get("skipped") and result.get("reason") == "cache_fresh":
            skipped += 1
            completed.add(tkr)
            continue
        if result.get("ok"):
            fetched += 1
            completed.add(tkr)
            failed.pop(tkr, None)
        else:
            errors_total += 1
            failed[tkr] = list(result.get("errors") or [str(result.get("reason") or "fetch_failed")])
        if idx % 25 == 0 or idx == len(ordered):
            _save_warm_progress(
                sd,
                {
                    "provider": provider,
                    "completed": sorted(completed),
                    "failed": [{"ticker": k, "errors": v} for k, v in sorted(failed.items())],
                    "processed": idx,
                    "total": len(ordered),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            LOG.info(
                "PEAD warm progress %s/%s fetched=%s skipped=%s errors=%s stale_progress=%s",
                idx,
                len(ordered),
                fetched,
                skipped,
                errors_total,
                progress_stale,
            )

    clear_earnings_cache_memo(sd)
    cache_summary = earnings_cache_summary(ordered, sd)
    cache_fresh = int(cache_summary.get("fresh") or 0)
    cache_missing = int(cache_summary.get("missing") or 0)
    # Only count tickers that are both in progress "completed" and actually fresh on disk.
    verified_completed = [
        t
        for t in sorted(completed)
        if provider not in {"off", ""} and _cached_earnings_rows(sd, t, provider=provider) is not None
    ]
    summary = {
        "ok": provider != "off" and errors_total == 0 and cache_missing == 0,
        "provider": provider,
        "total": len(ordered),
        "fetched": fetched,
        "skipped": skipped,
        "progress_stale": progress_stale,
        "errors": errors_total,
        "failed_tickers": sorted(failed.keys()),
        "cache_fresh": cache_fresh,
        "cache_missing": cache_missing,
        "verified_completed": len(verified_completed),
    }
    _save_warm_progress(
        sd,
        {
            **summary,
            "completed": verified_completed,
            "failed": [{"ticker": k, "errors": v} for k, v in sorted(failed.items())],
            "finished_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return summary


def _fetch_earnings_df_yfinance(ticker: str) -> pd.DataFrame:
    try:
        import yfinance as yf

        from _io_utils import yfinance_call

        tkr = _normalize_ticker(ticker)
        raw = None
        with yfinance_call():
            t = yf.Ticker(tkr)
            # Default .earnings_dates is short (~12–25 rows). limit=100 reaches
            # early-2000s for liquid names — required for five-era PEAD.
            try:
                raw = t.get_earnings_dates(limit=100)
            except Exception:
                raw = getattr(t, "earnings_dates", None)
        normalized = _normalize_earnings_df(raw)
        if normalized.empty:
            return normalized
        # yfinance columns are typically "Reported EPS" / "EPS Estimate".
        rep_col, est_col = _extract_eps_cols(normalized)
        rename: dict[str, str] = {}
        if rep_col:
            rename[rep_col] = "actual_eps"
        if est_col:
            rename[est_col] = "estimate_eps"
        # Also accept already-normalized names / Surprise path.
        cols_lower = {str(c).lower(): c for c in normalized.columns}
        if "actual_eps" not in rename.values():
            for key in ("reported eps", "reportedeps"):
                if key in cols_lower:
                    rename[cols_lower[key]] = "actual_eps"
                    break
        if "estimate_eps" not in rename.values():
            for key in ("eps estimate", "epsestimate"):
                if key in cols_lower:
                    rename[cols_lower[key]] = "estimate_eps"
                    break
        if rename:
            normalized = normalized.rename(columns=rename)
        keep = [c for c in ("actual_eps", "estimate_eps") if c in normalized.columns]
        return normalized[keep] if keep else normalized
    except Exception as exc:
        LOG.debug("yfinance earnings fetch failed for %s: %s", ticker, exc)
        return pd.DataFrame()


def _fetch_earnings_df(ticker: str, skill_dir: Path | None = None) -> tuple[pd.DataFrame, str]:
    sd = skill_dir or SKILL_DIR
    provider = _resolve_pead_provider(sd)
    if provider == "off":
        return pd.DataFrame(), "off"
    if provider == "finnhub":
        return _fetch_earnings_df_finnhub(ticker, sd), provider
    if provider == "alphavantage":
        return _fetch_earnings_df_alphavantage(ticker, sd), provider
    return _fetch_earnings_df_yfinance(ticker), provider


def check_recent_earnings(
    ticker: str,
    lookback_days: int = 10,
    *,
    skill_dir: Path | str | None = None,
) -> dict[str, Any] | None:
    """
    Check if ticker had earnings within lookback window from now.
    Returns EPS surprise details when available.
    """
    sd = Path(skill_dir) if skill_dir is not None else SKILL_DIR
    try:
        earnings_df, provider = _fetch_earnings_df(ticker, sd)
        if provider == "off":
            return None
        now = pd.Timestamp(datetime.now(timezone.utc).replace(tzinfo=None))
        return _evaluate_earnings_window(
            earnings_df,
            now,
            lookback_days,
            earnings_provider=provider,
        )
    except Exception as exc:
        LOG.debug("Recent earnings check failed for %s: %s", ticker, exc)
        return None


def check_earnings_at_date(
    ticker: str,
    date: Any,
    df: pd.DataFrame | None = None,
    lookback_days: int = 10,
    *,
    skill_dir: Path | str | None = None,
) -> dict[str, Any] | None:
    """
    Historical earnings check relative to a supplied entry date.
    """
    sd = Path(skill_dir) if skill_dir is not None else SKILL_DIR
    try:
        earnings_df, provider = _fetch_earnings_df(ticker, sd)
        if provider == "off":
            return None
        anchor = pd.Timestamp(date).tz_localize(None)
        return _evaluate_earnings_window(
            earnings_df,
            anchor,
            lookback_days,
            earnings_provider=provider,
        )
    except Exception as exc:
        LOG.debug("Historical earnings check failed for %s: %s", ticker, exc)
        return None


def maybe_warm_earnings_for_scan(
    tickers: list[str],
    skill_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Warm missing Finnhub earnings rows before a live scan (cache-first)."""
    from config import (
        get_pead_enabled,
        get_pead_prescan_warm_enabled,
        get_pead_prescan_warm_max_missing,
    )

    sd = Path(skill_dir) if skill_dir is not None else SKILL_DIR
    if not get_pead_enabled(sd) or not get_pead_prescan_warm_enabled(sd):
        return {"skipped": True, "reason": "prescan_warm_disabled"}
    provider = _resolve_pead_provider(sd)
    if provider == "off":
        return {"skipped": True, "reason": "provider_off", "provider": provider}

    before = earnings_cache_summary(tickers, sd)
    missing = int(before.get("missing") or 0)
    if missing == 0:
        return {"skipped": True, "reason": "cache_warm", "provider": provider, **before}

    max_missing = int(get_pead_prescan_warm_max_missing(sd))
    if max_missing > 0 and missing > max_missing:
        return {
            "skipped": True,
            "reason": "too_many_missing_run_warm_script",
            "provider": provider,
            "missing": missing,
            "max_missing": max_missing,
            **before,
        }

    warm = warm_earnings_for_tickers(tickers, sd, force=False, resume=True)
    after = earnings_cache_summary(tickers, sd)
    return {
        "skipped": False,
        "provider": provider,
        "before": before,
        "after": after,
        **warm,
    }
