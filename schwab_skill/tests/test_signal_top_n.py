"""
Regression tests for SIGNAL_TOP_N parsing and Stage A shortlist sizing.

Background: `_get_int` clamps integer env vars to >=1, which silently broke the
documented "SIGNAL_TOP_N=0 means return all ranked signals" contract that
`_compute_stage_a_shortlist_limit` and `/api/scan` rely on. With the bug in
place a default API scan would dispatch Stage B on only ~3 candidates and
truncate the final response to 1 signal, even when Stage A had ranked the
full SP1500 universe. These tests pin the corrected behavior.
"""

from __future__ import annotations

import config
import signal_scanner


def test_signal_top_n_default_when_unset(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("SIGNAL_TOP_N", raising=False)
    config.clear_env_cache()
    # tmp_path has no `.env`, so the getter falls back to its hard-coded default.
    assert config.get_signal_top_n(tmp_path) == 5


def test_signal_top_n_zero_passes_through(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SIGNAL_TOP_N", "0")
    config.clear_env_cache()
    assert config.get_signal_top_n(tmp_path) == 0


def test_signal_top_n_negative_clamps_to_zero(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SIGNAL_TOP_N", "-3")
    config.clear_env_cache()
    assert config.get_signal_top_n(tmp_path) == 0


def test_signal_top_n_positive_passes_through(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SIGNAL_TOP_N", "10")
    config.clear_env_cache()
    assert config.get_signal_top_n(tmp_path) == 10


def test_signal_top_n_garbage_falls_back_to_default(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SIGNAL_TOP_N", "not-an-int")
    config.clear_env_cache()
    assert config.get_signal_top_n(tmp_path) == 5


def test_shortlist_limit_unbounded_when_top_n_zero_and_no_ceiling() -> None:
    n = signal_scanner._compute_stage_a_shortlist_limit(
        total_candidates=1506,
        top_n=0,
        multiplier=3.0,
        cap=40,
        nocap_limit=0,
    )
    assert n == 1506


def test_shortlist_limit_bounded_by_nocap_limit_when_top_n_zero() -> None:
    n = signal_scanner._compute_stage_a_shortlist_limit(
        total_candidates=1506,
        top_n=0,
        multiplier=3.0,
        cap=40,
        nocap_limit=250,
    )
    assert n == 250


def test_shortlist_limit_respects_cap_when_top_n_positive() -> None:
    # cap should still apply for explicit top-N runs (preserves existing behavior).
    n = signal_scanner._compute_stage_a_shortlist_limit(
        total_candidates=100,
        top_n=10,
        multiplier=3.0,
        cap=20,
        nocap_limit=250,
    )
    assert n == 20


def test_shortlist_limit_explicit_top_n_unaffected_by_default_call() -> None:
    # Calling without nocap_limit should not regress prior callers.
    n = signal_scanner._compute_stage_a_shortlist_limit(
        total_candidates=17,
        top_n=5,
        multiplier=3.0,
        cap=40,
    )
    assert n == 15


def test_scan_stage_a_nocap_limit_default_is_finite(tmp_path) -> None:
    # Default ceiling should be a sane positive number so an unset env still
    # bounds Stage B work when SIGNAL_TOP_N=0.
    assert config.get_scan_stage_a_nocap_limit(tmp_path) >= 50


def test_scan_stage_a_nocap_limit_env_override(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("SCAN_STAGE_A_NOCAP_LIMIT", raising=False)
    (tmp_path / ".env").write_text("SCAN_STAGE_A_NOCAP_LIMIT=400\n")
    config.clear_env_cache()
    assert config.get_scan_stage_a_nocap_limit(tmp_path) == 400
