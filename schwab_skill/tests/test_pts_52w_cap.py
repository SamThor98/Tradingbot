from __future__ import annotations

from stage_analysis import evaluate_pts_52w_cap, pts_52w_cap_blocks_stage_a


def test_pts_52w_cap_shadow_flags_above_max(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PTS_52W_CAP_MODE", "shadow")
    monkeypatch.setenv("PTS_52W_CAP_MAX", "37")
    result = evaluate_pts_52w_cap(38.0, tmp_path)
    assert result["mode"] == "shadow"
    assert result["would_filter"] is True
    assert "pts_52w_cap_high" in result["would_filter_reasons"]
    assert pts_52w_cap_blocks_stage_a(result, tmp_path) is False


def test_pts_52w_cap_live_blocks_above_max(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PTS_52W_CAP_MODE", "live")
    monkeypatch.setenv("PTS_52W_CAP_MAX", "37")
    result = evaluate_pts_52w_cap(37.01, tmp_path)
    assert result["would_filter"] is True
    assert pts_52w_cap_blocks_stage_a(result, tmp_path) is True


def test_pts_52w_cap_allows_at_or_below_max(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PTS_52W_CAP_MODE", "live")
    monkeypatch.setenv("PTS_52W_CAP_MAX", "37")
    result = evaluate_pts_52w_cap(37.0, tmp_path)
    assert result["would_filter"] is False
    assert pts_52w_cap_blocks_stage_a(result, tmp_path) is False


def test_pts_52w_cap_off_skips(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PTS_52W_CAP_MODE", "off")
    result = evaluate_pts_52w_cap(40.0, tmp_path)
    assert result["mode"] == "off"
    assert result["would_filter"] is False
    assert pts_52w_cap_blocks_stage_a(result, tmp_path) is False


def test_pts_52w_cap_default_is_live(tmp_path, monkeypatch) -> None:
    from config import clear_env_cache, get_pts_52w_cap_max, get_pts_52w_cap_mode

    monkeypatch.delenv("PTS_52W_CAP_MODE", raising=False)
    monkeypatch.delenv("PTS_52W_CAP_MAX", raising=False)
    clear_env_cache()
    assert get_pts_52w_cap_mode(tmp_path) == "live"
    assert get_pts_52w_cap_max(tmp_path) == 37.0
