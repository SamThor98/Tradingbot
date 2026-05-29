from __future__ import annotations

import json
import math
from datetime import datetime, timezone

from webapp.db import _json_safe, _json_serializer


def test_json_safe_replaces_nan_and_inf_with_none() -> None:
    assert _json_safe(float("nan")) is None
    assert _json_safe(float("inf")) is None
    assert _json_safe(float("-inf")) is None
    assert _json_safe(1.5) == 1.5


def test_json_safe_recurses_into_dicts_and_lists() -> None:
    payload = {
        "ok": 1.0,
        "bad": float("nan"),
        "nested": {"x": float("inf"), "y": [1.0, float("nan"), 3.0]},
        "tuple": (float("-inf"), 2.0),
    }
    out = _json_safe(payload)
    assert out["ok"] == 1.0
    assert out["bad"] is None
    assert out["nested"]["x"] is None
    assert out["nested"]["y"] == [1.0, None, 3.0]
    assert out["tuple"] == [None, 2.0]


def test_json_serializer_emits_valid_json_for_nan() -> None:
    # The Postgres jsonb driver rejects NaN/Infinity tokens; our serializer must
    # never emit them. json.loads with the (default) strict parser proves it.
    raw = _json_serializer({"score": float("nan"), "rsi": float("inf"), "px": 12.3})
    parsed = json.loads(raw)  # would raise if 'NaN'/'Infinity' tokens were present
    assert parsed["score"] is None
    assert parsed["rsi"] is None
    assert parsed["px"] == 12.3
    assert "NaN" not in raw and "Infinity" not in raw


def test_json_serializer_handles_datetime() -> None:
    dt = datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc)
    raw = _json_serializer({"at": dt})
    assert json.loads(raw)["at"] == dt.isoformat()


def test_json_safe_passthrough_for_plain_types() -> None:
    assert math.isclose(_json_safe(2.0), 2.0)
    assert _json_safe("s") == "s"
    assert _json_safe(None) is None
    assert _json_safe(7) == 7
