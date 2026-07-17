from __future__ import annotations

import webapp.saas_redis as sr


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def set(self, key: str, value: str, ex: int | None = None) -> None:  # noqa: ARG002
        self.store[key] = value

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def delete(self, *keys: str) -> int:
        n = 0
        for key in keys:
            if key in self.store:
                del self.store[key]
                n += 1
        return n

    def eval(self, script: str, numkeys: int, *args: str) -> int:  # noqa: ARG002
        keys = list(args[:numkeys])
        argv = list(args[numkeys:])
        cur = self.store.get(keys[0])
        if cur == argv[0]:
            self.delete(*keys)
            return 1
        return 0


def test_clear_worker_busy_is_token_scoped(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(sr, "redis_client", lambda: fake)

    first = sr.mark_worker_busy("webapp.scan_for_user")
    second = sr.mark_worker_busy("webapp.scan_for_user")
    assert fake.get(sr._WORKER_BUSY_KEY) == second

    # Older task finishing must not wipe the newer stamp.
    sr.clear_worker_busy(first)
    assert fake.get(sr._WORKER_BUSY_KEY) == second
    assert sr.worker_busy_hint()["busy"] is True

    sr.clear_worker_busy(second)
    assert fake.get(sr._WORKER_BUSY_KEY) is None
    assert fake.get(sr._WORKER_HEARTBEAT_KEY) is None
    assert sr.worker_busy_hint()["busy"] is False


def test_heartbeat_grace_infers_busy_after_race_clear(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(sr, "redis_client", lambda: fake)
    monkeypatch.setenv("SAAS_BUSY_HEARTBEAT_GRACE_SEC", "900")

    token = sr.mark_worker_busy("webapp.scan_for_user")
    # Simulate old clear that only deleted the busy key.
    fake.delete(sr._WORKER_BUSY_KEY)
    assert fake.get(sr._WORKER_BUSY_KEY) is None
    assert fake.get(sr._WORKER_HEARTBEAT_KEY) is not None

    hint = sr.worker_busy_hint()
    assert hint["busy"] is True
    assert hint.get("busy_inferred_from_heartbeat") is True

    # Explicit token clear of a stale token is a no-op after race; unscoped clear
    # still works for operators.
    sr.clear_worker_busy(token)
    assert fake.get(sr._WORKER_HEARTBEAT_KEY) is not None
    sr.clear_worker_busy()
    assert sr.worker_busy_hint()["busy"] is False
