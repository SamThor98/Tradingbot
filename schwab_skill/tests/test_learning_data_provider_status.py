from __future__ import annotations

import sys
import types

from webapp.routes import learning


def test_data_provider_status_reuses_singleton(monkeypatch) -> None:
    calls = {"inits": 0}

    class _FakeProvider:
        def __init__(self, skill_dir=None):
            calls["inits"] += 1
            self._status_calls = 0

        def status(self):
            self._status_calls += 1
            return {"status_calls": self._status_calls}

    monkeypatch.setitem(sys.modules, "data_provider", types.SimpleNamespace(DataProvider=_FakeProvider))
    monkeypatch.setattr(learning, "_DATA_PROVIDER_INSTANCE", None)

    first = learning.data_provider_status()
    second = learning.data_provider_status()

    assert first.ok is True
    assert second.ok is True
    assert first.data["status_calls"] == 1
    assert second.data["status_calls"] == 2
    assert calls["inits"] == 1
