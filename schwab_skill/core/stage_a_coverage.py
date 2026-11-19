"""Stage A coverage: waves, checkpoint, and live/research merge.

The executable pass owes every watchlist symbol a decision. A wall-clock
timeout ends one wave, not the universe. Research history is a later pass and
cannot cancel Stage 2 results.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO
from zoneinfo import ZoneInfo

LOG = logging.getLogger(__name__)

CHECKPOINT_NAME = ".stage_a_checkpoint.json"
CHECKPOINT_FRAMES_SUFFIX = ".frames"
CHECKPOINT_VERSION = 1
# Live quotes move. Reuse a same-session breakout decision only briefly.
QUOTE_REFRESH_SEC = 1200
_ET = ZoneInfo("America/New_York")

# Executable rejections that already applied a live gate. A later horizon hit
# must not turn those names into research admits.
_HORIZON_BLOCKED_BY_EXECUTABLE = frozenset(
    {
        "entry_timing_blocked",
        "pts_52w_cap_blocked",
        "early_stop_gate_blocked",
        "breakout_not_confirmed",
        "vcp_fail",
        "no_sector_etf",
        "sector_not_winning",
        "self_study_low_cached_conviction",
        "stage_a_timeout",
        "exceptions",
        "df_empty",
        "too_few_candles",
        "bars_stale",
    }
)
_RETRY_REASONS = frozenset({"stage_a_timeout", "exceptions"})
_QUOTE_SENSITIVE_REASONS = frozenset({"breakout_not_confirmed"})


def session_date_et(now: datetime | None = None) -> str:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(_ET).date().isoformat()


def universe_key(
    *,
    tickers: list[str],
    strategy_ids: list[str] | None,
    executable_days: int,
    horizon_days: int,
    want_stage2: bool,
    want_pead: bool,
    want_horizon: bool,
) -> str:
    payload = {
        "tickers": sorted({str(t).upper().strip() for t in tickers if str(t).strip()}),
        "strategies": sorted({str(s).strip().lower() for s in (strategy_ids or []) if str(s).strip()}),
        "executable_days": int(executable_days),
        "horizon_days": int(horizon_days),
        "want_stage2": bool(want_stage2),
        "want_pead": bool(want_pead),
        "want_horizon": bool(want_horizon),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def checkpoint_path(skill_dir: Path) -> Path:
    return Path(skill_dir) / CHECKPOINT_NAME


def checkpoint_frames_path(path: Path) -> Path:
    """Binary sibling of the JSON checkpoint. One numeric matrix per kept frame."""
    return Path(str(path) + CHECKPOINT_FRAMES_SUFFIX)


def checkpoint_writes_enabled(skill_dir: Path | None = None) -> bool:
    """Unit tests drive the scanner with the real skill dir. Do not resume those files."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    from config import get_scan_stage_a_checkpoint_enabled

    return bool(get_scan_stage_a_checkpoint_enabled(skill_dir))


def _int(diagnostics: Mapping[str, Any], key: str) -> int:
    try:
        return int(diagnostics.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def stage_a_funnel_counts(diagnostics: Mapping[str, Any]) -> dict[str, int]:
    """Count Stage 2 passes from names that actually finished Stage A.

    Timeouts and Stage A task exceptions are not uptrend passes. Stage B
    exceptions stay in the later quality step.
    """
    watchlist = _int(diagnostics, "watchlist_size")
    unresolved = _int(diagnostics, "stage_a_timeouts") + _int(diagnostics, "stage_a_task_exceptions")
    evaluated = max(0, watchlist - unresolved)
    pre_stage2 = (
        _int(diagnostics, "stage2_fail")
        + _int(diagnostics, "df_empty")
        + _int(diagnostics, "too_few_candles")
        + _int(diagnostics, "bars_stale")
    )
    return {
        "watchlist": watchlist,
        "unresolved": unresolved,
        "evaluated": evaluated,
        "stage2_pass": max(0, evaluated - pre_stage2),
    }


def coverage_note(
    *,
    watchlist: int,
    executable_requested: bool,
    executable_unresolved: int,
    horizon_requested: bool,
    horizon_unresolved: int,
) -> str:
    if executable_requested and executable_unresolved > 0:
        return (
            f"Scan partial: {executable_unresolved} of {watchlist} names have no Stage A decision. "
            "The live book is incomplete until the next wave finishes them."
        )
    if horizon_requested and horizon_unresolved > 0:
        return (
            f"Live book covered all {watchlist} names. "
            f"Research sleeves are partial ({horizon_unresolved} names still queued)."
        )
    return ""


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items() if k != "df"}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _to_jsonable(item())
        except Exception:
            return str(value)
    iso = getattr(value, "isoformat", None)
    if callable(iso):
        try:
            return iso()
        except Exception:
            return str(value)
    return str(value)


