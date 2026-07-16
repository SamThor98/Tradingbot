"""Frozen Phase 0 observability schema for the Trading Cockpit.

This module locks the metric names every later phase emits into, so dashboards
and release gates stay stable as cockpit complexity grows. It is intentionally
side-effect-safe: instrumentation must never break the trading path, so every
public function swallows its own errors.

Storage
-------
- **Local**: a compact rolling-window JSON file (``cockpit_observability_metrics.json``)
  in ``skill_dir``, mirroring the shape/behavior of ``execution_safety_metrics.json``.
- **SaaS**: if ``webapp.prometheus_metrics`` exposes matching collectors they are
  updated too; otherwise the JSON file remains the single source of truth.

Frozen metric names (do not rename — only add)
----------------------------------------------
- ``schwab_request_latency_ms``     histogram  labels: endpoint, session
- ``schwab_request_errors_total``   counter    labels: endpoint, http_status
- ``data_fallback_total``           counter    labels: provider, reason
- ``data_stale_ratio``              gauge      labels: domain
- ``provider_confidence_total``     counter    labels: domain, confidence
- ``circuit_breaker_state``         gauge      labels: breaker
"""

from __future__ import annotations

import atexit
import logging
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

LOG = logging.getLogger(__name__)

_METRICS_FILE = "cockpit_observability_metrics.json"
_ROLLING_WINDOW_DAYS = 45
_FLUSH_DEBOUNCE_SEC = 1.0
SKILL_DIR = Path(__file__).resolve().parent.parent

_metrics_lock = threading.Lock()
_metrics_cache: dict[str, dict[str, Any]] = {}
_metrics_dirty: set[str] = set()
_metrics_last_save: dict[str, float] = {}

# Frozen metric name constants (import these; never hardcode the strings).
M_REQUEST_LATENCY_MS = "schwab_request_latency_ms"
M_REQUEST_ERRORS = "schwab_request_errors_total"
M_DATA_FALLBACK = "data_fallback_total"
M_DATA_STALE_RATIO = "data_stale_ratio"
M_PROVIDER_CONFIDENCE = "provider_confidence_total"
M_CIRCUIT_BREAKER_STATE = "circuit_breaker_state"


def _enabled(skill_dir: Path | None) -> bool:
    try:
        from config import get_observability_metrics_enabled

        return bool(get_observability_metrics_enabled(skill_dir))
    except Exception:
        return True


def _metrics_path(skill_dir: Path) -> Path:
    return skill_dir / _METRICS_FILE


def _cache_key(path: Path) -> str:
    return str(path.resolve())


def _get_cached(path: Path) -> dict[str, Any]:
    key = _cache_key(path)
    with _metrics_lock:
        if key not in _metrics_cache:
            _metrics_cache[key] = _load(path)
        return _metrics_cache[key]


def flush_observability_metrics(skill_dir: Path | str | None = None, *, force: bool = True) -> None:
    """Persist in-memory observability buckets (debounced writers call with force=True)."""
    sd = Path(skill_dir or SKILL_DIR)
    path = _metrics_path(sd)
    key = _cache_key(path)
    with _metrics_lock:
        if key not in _metrics_dirty:
            return
        if not force:
            last = _metrics_last_save.get(key, 0.0)
            if time.monotonic() - last < _FLUSH_DEBOUNCE_SEC:
                return
        data = _metrics_cache.get(key)
        if not isinstance(data, dict):
            _metrics_dirty.discard(key)
            return
        _metrics_dirty.discard(key)
    _save(path, data)
    with _metrics_lock:
        _metrics_last_save[key] = time.monotonic()


def _flush_all_observability_metrics() -> None:
    with _metrics_lock:
        keys = list(_metrics_dirty)
    for key in keys:
        try:
            flush_observability_metrics(Path(key).parent, force=True)
        except Exception:
            pass


atexit.register(_flush_all_observability_metrics)


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"days": {}}
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("days"), dict):
            return data
    except Exception as exc:
        LOG.warning("Ignoring unreadable observability metrics file %s: %s", path, exc)
    return {"days": {}}


def _save(path: Path, data: dict[str, Any]) -> None:
    try:
        from _io_utils import atomic_write_json

        atomic_write_json(path, data, indent=2)
    except Exception as exc:  # pragma: no cover - disk failure must not break trading
        LOG.warning("Could not persist observability metrics to %s: %s", path, exc)


