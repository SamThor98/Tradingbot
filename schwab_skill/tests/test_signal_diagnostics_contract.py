from __future__ import annotations

import sys
import types

import signal_scanner


def test_scan_diagnostics_contract_on_auth_failure(monkeypatch) -> None:
    fake_notifier = types.SimpleNamespace(send_alert=lambda *_args, **_kwargs: None)

    class _BoomAuth:
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("auth unavailable")

    fake_schwab_auth = types.SimpleNamespace(DualSchwabAuth=_BoomAuth)
    monkeypatch.setitem(sys.modules, "notifier", fake_notifier)
    monkeypatch.setitem(sys.modules, "schwab_auth", fake_schwab_auth)

    signals, diagnostics = signal_scanner.scan_for_signals_detailed()
    assert signals == []
    assert diagnostics["scan_blocked"] == 0
    assert diagnostics["data_failure_count"] == 1
    assert diagnostics["scan_id"]
    assert "prediction_market" in diagnostics and isinstance(diagnostics["prediction_market"], dict)
    assert "meta_policy" in diagnostics and isinstance(diagnostics["meta_policy"], dict)
    assert "uncertainty" in diagnostics and isinstance(diagnostics["uncertainty"], dict)
