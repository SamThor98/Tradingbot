from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR / "scripts"))

from backfill_counterfactual_from_last_scan import events_from_last_scan  # noqa: E402
from score_counterfactual_outcomes import _forward_return_from_closes  # noqa: E402


def test_events_from_last_scan_logs_shadow_suppress_and_fired() -> None:
    last_scan = {
        "at": "2026-09-04T14:12:47+00:00",
        "signals": [
            {
                "ticker": "DXCM",
                "signal_score": 46.19,
                "mirofish_conviction": 5,
                "meta_policy": {"decision": "suppress", "shadow_action": "suppress"},
            },
            {
                "ticker": "TECH",
                "signal_score": 53.5,
                "mirofish_conviction": -2,
                "meta_policy": {"decision": "allow", "shadow_action": "allow"},
            },
        ],
    }
    events = events_from_last_scan(last_scan, horizon_days=20)
    reasons = {(e["ticker"], e["reason"]) for e in events}
    assert ("DXCM", "meta_policy_would_suppress") in reasons
    assert ("DXCM", "fired") in reasons
    assert ("TECH", "fired") in reasons
    assert ("TECH", "meta_policy_would_suppress") not in reasons
    assert all(e["source"] == "last_scan_backfill" for e in events)
    assert all(e["ts"] == "2026-09-04T14:12:47+00:00" for e in events)


def test_forward_return_uses_next_session_after_holiday_weekend() -> None:
    closes = [
        (datetime(2026, 9, 4), 100.0),
        (datetime(2026, 9, 8), 98.0),
    ]
    ret = _forward_return_from_closes(closes, datetime(2026, 9, 4, 14, 12), 1)
    assert ret == -0.02