def _today_bucket(data: dict[str, Any]) -> dict[str, Any]:
    days = data.setdefault("days", {})
    today = date.today().isoformat()
    bucket = days.setdefault(
        today,
        {"counters": {}, "latency": {}, "gauges": {}},
    )
    bucket.setdefault("counters", {})
    bucket.setdefault("latency", {})
    bucket.setdefault("gauges", {})
    return bucket


def _prune(data: dict[str, Any]) -> None:
    cutoff = (date.today() - timedelta(days=_ROLLING_WINDOW_DAYS)).isoformat()
    days = data.get("days", {})
    for k in [k for k in days if k < cutoff]:
        days.pop(k, None)


def _mutate(skill_dir: Path | None, fn: Any) -> None:
    """Load → mutate today's bucket → prune → debounced save, guarding all failures."""
    if not _enabled(skill_dir):
        return
    try:
        sd = Path(skill_dir or SKILL_DIR)
        path = _metrics_path(sd)
        data = _get_cached(path)
        fn(_today_bucket(data))
        _prune(data)
        key = _cache_key(path)
        with _metrics_lock:
            _metrics_dirty.add(key)
        flush_observability_metrics(sd, force=False)
    except Exception as exc:  # pragma: no cover
        LOG.debug("observability emit skipped: %s", exc)


def _label_key(name: str, **labels: Any) -> str:
    if not labels:
        return name
    parts = ",".join(f"{k}={labels[k]}" for k in sorted(labels) if labels[k] is not None)
    return f"{name}{{{parts}}}"


# --------------------------------------------------------------------------- #
# Emitters
# --------------------------------------------------------------------------- #
def record_request_latency(skill_dir: Path | None, endpoint: str, session: str, latency_ms: float) -> None:
    key = _label_key(M_REQUEST_LATENCY_MS, endpoint=endpoint, session=session)

    def _fn(bucket: dict[str, Any]) -> None:
        lat = bucket["latency"].setdefault(key, {"count": 0, "sum_ms": 0.0, "max_ms": 0.0})
        lat["count"] = int(lat.get("count", 0)) + 1
        lat["sum_ms"] = float(lat.get("sum_ms", 0.0)) + float(latency_ms)
        lat["max_ms"] = max(float(lat.get("max_ms", 0.0)), float(latency_ms))

    _mutate(skill_dir, _fn)
    _prom_observe_hist(M_REQUEST_LATENCY_MS, latency_ms, endpoint=endpoint, session=session)


def record_request_error(skill_dir: Path | None, endpoint: str, http_status: int | str | None) -> None:
    key = _label_key(M_REQUEST_ERRORS, endpoint=endpoint, http_status=http_status or "exception")
    _incr_counter(skill_dir, key)
    _prom_incr(M_REQUEST_ERRORS, endpoint=endpoint, http_status=str(http_status or "exception"))


def record_fallback(skill_dir: Path | None, provider: str, reason: str | None) -> None:
    key = _label_key(M_DATA_FALLBACK, provider=provider, reason=(reason or "unknown")[:80])
    _incr_counter(skill_dir, key)
    _prom_incr(M_DATA_FALLBACK, provider=provider, reason=(reason or "unknown")[:80])


def record_provider_confidence(skill_dir: Path | None, domain: str, confidence: str) -> None:
    key = _label_key(M_PROVIDER_CONFIDENCE, domain=domain, confidence=confidence)
    _incr_counter(skill_dir, key)
    _prom_incr(M_PROVIDER_CONFIDENCE, domain=domain, confidence=confidence)


def set_stale_ratio(skill_dir: Path | None, domain: str, ratio: float) -> None:
    key = _label_key(M_DATA_STALE_RATIO, domain=domain)
    _set_gauge(skill_dir, key, float(ratio))
    _prom_set(M_DATA_STALE_RATIO, float(ratio), domain=domain)


def set_circuit_breaker_state(skill_dir: Path | None, breaker: str, is_open: bool) -> None:
    key = _label_key(M_CIRCUIT_BREAKER_STATE, breaker=breaker)
    _set_gauge(skill_dir, key, 1.0 if is_open else 0.0)
    _prom_set(M_CIRCUIT_BREAKER_STATE, 1.0 if is_open else 0.0, breaker=breaker)