def _freeze_frame_records(df: Any) -> tuple[list[dict[str, Any]] | None, str]:
    """Legacy per-bar dicts. Used only when a frame is not a numeric date index.

    A broad universe of these dicts is what pinned the dashboard heap. Numeric
    Stage A frames go through ``_encode_frame`` instead.
    """
    try:
        import pandas as pd
    except Exception:
        return None, "date"
    if not isinstance(df, pd.DataFrame) or df.empty:
        return None, "date"
    frame = df.reset_index()
    index_name = str(df.index.name or "date")
    if index_name not in frame.columns:
        frame = frame.rename(columns={frame.columns[0]: index_name})
    records = frame.to_dict(orient="records")
    return [_to_jsonable(row) for row in records], index_name


def _thaw_frame_records(records: Any, index_name: str) -> Any:
    if not isinstance(records, list) or not records:
        return None
    import pandas as pd

    frame = pd.DataFrame(records)
    if index_name in frame.columns:
        frame[index_name] = pd.to_datetime(frame[index_name], errors="coerce")
        frame = frame.set_index(index_name)
    frame.index.name = index_name
    return frame


def _encode_frame(df: Any) -> tuple[bytes, dict[str, Any]] | None:
    """Pack a numeric date-indexed frame into one little-endian buffer.

    Layout is ``int64`` index nanoseconds followed by column-major ``float64``
    values. One buffer per frame, written straight to the frames file, so a
    1,500-name wave does not build a Python object per bar.
    """
    try:
        import numpy as np
        import pandas as pd
    except Exception:
        return None
    if not isinstance(df, pd.DataFrame) or df.empty:
        return None
    if df.index is None or not pd.api.types.is_datetime64_any_dtype(df.index):
        return None
    if getattr(df.index, "tz", None) is not None:
        return None
    if any(not pd.api.types.is_numeric_dtype(df[col]) for col in df.columns):
        return None
    index = pd.DatetimeIndex(df.index)
    if bool(index.isna().any()):
        return None
    try:
        index_ns = np.ascontiguousarray(index.astype("datetime64[ns]").asi8, dtype="<i8")
    except (TypeError, ValueError):
        return None
    columns = [str(col) for col in df.columns]
    arrays: list[Any] = []
    for col in df.columns:
        try:
            arrays.append(np.ascontiguousarray(df[col].to_numpy(dtype="<f8", copy=True), dtype="<f8"))
        except (TypeError, ValueError):
            return None
    packed = np.concatenate(arrays) if arrays else np.empty(0, dtype="<f8")
    raw = index_ns.tobytes() + packed.tobytes()
    meta = {
        "rows": int(len(df)),
        "columns": columns,
        "index_name": str(df.index.name or "date"),
    }
    return raw, meta


def _decode_frame(raw: bytes, meta: Mapping[str, Any]) -> Any:
    import numpy as np
    import pandas as pd

    rows = int(meta.get("rows") or 0)
    columns = [str(col) for col in (meta.get("columns") or [])]
    index_name = str(meta.get("index_name") or "date")
    if rows <= 0:
        return None
    index_bytes = rows * 8
    value_bytes = rows * len(columns) * 8
    if len(raw) != index_bytes + value_bytes:
        return None
    index_ns = np.frombuffer(raw[:index_bytes], dtype="<i8").copy()
    values = np.frombuffer(raw[index_bytes:], dtype="<f8")
    if columns:
        values = values.reshape(len(columns), rows).copy()
    index = pd.to_datetime(index_ns, unit="ns")
    frame = pd.DataFrame({col: values[i] for i, col in enumerate(columns)}, index=index)
    frame.index.name = index_name
    return frame


