from __future__ import annotations

import os
import time

import redis

_client: redis.Redis | None = None


def _bool_env(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on"}


def _rate_limit_fail_open_allowed() -> bool:
    """Fail closed in production-like envs, but stay developer-friendly locally."""
    env = (os.getenv("ENV") or os.getenv("APP_ENV") or "").strip().lower()
    production_like = env in {"prod", "production", "staging"}
    raw = os.getenv("SAAS_RATE_LIMIT_FAIL_OPEN")
    if production_like:
        return _bool_env("SAAS_RATE_LIMIT_FAIL_OPEN", default=False)
    if raw is None or not str(raw).strip():
        return True
    return _bool_env("SAAS_RATE_LIMIT_FAIL_OPEN", default=False)


def redis_client() -> redis.Redis:
    global _client
    if _client is None:
        url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        _client = redis.from_url(url, decode_responses=True)
    return _client


def redis_ping() -> bool:
    try:
        return bool(redis_client().ping())
    except redis.RedisError:
        return False


def acquire_scan_cooldown(user_id: str, cooldown_sec: int) -> bool:
    """Return True if scan may proceed (key was absent). False if within cooldown."""
    try:
        key = f"saas:scan:cooldown:{user_id}"
        return bool(redis_client().set(key, "1", nx=True, ex=cooldown_sec))
    except redis.RedisError:
        fail_open = _rate_limit_fail_open_allowed()
        return bool(fail_open)


def fixed_window_rate_limit(user_id: str, bucket: str, limit: int, window_sec: int) -> tuple[bool, int]:
    """
    Return (allowed, current_count). On Redis failure default to deny (fail-closed).
    """
    try:
        r = redis_client()
        window_id = int(time.time()) // max(1, window_sec)
        key = f"saas:rl:{bucket}:{user_id}:{window_id}"
        n = int(r.incr(key))
        if n == 1:
            r.expire(key, window_sec)
        return n <= limit, n
    except redis.RedisError:
        fail_open = _rate_limit_fail_open_allowed()
        return bool(fail_open), 0


def order_idempotency_existing_task(user_id: str, idempotency_key: str) -> str | None:
    try:
        key = f"saas:idem:order:{user_id}:{idempotency_key}"
        v = redis_client().get(key)
        return str(v) if v else None
    except redis.RedisError:
        return None


_WORKER_BUSY_KEY = "saas:celery:worker_busy"
_WORKER_HEARTBEAT_KEY = "saas:celery:worker_heartbeat"


def mark_worker_busy(task_name: str, ttl_sec: int = 3600) -> None:
    """Signal that a long-running solo worker task is in flight (inspect will time out)."""
    try:
        r = redis_client()
        r.set(_WORKER_BUSY_KEY, str(task_name or "task"), ex=max(60, int(ttl_sec)))
        r.set(_WORKER_HEARTBEAT_KEY, str(int(time.time())), ex=max(60, int(ttl_sec)))
    except redis.RedisError:
        return


def clear_worker_busy() -> None:
    try:
        redis_client().delete(_WORKER_BUSY_KEY)
    except redis.RedisError:
        return


def worker_busy_hint() -> dict[str, str | int | bool]:
    """Best-effort busy signal for health checks when Celery inspect cannot answer."""
    try:
        r = redis_client()
        busy = r.get(_WORKER_BUSY_KEY)
        hb = r.get(_WORKER_HEARTBEAT_KEY)
        out: dict[str, str | int | bool] = {"busy": bool(busy)}
        if busy:
            out["task"] = str(busy)
        if hb:
            try:
                out["heartbeat_epoch"] = int(hb)
            except (TypeError, ValueError):
                pass
        return out
    except redis.RedisError:
        return {"busy": False, "inspect_error": True}


def order_idempotency_record_task(
    user_id: str, idempotency_key: str, task_id: str, ttl_sec: int = 86400
) -> None:
    try:
        key = f"saas:idem:order:{user_id}:{idempotency_key}"
        redis_client().set(key, task_id, ex=ttl_sec)
    except redis.RedisError:
        pass