def observe_lineage(skill_dir: Path | None, domain: str, meta: dict[str, Any] | None) -> None:
    """Convenience: emit fallback + confidence metrics from a lineage dict.

    Reads the same keys ``Provenance.from_lineage`` understands so the metric
    stream and the DTO trust label can never disagree.
    """
    try:
        from core.contracts.provenance import Provenance

        prov = Provenance.from_lineage(meta)
        record_provider_confidence(skill_dir, domain, prov.confidence)
        if (meta or {}).get("used_fallback") or (meta or {}).get("used_fallback_data"):
            record_fallback(skill_dir, prov.source, (meta or {}).get("fallback_reason"))
    except Exception as exc:  # pragma: no cover
        LOG.debug("observe_lineage skipped: %s", exc)


def _incr_counter(skill_dir: Path | None, key: str, by: int = 1) -> None:
    def _fn(bucket: dict[str, Any]) -> None:
        c = bucket["counters"]
        c[key] = int(c.get(key, 0)) + int(by)

    _mutate(skill_dir, _fn)


def _set_gauge(skill_dir: Path | None, key: str, value: float) -> None:
    def _fn(bucket: dict[str, Any]) -> None:
        bucket["gauges"][key] = {"value": float(value), "ts": datetime.now(timezone.utc).isoformat()}

    _mutate(skill_dir, _fn)


@contextmanager
def timed_request(skill_dir: Path | None, endpoint: str, session: str) -> Iterator[None]:
    """Wrap a Schwab call to emit latency (always) and error (on exception)."""
    start = time.perf_counter()
    try:
        yield
    except Exception:
        record_request_error(skill_dir, endpoint, None)
        raise
    finally:
        record_request_latency(skill_dir, endpoint, session, (time.perf_counter() - start) * 1000.0)


# --------------------------------------------------------------------------- #
# Optional Prometheus bridge (SaaS). No-op if collectors are unavailable.
# --------------------------------------------------------------------------- #
def _prom():  # pragma: no cover - exercised only with prometheus installed
    try:
        import webapp.prometheus_metrics as pm  # type: ignore

        return pm
    except Exception:
        return None


def _prom_incr(name: str, **labels: Any) -> None:  # pragma: no cover
    pm = _prom()
    fn = getattr(pm, f"incr_{name}", None) if pm else None
    if callable(fn):
        try:
            fn(**labels)
        except Exception:
            pass


def _prom_observe_hist(name: str, value: float, **labels: Any) -> None:  # pragma: no cover
    pm = _prom()
    fn = getattr(pm, f"observe_{name}", None) if pm else None
    if callable(fn):
        try:
            fn(value, **labels)
        except Exception:
            pass


def _prom_set(name: str, value: float, **labels: Any) -> None:  # pragma: no cover
    pm = _prom()
    fn = getattr(pm, f"set_{name}", None) if pm else None
    if callable(fn):
        try:
            fn(value, **labels)
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Read side (dashboards / validators)
# --------------------------------------------------------------------------- #
def get_observability_summary(skill_dir: Path | str | None = None, days: int = 1) -> dict[str, Any]:
    """Aggregate the rolling window into a flat, dashboard-friendly summary."""
    sd = Path(skill_dir or SKILL_DIR)
    flush_observability_metrics(sd, force=True)
    data = _get_cached(_metrics_path(sd))
    all_days = data.get("days", {})
    day_keys = sorted(all_days.keys())
    take = day_keys[-max(1, int(days)) :] if day_keys else []

    counters: dict[str, int] = {}
    latency: dict[str, dict[str, float]] = {}
    gauges: dict[str, float] = {}
    for d in take:
        bucket = all_days.get(d, {})
        for k, v in (bucket.get("counters", {}) or {}).items():
            counters[k] = counters.get(k, 0) + int(v or 0)
        for k, v in (bucket.get("latency", {}) or {}).items():
            agg = latency.setdefault(k, {"count": 0.0, "sum_ms": 0.0, "max_ms": 0.0})
            agg["count"] += float(v.get("count", 0) or 0)
            agg["sum_ms"] += float(v.get("sum_ms", 0.0) or 0.0)
            agg["max_ms"] = max(agg["max_ms"], float(v.get("max_ms", 0.0) or 0.0))
        for k, v in (bucket.get("gauges", {}) or {}).items():
            gauges[k] = float((v or {}).get("value", 0.0) or 0.0)

    latency_avg = {k: round(v["sum_ms"] / v["count"], 2) if v["count"] else 0.0 for k, v in latency.items()}
    return {
        "window_days": max(1, int(days)),
        "days_present": len(take),
        "counters": counters,
        "latency_avg_ms": latency_avg,
        "latency_max_ms": {k: round(v["max_ms"], 2) for k, v in latency.items()},
        "gauges": gauges,
    }