def _attach_frame_payload(frozen_candidate: dict[str, Any], df: Any) -> tuple[bytes | None, dict[str, Any] | None]:
    """Remember how to restore ``df``. Returns raw bytes when the numeric path fits."""
    encoded = _encode_frame(df)
    if encoded is not None:
        return encoded
    records, index_name = _freeze_frame_records(df)
    if records:
        frozen_candidate["ohlcv_records"] = records
        frozen_candidate["ohlcv_index_name"] = index_name
    return None, None


def freeze_result(result: dict[str, Any]) -> dict[str, Any]:
    """JSON-safe copy of one Stage A result, with a numeric frame inlined as base64.

    Checkpoint saves do not use this. They stream frames to the sibling file so
    a full universe never holds a base64 string per ticker at once.
    """
    import base64

    frozen = _to_jsonable(result)
    if not isinstance(frozen, dict):
        return {"ok": False, "reason": "exceptions"}
    candidate = result.get("candidate")
    frozen_candidate = frozen.get("candidate")
    if isinstance(candidate, dict) and isinstance(frozen_candidate, dict):
        raw, meta = _attach_frame_payload(frozen_candidate, candidate.get("df"))
        if raw is not None and meta is not None:
            frozen_candidate.pop("ohlcv_records", None)
            frozen_candidate.pop("ohlcv_index_name", None)
            frozen_candidate["ohlcv_b64"] = base64.b64encode(raw).decode("ascii")
            frozen_candidate["ohlcv_meta"] = meta
    return frozen


def thaw_result(result: dict[str, Any], *, frame_blob: bytes = b"") -> dict[str, Any]:
    thawed = dict(result)
    candidate = thawed.get("candidate")
    if not isinstance(candidate, dict):
        return thawed
    cand = dict(candidate)
    span = cand.pop("ohlcv_span", None)
    inline = cand.pop("ohlcv_b64", None)
    meta = cand.pop("ohlcv_meta", None)
    records = cand.pop("ohlcv_records", None)
    index_name = str(cand.pop("ohlcv_index_name", "date") or "date")
    frame = None
    if isinstance(span, dict) and frame_blob:
        try:
            offset = int(span.get("offset") or 0)
            length = int(span.get("length") or 0)
        except (TypeError, ValueError):
            offset, length = 0, 0
        if length > 0 and offset >= 0 and offset + length <= len(frame_blob):
            frame = _decode_frame(frame_blob[offset : offset + length], span)
    elif isinstance(inline, str) and inline:
        import base64

        try:
            raw = base64.b64decode(inline)
        except Exception:
            raw = b""
        if raw and isinstance(meta, dict):
            frame = _decode_frame(raw, meta)
    elif records is not None:
        frame = _thaw_frame_records(records, index_name)
    if frame is not None:
        cand["df"] = frame
    thawed["candidate"] = cand
    return thawed


def _write_result_bucket(
    results: Mapping[str, dict[str, Any]],
    handle: BinaryIO,
    hasher: Any,
    cursor: int,
) -> tuple[dict[str, dict[str, Any]], int]:
    kept: dict[str, dict[str, Any]] = {}
    for ticker, result in results.items():
        if not isinstance(result, dict):
            continue
        reason = str(result.get("reason") or "")
        if reason in _RETRY_REASONS:
            continue
        frozen = _to_jsonable(result)
        if not isinstance(frozen, dict):
            continue
        candidate = result.get("candidate") if isinstance(result.get("candidate"), dict) else None
        frozen_candidate = frozen.get("candidate") if isinstance(frozen.get("candidate"), dict) else None
        if candidate is not None and frozen_candidate is not None:
            raw, meta = _attach_frame_payload(frozen_candidate, candidate.get("df"))
            if raw is not None and meta is not None:
                handle.write(raw)
                hasher.update(raw)
                frozen_candidate["ohlcv_span"] = {"offset": cursor, "length": len(raw), **meta}
                cursor += len(raw)
        kept[str(ticker).upper()] = frozen
    return kept, cursor


