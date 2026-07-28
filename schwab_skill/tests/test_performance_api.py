from __future__ import annotations

from webapp.routes import learning


def test_performance_endpoint_returns_separated_buckets() -> None:
    """Regression: validation status must receive skill_dir (was a 500)."""
    out = learning.performance()
    assert out.ok is True
    assert isinstance(out.data, dict)
    for key in ("backtest", "shadow_paper", "live", "validation", "separation_guard", "challenger"):
        assert key in out.data
    assert out.data["separation_guard"]["commingled_metric_allowed"] is False
    status = out.data["validation"]["status"]
    assert isinstance(status, dict)
    assert "exists" in status
    assert "run_status" in status