def _age_seconds(saved_at: str, now: datetime) -> float | None:
    try:
        stamp = datetime.fromisoformat(saved_at)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return (now - stamp).total_seconds()


def result_is_reusable(result: dict[str, Any], *, saved_at: str, now: datetime) -> bool:
    reason = str(result.get("reason") or "")
    if reason in _RETRY_REASONS:
        return False
    quote_sensitive = bool(result.get("ok")) or reason in _QUOTE_SENSITIVE_REASONS
    if not quote_sensitive:
        return True
    age = _age_seconds(saved_at, now)
    if age is None:
        return False
    return 0 <= age <= QUOTE_REFRESH_SEC


def save_checkpoint(
    path: Path,
    *,
    universe_key_value: str,
    session_date: str,
    executable: Mapping[str, dict[str, Any]],
    horizon: Mapping[str, dict[str, Any]],
    horizon_started: bool,
    now: datetime | None = None,
) -> None:
    """Persist Stage A decisions. Numeric bars go to a sibling ``.frames`` file.

    The JSON file keeps the decisions and byte spans. Writing one frame at a
    time avoids the per-bar dict graph that previously stayed resident after
    the scan returned.
    """
    moment = now or datetime.now(timezone.utc)
    path.parent.mkdir(parents=True, exist_ok=True)
    frames_path = checkpoint_frames_path(path)
    frames_tmp = Path(str(frames_path) + ".tmp")
    json_tmp = path.with_suffix(path.suffix + ".tmp")
    hasher = hashlib.sha256()
    try:
        with frames_tmp.open("wb") as handle:
            executable_kept, cursor = _write_result_bucket(executable, handle, hasher, 0)
            horizon_kept, cursor = _write_result_bucket(horizon, handle, hasher, cursor)
        wrote_frames = cursor > 0
        payload: dict[str, Any] = {
            "version": CHECKPOINT_VERSION,
            "universe_key": universe_key_value,
            "session_date": session_date,
            "saved_at": moment.astimezone(timezone.utc).isoformat(),
            "horizon_started": bool(horizon_started),
            "executable": executable_kept,
            "horizon": horizon_kept,
        }
        if wrote_frames:
            payload["frame_sha256"] = hasher.hexdigest()
        json_tmp.write_text(json.dumps(payload), encoding="utf-8")
        del payload, executable_kept, horizon_kept
        if wrote_frames:
            os.replace(frames_tmp, frames_path)
        else:
            frames_tmp.unlink(missing_ok=True)
            if frames_path.exists():
                frames_path.unlink()
        os.replace(json_tmp, path)
    except Exception:
        for temp in (frames_tmp, json_tmp):
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    finally:
        try:
            from core.process_memory import release_process_memory

            release_process_memory()
        except Exception:
            LOG.debug("Checkpoint memory release skipped", exc_info=True)


def load_checkpoint(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        LOG.warning("Stage A checkpoint unreadable: %s", exc)
        return None
    if not isinstance(payload, dict) or int(payload.get("version") or 0) != CHECKPOINT_VERSION:
        return None
    payload["_frames_path"] = str(checkpoint_frames_path(path))
    return payload


def _read_frame_blob(payload: dict[str, Any]) -> bytes:
    """Load the frames sibling when its hash matches. Empty means no numeric bars."""
    frames_path = str(payload.pop("_frames_path", "") or "")
    digest = str(payload.get("frame_sha256") or "")
    if not digest:
        return b""
    if not frames_path:
        LOG.warning("Stage A checkpoint names frames but has no frames path")
        return b""
    path = Path(frames_path)
    try:
        blob = path.read_bytes()
    except OSError as exc:
        LOG.warning("Stage A frame checkpoint unreadable: %s", exc)
        return b""
    if hashlib.sha256(blob).hexdigest() != digest:
        LOG.warning("Stage A frame checkpoint hash mismatch: %s", path)
        return b""
    return blob


def reusable_from_checkpoint(
    payload: dict[str, Any] | None,
    *,
    universe_key_value: str,
    session_date: str,
    now: datetime | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], bool]:
    if not payload:
        return {}, {}, False
    if str(payload.get("universe_key") or "") != universe_key_value:
        return {}, {}, False
    if str(payload.get("session_date") or "") != session_date:
        return {}, {}, False
    frame_blob = _read_frame_blob(payload)
    if payload.get("frame_sha256") and not frame_blob:
        # A mismatched bar file would feed Stage B a different history than the
        # one that produced the saved decision. Drop the resume and recompute.
        return {}, {}, False
    moment = now or datetime.now(timezone.utc)
    saved_at = str(payload.get("saved_at") or "")

    def _take(bucket: Any) -> dict[str, dict[str, Any]]:
        if not isinstance(bucket, dict):
            return {}
        out: dict[str, dict[str, Any]] = {}
        for ticker, result in bucket.items():
            if not isinstance(result, dict):
                continue
            if not result_is_reusable(result, saved_at=saved_at, now=moment):
                continue
            out[str(ticker).upper()] = thaw_result(result, frame_blob=frame_blob)
        return out

    return (
        _take(payload.get("executable")),
        _take(payload.get("horizon")),
        bool(payload.get("horizon_started")),
    )


def clear_checkpoint(path: Path) -> None:
    for target in (path, checkpoint_frames_path(path)):
        try:
            target.unlink(missing_ok=True)
        except OSError as exc:
            LOG.warning("Stage A checkpoint clear failed: %s", exc)


def merge_horizon_candidates(
    executable_results: Mapping[str, dict[str, Any]],
    horizon_results: Mapping[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Executable admissions stay authoritative. Horizon-only rows are added beside them."""
    candidates: list[dict[str, Any]] = []
    by_ticker: dict[str, dict[str, Any]] = {}
    executable_results = {str(k).upper(): v for k, v in executable_results.items() if isinstance(v, dict)}
    horizon_results = {str(k).upper(): v for k, v in horizon_results.items() if isinstance(v, dict)}
    for ticker, result in executable_results.items():
        if not isinstance(result, dict) or not result.get("ok"):
            continue
        candidate = result.get("candidate")
        if not isinstance(candidate, dict):
            continue
        key = str(ticker).upper()
        by_ticker[key] = candidate
        candidates.append(candidate)

    for ticker, result in horizon_results.items():
        if not isinstance(result, dict) or not result.get("ok"):
            continue
        horizon_cand = result.get("candidate")
        if not isinstance(horizon_cand, dict):
            continue
        key = str(ticker).upper()
        family = str(horizon_cand.get("entry_family") or "").strip().lower()
        hits = list(horizon_cand.get("horizon_hits") or [])
        plugin_hits = list(horizon_cand.get("horizon_plugin_hits") or [])
        existing = by_ticker.get(key)
        if existing is not None:
            if hits and not existing.get("horizon_hits"):
                existing["horizon_hits"] = hits
            if plugin_hits and not existing.get("horizon_plugin_hits"):
                existing["horizon_plugin_hits"] = plugin_hits
            _prefer_longer_frame(existing, horizon_cand)
            continue
        executable = executable_results.get(key) or executable_results.get(ticker)
        if isinstance(executable, dict) and not executable.get("ok"):
            reason = str(executable.get("reason") or "")
            if reason in _HORIZON_BLOCKED_BY_EXECUTABLE:
                continue
        if family == "horizon":
            by_ticker[key] = horizon_cand
            candidates.append(horizon_cand)
    return candidates


def _prefer_longer_frame(existing: dict[str, Any], horizon_cand: dict[str, Any]) -> None:
    horizon_df = horizon_cand.get("df")
    current = existing.get("df")
    try:
        horizon_len = len(horizon_df) if horizon_df is not None else 0
        current_len = len(current) if current is not None else 0
    except TypeError:
        return
    if horizon_len > current_len:
        existing["df"] = horizon_df


def _shutdown(executor: Any, *, wait: bool, cancel: bool) -> None:
    shutdown = getattr(executor, "shutdown", None)
    if not callable(shutdown):
        return
    try:
        shutdown(wait=wait, cancel_futures=cancel)
    except TypeError:
        try:
            shutdown(wait=wait)
        except Exception:
            LOG.debug("Stage A executor shutdown failed", exc_info=True)
    except Exception:
        LOG.debug("Stage A executor shutdown failed", exc_info=True)


def _cancel(fut: Any) -> None:
    cancel = getattr(fut, "cancel", None)
    if not callable(cancel):
        return
    try:
        cancel()
    except Exception:
        return


def _harvest(
    future_map: dict[Any, str],
    results: dict[str, dict[str, Any]],
    on_result: Callable[[str, dict[str, Any]], None] | None,
) -> None:
    for fut, ticker in future_map.items():
        if ticker in results or not bool(getattr(fut, "done", lambda: False)()):
            continue
        try:
            out = fut.result()
        except Exception as exc:
            out = {"ok": False, "reason": "exceptions", "error": f"{ticker}: {exc}"}
        if not isinstance(out, dict):
            out = {"ok": False, "reason": "exceptions", "error": f"{ticker}: bad stage A result"}
        results[ticker] = out
        if on_result is not None:
            on_result(ticker, out)


def run_stage_a_waves(
    tickers: list[str],
    evaluate: Callable[..., dict[str, Any]],
    *,
    max_workers: int,
    wall_budget_sec: float,
    task_timeout_sec: float,
    max_stalls: int,
    executor_cls: Any,
    as_completed: Callable[..., Any],
    timeout_error: type[BaseException],
    eval_args: tuple[Any, ...] = (),
    on_result: Callable[[str, dict[str, Any]], None] | None = None,
    on_wave: Callable[[dict[str, dict[str, Any]], list[str]], None] | None = None,
    prior_results: Mapping[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Evaluate tickers in waves until each has a decision or progress stalls.

    Returns results for every input ticker. Unresolved names are present with
    reason ``stage_a_timeout`` so the caller can label the scan partial.
    """
    ordered = [str(t).upper().strip() for t in tickers if str(t).strip()]
    results: dict[str, dict[str, Any]] = {
        str(k).upper(): v for k, v in (prior_results or {}).items() if isinstance(v, dict)
    }
    pending = [t for t in ordered if t not in results]
    stalls = 0
    waves = 0
    stall_limit = max(1, int(max_stalls))
    while pending:
        waves += 1
        before = len(results)
        timeout = min(
            float(wall_budget_sec),
            max(float(task_timeout_sec), float(task_timeout_sec) * max(1, len(pending))),
        )
        executor = executor_cls(max_workers=max(1, int(max_workers)))
        future_map: dict[Any, str] = {}
        timed_out = False
        try:
            for ticker in pending:
                future_map[executor.submit(evaluate, ticker, *eval_args)] = ticker
            try:
                for fut in as_completed(future_map.keys(), timeout=timeout):
                    ticker = future_map[fut]
                    try:
                        out = fut.result()
                    except Exception as exc:
                        out = {"ok": False, "reason": "exceptions", "error": f"{ticker}: {exc}"}
                    if not isinstance(out, dict):
                        out = {"ok": False, "reason": "exceptions", "error": f"{ticker}: bad stage A result"}
                    results[ticker] = out
                    if on_result is not None:
                        on_result(ticker, out)
            except timeout_error:
                timed_out = True
            _harvest(future_map, results, on_result)
        finally:
            _shutdown(executor, wait=not timed_out, cancel=timed_out)
        pending = [t for t in pending if t not in results]
        if on_wave is not None:
            on_wave(results, list(pending))
        if len(results) == before:
            stalls += 1
            if stalls >= stall_limit:
                break
        else:
            stalls = 0
    unresolved = list(pending)
    for ticker in unresolved:
        out = {"ok": False, "reason": "stage_a_timeout", "error": f"{ticker}: stage_a timeout"}
        results[ticker] = out
        if on_result is not None:
            on_result(ticker, out)
    return {
        "results": results,
        "waves": waves,
        "stalls": stalls,
        "unresolved": unresolved,
    }
