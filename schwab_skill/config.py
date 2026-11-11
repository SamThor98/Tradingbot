"""
Load configurable parameters from .env for Stage 2, VCP, signal scoring, and data.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)
SKILL_DIR = Path(__file__).resolve().parent
_USER_TRADING_HALT_STATE = ".user_trading_halt.json"

# Cache parsed `.env` files keyed by absolute path. Each entry stores the file's
# mtime_ns alongside the parsed values so we can invalidate when the file
# changes on disk. Previously every call to a getter (e.g. `_get_float`) would
# re-open and re-parse `.env`, which became a hot path during scans.
_ENV_CACHE: dict[str, tuple[int, dict[str, str]]] = {}
_ENV_CACHE_LOCK = threading.Lock()


def _parse_env_file(path: Path) -> dict[str, str]:
    vals: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            vals[k.strip()] = v.strip().strip("\"'")
    return vals


def _load_env(skill_dir: Path | None = None) -> dict[str, str]:
    path = (skill_dir or SKILL_DIR) / ".env"
    try:
        st = path.stat()
    except FileNotFoundError:
        return {}
    except OSError:
        return {}
    cache_key = str(path)
    mtime = int(getattr(st, "st_mtime_ns", 0) or int(st.st_mtime * 1e9))
    with _ENV_CACHE_LOCK:
        cached = _ENV_CACHE.get(cache_key)
        if cached and cached[0] == mtime:
            return cached[1]
    try:
        parsed = _parse_env_file(path)
    except OSError:
        return {}
    with _ENV_CACHE_LOCK:
        _ENV_CACHE[cache_key] = (mtime, parsed)
    return parsed


def clear_env_cache() -> None:
    """Force a full reload of `.env` on the next getter call.

    Useful in tests and after `_apply_temporary_env` patches the file.
    """
    with _ENV_CACHE_LOCK:
        _ENV_CACHE.clear()


def bootstrap_dotenv_into_environ(skill_dir: Path | None = None) -> list[str]:
    """Copy parsed `.env` values into ``os.environ`` for callers that only
    consult ``os.getenv`` (e.g. ``sec_filing_analysis._call_llm_summary``,
    ``webapp.strategy_chat.run_strategy_chat``).

    Existing process-environment values (Render/Docker injected, or
    deliberately set via shell ``set``/``export``) always win — we only
    populate keys that are unset or empty in ``os.environ`` so live deploys
    keep their authoritative secrets.

    Returns the list of keys actually promoted, mostly for logging/tests.
    """
    env = _load_env(skill_dir)
    promoted: list[str] = []
    for key, value in env.items():
        if not key or value is None:
            continue
        existing = os.environ.get(key)
        if existing is not None and existing.strip() != "":
            continue
        os.environ[key] = value
        promoted.append(key)
    return promoted


def _env_value(key: str, env: dict[str, str]) -> str:
    """
    Resolve config with process override precedence.

    Process env overrides let scripts tune parameters per run without editing
    local `.env`.
    """
    raw = os.environ.get(key)
    if raw is not None and str(raw).strip() != "":
        return str(raw)
    return str(env.get(key, ""))


def _get_float(key: str, default: float, skill_dir: Path | None = None) -> float:
    env = _load_env(skill_dir)
    v = _env_value(key, env)
    if not v:
        return default
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def _get_int(key: str, default: int, skill_dir: Path | None = None) -> int:
    env = _load_env(skill_dir)
    v = _env_value(key, env)
    if not v:
        return default
    try:
        return max(1, int(float(v)))
    except (ValueError, TypeError):
        return default


def _get_bool(key: str, default: bool, skill_dir: Path | None = None) -> bool:
    env = _load_env(skill_dir)
    v = _env_value(key, env).strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    if v in ("1", "true", "yes", "on"):
        return True
    return default


def _get_mode(
    key: str,
    allowed: set[str],
    default: str,
    skill_dir: Path | None = None,
) -> str:
    env = _load_env(skill_dir)
    raw = _env_value(key, env).strip().lower()
    if raw in allowed:
        return raw
    return default


PLUGIN_MODE_VALUES = {"off", "shadow", "live"}


def get_self_study_mode(skill_dir: Path | None = None) -> str:
    """Self-study rollout. ``SELF_STUDY_MODE`` wins over legacy ``SELF_STUDY_ENABLED``.

    Missing config is off. ``SELF_STUDY_ENABLED=true`` with no mode means live.
    """
    env = _load_env(skill_dir)
    raw = _env_value("SELF_STUDY_MODE", env).strip().lower()
    if raw in PLUGIN_MODE_VALUES:
        return raw
    legacy = _env_value("SELF_STUDY_ENABLED", env).strip().lower()
    if legacy in {"1", "true", "yes", "on"}:
        return "live"
    return "off"


def get_pred_market_enabled(skill_dir: Path | None = None) -> bool:
    """Enable prediction-market metadata enrichment."""
    return _get_bool("PRED_MARKET_ENABLED", False, skill_dir)


def get_pred_market_mode(skill_dir: Path | None = None) -> str:
    """Prediction-market rollout mode (OFF|SHADOW|LIVE)."""
    return _get_mode("PRED_MARKET_MODE", PLUGIN_MODE_VALUES, "off", skill_dir)


def get_pred_market_provider(skill_dir: Path | None = None) -> str:
    """
    Prediction-market provider id.
    Allowed: stub, polymarket.
    """
    env = _load_env(skill_dir)
    raw = _env_value("PRED_MARKET_PROVIDER", env).strip().lower()
    if raw in {"stub", "polymarket"}:
        return raw
    return "stub"


def get_liquid_overlay_mode(skill_dir: Path | None = None) -> str:
    """Liquid positioning overlay rollout mode (OFF|SHADOW|LIVE).

    Shadow-first per plugin rollout rules. LIVE here means rank/display
    influence only — Liquid execution is never integrated; Schwab stays
    the broker of record.
    """
    return _get_mode("LIQUID_OVERLAY_MODE", PLUGIN_MODE_VALUES, "shadow", skill_dir)


def get_liquid_collect_enabled(skill_dir: Path | None = None) -> bool:
    """Enable the Liquid (xyz) snapshot collector. Default off until opted in."""
    return _get_bool("LIQUID_COLLECT_ENABLED", False, skill_dir)


def get_liquid_dex(skill_dir: Path | None = None) -> str:
    """Hyperliquid HIP-3 dex hosting Liquid's equity perps."""
    env = _load_env(skill_dir)
    raw = _env_value("LIQUID_DEX", env).strip().lower()
    return raw if raw else "xyz"


def get_liquid_api_url(skill_dir: Path | None = None) -> str:
    """Hyperliquid public info API endpoint (no credentials)."""
    env = _load_env(skill_dir)
    raw = _env_value("LIQUID_API_URL", env).strip()
    return raw if raw else "https://api.hyperliquid.xyz/info"


def get_liquid_min_oi_usd(skill_dir: Path | None = None) -> float:
    """OI (USD) below this is no-signal: snapshot kept, flagged thin."""
    val = _get_float("LIQUID_MIN_OI_USD", 1_000_000.0, skill_dir)
    return max(0.0, val)


def get_pred_market_timeout_ms(skill_dir: Path | None = None) -> int:
    """Per-request timeout in milliseconds for prediction-market provider calls."""
    val = _get_int("PRED_MARKET_TIMEOUT_MS", 1200, skill_dir)
    return max(100, min(15000, val))


def get_pred_market_cache_ttl_sec(skill_dir: Path | None = None) -> int:
    """Cache TTL in seconds for provider responses."""
    val = _get_int("PRED_MARKET_CACHE_TTL_SEC", 300, skill_dir)
    return max(10, min(86400, val))


def get_pred_market_max_event_age_hours(skill_dir: Path | None = None) -> float:
    """Maximum age for event metadata before considered stale."""
    val = _get_float("PRED_MARKET_MAX_EVENT_AGE_HOURS", 24.0, skill_dir)
    return max(0.25, min(240.0, val))


def get_pred_market_min_liquidity(skill_dir: Path | None = None) -> float:
    """Minimum acceptable market liquidity for overlay usage."""
    val = _get_float("PRED_MARKET_MIN_LIQUIDITY", 1000.0, skill_dir)
    return max(0.0, val)


def get_pred_market_max_spread(skill_dir: Path | None = None) -> float:
    """Maximum acceptable spread (0..1) before ignoring metadata."""
    val = _get_float("PRED_MARKET_MAX_SPREAD", 0.08, skill_dir)
    return max(0.0, min(1.0, val))


def get_pred_market_min_match_confidence(skill_dir: Path | None = None) -> float:
    """Minimum acceptable PM event-ticker match confidence (0..1)."""
    val = _get_float("PRED_MARKET_MIN_MATCH_CONFIDENCE", 0.55, skill_dir)
    return max(0.0, min(1.0, val))


def get_pred_market_score_delta_clamp(skill_dir: Path | None = None) -> float:
    """Clamp (absolute) applied to PM score delta when overlay is live."""
    val = _get_float("PRED_MARKET_SCORE_DELTA_CLAMP", 2.0, skill_dir)
    return max(0.0, min(10.0, val))


def get_pred_market_size_mult_min(skill_dir: Path | None = None) -> float:
    """Lower bound for PM position-size multiplier."""
    val = _get_float("PRED_MARKET_SIZE_MULT_MIN", 0.9, skill_dir)
    return max(0.1, min(1.0, val))


def get_pred_market_size_mult_max(skill_dir: Path | None = None) -> float:
    """Upper bound for PM position-size multiplier."""
    val = _get_float("PRED_MARKET_SIZE_MULT_MAX", 1.1, skill_dir)
    return max(1.0, min(3.0, val))


def get_pred_market_advisory_delta_clamp(skill_dir: Path | None = None) -> float:
    """Clamp (absolute) applied to advisory probability delta."""
    val = _get_float("PRED_MARKET_ADVISORY_DELTA_CLAMP", 0.02, skill_dir)
    return max(0.0, min(0.25, val))


def get_pred_market_min_baseline_score(skill_dir: Path | None = None) -> float:
    """Minimum baseline signal score required before PM overlay can apply."""
    val = _get_float("PRED_MARKET_MIN_BASELINE_SCORE", 55.0, skill_dir)
    return max(0.0, min(100.0, val))


# Stage 2: price must be within this fraction of 52-week high (0.85 = within 15%)
def get_stage2_52w_pct(skill_dir: Path | None = None) -> float:
    return _get_float("STAGE2_52W_PCT", 0.85, skill_dir)


# Stage 2: 200 SMA must be upward for this many days
def get_stage2_sma_upward_days(skill_dir: Path | None = None) -> int:
    return _get_int("STAGE2_SMA_UPWARD_DAYS", 20, skill_dir)


def get_signal_edge_shadow_mode(skill_dir: Path | None = None) -> str:
    """P0 signal-edge shadow diagnostics (rank-filter + Stage 2 tighten counters).

    off — disable shadow counters. shadow — count would-filter actions only (no blocking).
    """
    return _get_mode("SIGNAL_EDGE_SHADOW_MODE", PLUGIN_MODE_VALUES, "shadow", skill_dir)


def get_rank_filter_shadow_min_percentile_composite(skill_dir: Path | None = None) -> int:
    """Min score percentile for composite rank-filter shadow (default p50 from counterfactual)."""
    return max(0, min(95, _get_int("RANK_FILTER_SHADOW_MIN_PERCENTILE_COMPOSITE", 50, skill_dir)))


def get_rank_filter_shadow_min_percentile_rank_v2(skill_dir: Path | None = None) -> int:
    """Min score percentile for rank_score_v2 filter (default p76 post-cap retune)."""
    return max(0, min(95, _get_int("RANK_FILTER_SHADOW_MIN_PERCENTILE_RANK_V2", 76, skill_dir)))


def get_rank_filter_v2_mode(skill_dir: Path | None = None) -> str:
    """Rank-v2 percentile trim mode (OFF|SHADOW|LIVE).

    Default promoted to ``live`` at p75 (ledger seq 15), retuned to p76
    under pts_52w≤37 (ledger seq after 2026-07-22). Live drops below the
    percentile threshold; keep ``SCAN_LIVE_SORT_KEY=signal_score`` until a
    separate sort-key promotion.
    """
    return _get_mode("RANK_FILTER_V2_MODE", PLUGIN_MODE_VALUES, "live", skill_dir)


def get_prob_rank_mode(skill_dir: Path | None = None) -> str:
    """Probabilistic ranker mode (OFF|SHADOW|LIVE).

    Default ``off``. Shadow attaches ``prob_rank`` scores without changing
    selection. Live keeps top ``PROB_RANK_TOP_N`` by expected_return_40d.
    Requires a trained model under ``research_store/models/`` or
    ``PROB_RANK_MODEL_DIR``.
    """
    return _get_mode("PROB_RANK_MODE", PLUGIN_MODE_VALUES, "off", skill_dir)


def get_prob_rank_model_dir(skill_dir: Path | None = None) -> str:
    """Optional path to a trained model directory (artifact.json + model.txt)."""
    env = _load_env(skill_dir)
    return _env_value("PROB_RANK_MODEL_DIR", env).strip()


def get_prob_rank_top_n(skill_dir: Path | None = None) -> int:
    """Top-N cross-sectional keep count when PROB_RANK_MODE=live."""
    return max(1, min(50, _get_int("PROB_RANK_TOP_N", 5, skill_dir)))


def get_prob_rank_include_shap(skill_dir: Path | None = None) -> bool:
    """Compute local SHAP contributors on each scored signal (slower)."""
    return _get_bool("PROB_RANK_INCLUDE_SHAP", False, skill_dir)


def get_prob_rank_sizing_mode(skill_dir: Path | None = None) -> str:
    """Portfolio sizing for prob-rank live path: equal | edge_vol."""
    env = _load_env(skill_dir)
    raw = _env_value("PROB_RANK_SIZING_MODE", env).strip().lower()
    if raw in {"equal", "edge_vol", "dynamic", "edge", "off"}:
        return "equal" if raw == "off" else ("edge_vol" if raw in {"dynamic", "edge"} else raw)
    return "equal"


def get_prob_rank_max_position(skill_dir: Path | None = None) -> float:
    """Max fraction of day book per name under edge_vol sizing."""
    return max(0.05, min(1.0, _get_float("PROB_RANK_MAX_POSITION", 0.25, skill_dir)))


def get_prob_rank_max_sector(skill_dir: Path | None = None) -> float:
    """Max fraction of day book per sector under edge_vol sizing."""
    return max(0.10, min(1.0, _get_float("PROB_RANK_MAX_SECTOR", 0.40, skill_dir)))


def get_prob_rank_kelly_cap(skill_dir: Path | None = None) -> float:
    """Kelly-style cap added onto equal-weight baseline for size multipliers."""
    return max(0.05, min(0.50, _get_float("PROB_RANK_KELLY_CAP", 0.25, skill_dir)))


def get_rank_filter_shadow_min_percentile_signal(skill_dir: Path | None = None) -> int:
    """Min score percentile for signal_score shadow filter (default p70)."""
    return max(0, min(95, _get_int("RANK_FILTER_SHADOW_MIN_PERCENTILE_SIGNAL", 70, skill_dir)))


def get_stage2_shadow_52w_pct(skill_dir: Path | None = None) -> float:
    """Stricter 52w proximity for Stage 2 shadow tighten (live default 0.85)."""
    val = _get_float("STAGE2_SHADOW_52W_PCT", 0.88, skill_dir)
    return max(0.5, min(0.99, val))


def get_stage2_shadow_sma_upward_days(skill_dir: Path | None = None) -> int:
    """Stricter 200-SMA upward window for Stage 2 shadow tighten (live default 20)."""
    return max(1, _get_int("STAGE2_SHADOW_SMA_UPWARD_DAYS", 25, skill_dir))


def get_pts_52w_cap_mode(skill_dir: Path | None = None) -> str:
    """Bare Stage A: cap deepest 52w-high chase (pts_52w).

    off — disable. shadow — annotate/count only (never blocks).
    live — drop Stage A / backtest entries above the cap.

    Default ``live`` after multi-era ``stage2_pts52w_cap37`` (2026-07-18)
    cleared PF mean ≥ 1.20 and worst-era ≥ 1.00. See ``SIGNAL_QUALITY_ROLLOUT.md``.
    """
    return _get_mode("PTS_52W_CAP_MODE", PLUGIN_MODE_VALUES, "live", skill_dir)


def get_pts_52w_cap_max(skill_dir: Path | None = None) -> float:
    """Max pts_52w allowed at entry (default 37.0; component scale 0–40)."""
    val = _get_float("PTS_52W_CAP_MAX", 37.0, skill_dir)
    return max(0.0, min(40.0, val))


def get_early_stop_gate_mode(skill_dir: Path | None = None) -> str:
    """Track A3: pre-entry early-stop risk gate (pts_52w + breakout buffer).

    off — disable. shadow — annotate/count only (never blocks).
    live — drop Stage A / backtest entries that fail the rule.

    Default ``shadow`` until offline CF clears incremental lift gates.
    """
    return _get_mode("EARLY_STOP_GATE_MODE", PLUGIN_MODE_VALUES, "shadow", skill_dir)


def get_early_stop_gate_pts_52w_max(skill_dir: Path | None = None) -> float:
    """Max pts_52w for early-stop gate (default 35.0 from preentry CF)."""
    val = _get_float("EARLY_STOP_GATE_PTS_52W_MAX", 35.0, skill_dir)
    return max(0.0, min(40.0, val))


def get_early_stop_gate_breakout_buffer_min(skill_dir: Path | None = None) -> float:
    """Min breakout buffer pct for early-stop gate (default 1.0%)."""
    val = _get_float("EARLY_STOP_GATE_BREAKOUT_BUFFER_MIN", 0.01, skill_dir)
    return max(0.0, min(0.25, val))


def get_backtest_entry_family(skill_dir: Path | None = None) -> str:
    """Backtest / multi-era entry generator family (Track B).

    - stage2: legacy Stage 2 hard entry (default)
    - pullback: peer pullback-to-SMA50 generator (no Stage 2 required)
    - pead_primary: peer PEAD/earnings-beat generator (no Stage 2 required)
    """
    env = _load_env(skill_dir)
    raw = _env_value("BACKTEST_ENTRY_FAMILY", env).strip().lower()
    if raw in {"stage2", "pullback", "pead_primary"}:
        return raw
    return "stage2"


def get_entry_timing_shadow_mode(skill_dir: Path | None = None) -> str:
    """P0 entry-timing gates (SMA50 cushion, breakout buffer, extension cap).

    off — disable. shadow — annotate/count only (never blocks).
    live — drop Stage A candidates that fail entry-timing rules.

    Default promoted to ``live`` with breakout-buffer-only 1% profile after
    offline stack PF cleared gates (``exit_grace_breakout_buffer_0.010``) and
    live shadow filter rates stayed in band. See ``SIGNAL_QUALITY_ROLLOUT.md``.
    """
    return _get_mode("ENTRY_TIMING_SHADOW_MODE", PLUGIN_MODE_VALUES, "live", skill_dir)


def get_entry_shadow_min_pct_above_sma50(skill_dir: Path | None = None) -> float:
    """Min % above 50 SMA at entry; shadow rejects hugging support (default 1%)."""
    val = _get_float("ENTRY_SHADOW_MIN_PCT_ABOVE_SMA50", 0.01, skill_dir)
    return max(0.0, min(0.25, val))


def get_entry_shadow_max_pct_above_sma50(skill_dir: Path | None = None) -> float:
    """Max % above 50 SMA at entry; shadow flags extended/chase entries (default 25%).

    Full replay on control_legacy_aug showed 12% was too tight (222 false positives,
    overlap PF -0.18). Keep high until a narrower cap shows early-stop reduction
    with >=50% retention in ``analyze_entry_timing_shadow_counterfactual.py``.
    """
    val = _get_float("ENTRY_SHADOW_MAX_PCT_ABOVE_SMA50", 0.25, skill_dir)
    return max(0.02, min(0.50, val))


def get_entry_shadow_min_breakout_buffer_pct(skill_dir: Path | None = None) -> float:
    """Min close buffer above prior-bar high for breakout (default 1.0%).

    Offline replay on ``control_legacy_aug`` preferred 1.0% (~50% retention,
    overlap PF +0.32). The prior 0.2% default was too loose for live enforcement.
    """
    val = _get_float("ENTRY_SHADOW_MIN_BREAKOUT_BUFFER_PCT", 0.01, skill_dir)
    return max(0.0, min(0.05, val))


def get_entry_shadow_disable_sma50_filters(skill_dir: Path | None = None) -> bool:
    """When true, entry-timing only evaluates breakout buffer (validated P0 path).

    SMA50 cushion/extension caps hurt overlap PF offline; default on so the
    live profile is ``breakout_buffer_only_0.010``.
    """
    return _get_bool("ENTRY_SHADOW_DISABLE_SMA50_FILTERS", True, skill_dir)


def get_entry_timing_shadow_profile(skill_dir: Path | None = None) -> str:
    """Human-readable active entry-timing shadow profile for diagnostics."""
    if get_entry_timing_shadow_mode(skill_dir) == "off":
        return "off"
    if get_entry_shadow_disable_sma50_filters(skill_dir):
        buf = get_entry_shadow_min_breakout_buffer_pct(skill_dir)
        return f"breakout_buffer_only_{buf:.3f}"
    return "default_sma50_and_buffer"


def get_entry_timing_breakout_buffer_readiness(skill_dir: Path | None = None) -> dict[str, Any]:
    """Profile readiness for breakout-buffer-only path (shadow or live mode)."""
    expected_profile = "breakout_buffer_only_0.010"
    mode = get_entry_timing_shadow_mode(skill_dir)
    profile = get_entry_timing_shadow_profile(skill_dir)
    buffer = get_entry_shadow_min_breakout_buffer_pct(skill_dir)
    disable_sma50 = get_entry_shadow_disable_sma50_filters(skill_dir)

    missing_env: list[str] = []
    if mode not in {"shadow", "live"}:
        missing_env.append("ENTRY_TIMING_SHADOW_MODE=shadow|live")
    if not disable_sma50:
        missing_env.append("ENTRY_SHADOW_DISABLE_SMA50_FILTERS=true")
    if abs(buffer - 0.01) > 1e-9:
        missing_env.append("ENTRY_SHADOW_MIN_BREAKOUT_BUFFER_PCT=0.01")

    ready = not missing_env and profile == expected_profile
    return {
        "ready": ready,
        "mode": mode,
        "profile": profile,
        "expected_profile": expected_profile,
        "missing_env": missing_env,
        "live_enforced": mode == "live",
    }


def get_entry_timing_experiment_readiness(skill_dir: Path | None = None) -> dict[str, Any]:
    """Whether process env matches the offline breakout-buffer-only experiment (shadow)."""
    expected_profile = "breakout_buffer_only_0.010"
    base = get_entry_timing_breakout_buffer_readiness(skill_dir)
    mode = str(base.get("mode") or "")
    missing_env = list(base.get("missing_env") or [])
    if mode == "live":
        missing_env.append("ENTRY_TIMING_SHADOW_MODE=shadow (live active — use breakout_buffer_readiness)")
    elif mode != "shadow":
        missing_env.append("ENTRY_TIMING_SHADOW_MODE=shadow")

    recommended_env = {
        "ENTRY_TIMING_SHADOW_MODE": "shadow",
        "ENTRY_SHADOW_DISABLE_SMA50_FILTERS": "true",
        "ENTRY_SHADOW_MIN_BREAKOUT_BUFFER_PCT": "0.01",
    }
    ready = not missing_env and base.get("profile") == expected_profile
    return {
        "ready": ready,
        "profile": base.get("profile"),
        "expected_profile": expected_profile,
        "missing_env": missing_env,
        "recommended_env": recommended_env,
    }


# VCP: number of consecutive days volume below 50d avg
def get_vcp_days(skill_dir: Path | None = None) -> int:
    return _get_int("VCP_DAYS", 4, skill_dir)


def get_vcp_exclude_breakout_bars(skill_dir: Path | None = None) -> int:
    """Most-recent bars to exclude from the VCP dry-up window.

    The VCP check historically included the breakout/entry bar itself, which
    forces every accepted signal to have below-average breakout-day volume and
    makes any breakout-volume confirmation gate (ratio >= 1.0) unsatisfiable.
    Setting this to BREAKOUT_CONFIRM_BARS measures dry-up strictly *before*
    the breakout. Default 0 preserves legacy behavior.
    """
    return max(0, _get_int("VCP_EXCLUDE_BREAKOUT_BARS", 0, skill_dir))


# Signal ranking: max number of signals to send (0 = no limit)
def get_signal_top_n(skill_dir: Path | None = None) -> int:
    """
    Number of top signals returned after final ranking.

    SIGNAL_TOP_N=0 (or negative) means "return all ranked signals" -- the
    dashboard /api/scan endpoint relies on this to surface every Stage B
    candidate without truncation. Stage A shortlist sizing also honors 0
    via _compute_stage_a_shortlist_limit.

    Implemented as a dedicated parser instead of _get_int because _get_int
    clamps to >=1, which silently broke the documented "0 = no cap" contract.
    """
    env = _load_env(skill_dir)
    raw = _env_value("SIGNAL_TOP_N", env).strip()
    if not raw:
        return 5
    try:
        return max(0, int(float(raw)))
    except (ValueError, TypeError):
        return 5


# Scanner: bounded workers for fast filter stage
def get_scan_stage_a_max_workers(skill_dir: Path | None = None) -> int:
    # Default kept conservative to reduce Schwab 429s during wide watchlists.
    return _get_int("SCAN_STAGE_A_MAX_WORKERS", 4, skill_dir)


# Scanner: bounded workers for heavy enrichment stage
def get_scan_stage_b_max_workers(skill_dir: Path | None = None) -> int:
    # Default 4: Stage B is shortlist-only; moderate parallelism improves latency vs Schwab 429 tradeoffs.
    return _get_int("SCAN_STAGE_B_MAX_WORKERS", 4, skill_dir)


# Scanner: shortlist width relative to top-N final output size
def get_scan_stage_a_shortlist_multiplier(skill_dir: Path | None = None) -> float:
    return _get_float("SCAN_STAGE_A_SHORTLIST_MULTIPLIER", 3.0, skill_dir)


# Scanner: hard cap for Stage A shortlist candidates
def get_scan_stage_a_shortlist_cap(skill_dir: Path | None = None) -> int:
    return _get_int("SCAN_STAGE_A_SHORTLIST_CAP", 40, skill_dir)


# Scanner: ceiling that applies only when SIGNAL_TOP_N <= 0 ("show all").
# Without this, a permissive top_n=0 scan would dispatch Stage B enrichment on
# every Stage A survivor (potentially hundreds of names), which inflates scan
# latency and Schwab API pressure. Default 250 is generous for SP1500-scale
# scans while still bounding worst-case work. Set to 0 to disable the ceiling.
def get_scan_stage_a_nocap_limit(skill_dir: Path | None = None) -> int:
    return _get_int("SCAN_STAGE_A_NOCAP_LIMIT", 250, skill_dir)


# Scanner: per-ticker stage timeout safety bound (seconds)
def get_scan_stage_task_timeout_sec(skill_dir: Path | None = None) -> float:
    return _get_float("SCAN_STAGE_TASK_TIMEOUT_SEC", 120.0, skill_dir)


def get_scan_stage_a_max_stalls(skill_dir: Path | None = None) -> int:
    """Waves that finish no symbols before Stage A checkpoints the rest.

    A wave that completes at least one symbol resets the stall count, so a slow
    but healthy scan continues until the watchlist is covered. Two stalled waves
    means the data source is not making progress; the remainder is checkpointed
    for the next scan instead of being dropped.
    """
    return _get_int("SCAN_STAGE_A_MAX_STALLS", 2, skill_dir)


def get_scan_stage_a_checkpoint_enabled(skill_dir: Path | None = None) -> bool:
    """Persist Stage A decisions so a restarted scan resumes the remainder."""
    return _get_bool("SCAN_STAGE_A_CHECKPOINT", True, skill_dir)


def get_scan_stage_wall_budget_sec(skill_dir: Path | None = None) -> float:
    """Hard wall-clock ceiling for one Stage A wave, or for Stage B.

    A Stage A wave that finishes at least one symbol is followed by another
    wave until the watchlist is covered. This cap only bounds a wave whose
    workers are stuck. Names still pending after the stall limit are
    checkpointed, not counted as Stage 2 passes.
    """
    return _get_float("SCAN_STAGE_WALL_BUDGET_SEC", 1800.0, skill_dir)


# Signal score-stack weights (see signal_scanner._apply_score_stack).
# Defaults preserve the original hardcoded blend; exposing them as config lets
# the weight-feedback review loop propose tuning without code edits.
def get_score_edge_signal_weight(skill_dir: Path | None = None) -> float:
    """Weight of the edge signal term within edge_score (default 1.0 — p_up harmful at 40d)."""
    return _get_float("SCORE_EDGE_SIGNAL_WEIGHT", 1.0, skill_dir)


def get_score_edge_pup_weight(skill_dir: Path | None = None) -> float:
    """Weight of calibrated P(up) within edge_score (default 0.0 at 40d)."""
    return _get_float("SCORE_EDGE_PUP_WEIGHT", 0.0, skill_dir)


def get_score_composite_edge_weight(skill_dir: Path | None = None) -> float:
    """Legacy stack blend: edge weight (default 0.0 when direct components + caps-only)."""
    return _get_float("SCORE_COMPOSITE_EDGE_WEIGHT", 0.0, skill_dir)


def get_score_composite_reliability_weight(skill_dir: Path | None = None) -> float:
    """Legacy stack blend: reliability weight (default 0.0 — caps only)."""
    return _get_float("SCORE_COMPOSITE_RELIABILITY_WEIGHT", 0.0, skill_dir)


def get_score_composite_execution_weight(skill_dir: Path | None = None) -> float:
    """Legacy stack blend: execution weight (default 0.0 — caps only)."""
    return _get_float("SCORE_COMPOSITE_EXECUTION_WEIGHT", 0.0, skill_dir)


def get_score_composite_use_direct_components(skill_dir: Path | None = None) -> bool:
    """Blend pts_volume / trend / signal-minus-52w / pts_mirofish directly into composite (default true)."""
    return _get_bool("SCORE_COMPOSITE_USE_DIRECT_COMPONENTS", True, skill_dir)


def get_score_composite_direct_trend_weight(skill_dir: Path | None = None) -> float:
    """Weight of 200-SMA trend distance in direct composite (40d IC ~ +0.07)."""
    return _get_float("SCORE_COMPOSITE_DIRECT_TREND_WEIGHT", 0.70, skill_dir)


def get_score_composite_direct_volume_weight(skill_dir: Path | None = None) -> float:
    return _get_float("SCORE_COMPOSITE_DIRECT_VOLUME_WEIGHT", 0.20, skill_dir)


def get_score_composite_direct_signal_weight(skill_dir: Path | None = None) -> float:
    return _get_float("SCORE_COMPOSITE_DIRECT_SIGNAL_WEIGHT", 0.05, skill_dir)


def get_score_composite_direct_mirofish_weight(skill_dir: Path | None = None) -> float:
    return _get_float("SCORE_COMPOSITE_DIRECT_MIROFISH_WEIGHT", 0.05, skill_dir)


def get_score_composite_stack_blend_weight(skill_dir: Path | None = None) -> float:
    """Mix edge stack into direct predictive core (default 0.0 — p_up harmful at 40d)."""
    return _get_float("SCORE_COMPOSITE_STACK_BLEND_WEIGHT", 0.0, skill_dir)


def get_score_composite_safety_caps_only(skill_dir: Path | None = None) -> bool:
    """Apply reliability/execution via hard caps only, not blend weights (default true)."""
    return _get_bool("SCORE_COMPOSITE_SAFETY_CAPS_ONLY", True, skill_dir)


def get_score_pts_sma_cap(skill_dir: Path | None = None) -> float:
    """Max points from SMA distance component (default 25)."""
    return _get_float("SCORE_PTS_SMA_CAP", 25.0, skill_dir)


def get_score_pts_sma_multiplier(skill_dir: Path | None = None) -> float:
    """Scale applied to pts_sma before adding to signal_score (default 0.0).

    Offline scoring validation (40d, 1102 candidates) showed pts_sma is harmful:
    removing it improved AUC +0.023; multiplier=0.0 beat 1.0 (0.491 vs 0.467 AUC).
    """
    return _get_float("SCORE_PTS_SMA_MULTIPLIER", 0.0, skill_dir)


def get_score_edge_exclude_52w(skill_dir: Path | None = None) -> bool:
    """Use signal_score minus pts_52w in edge_score (52w harmful at 40d)."""
    return _get_bool("SCORE_EDGE_EXCLUDE_52W", True, skill_dir)


def get_scan_live_sort_key(skill_dir: Path | None = None) -> str:
    """Primary candidate sort key until composite beats signal on realized trades."""
    env = _load_env(skill_dir)
    raw = _env_value("SCAN_LIVE_SORT_KEY", env).strip().lower()
    if raw in {"signal_score", "composite_score", "rank_score", "rank_score_v2"}:
        return raw
    return "signal_score"


def get_rank_score_v2_mode(skill_dir: Path | None = None) -> str:
    """Component-weighted rank-v2 computation mode; sorting is configured separately."""
    return _get_mode("RANK_SCORE_V2_MODE", PLUGIN_MODE_VALUES, "shadow", skill_dir)


def get_historical_rank_mode(skill_dir: Path | None = None) -> str:
    """Fitted historical blend attach: off | shadow | live. Live is not approved."""
    return _get_mode("HISTORICAL_RANK_MODE", PLUGIN_MODE_VALUES, "shadow", skill_dir)


def get_historical_rank_artifact(skill_dir: Path | None = None) -> str:
    """Optional override path for ``research_store/models/historical_rank_blend.json``."""
    env = _load_env(skill_dir)
    return _env_value("HISTORICAL_RANK_ARTIFACT", env).strip()


def get_analog_overlay_mode(skill_dir: Path | None = None) -> str:
    """Winner-neighborhood analog overlay: off | shadow | live. Live is not approved."""
    return _get_mode("ANALOG_OVERLAY_MODE", PLUGIN_MODE_VALUES, "shadow", skill_dir)


def get_analog_overlay_artifact(skill_dir: Path | None = None) -> str:
    """Optional override path for ``research_store/models/analog_library.json``."""
    env = _load_env(skill_dir)
    return _env_value("ANALOG_OVERLAY_ARTIFACT", env).strip()


def get_analog_overlay_k(skill_dir: Path | None = None) -> int:
    """Neighbor count for analog k-NN (Euclidean in normalized space)."""
    return _get_int("ANALOG_OVERLAY_K", 40, skill_dir)


def get_analog_overlay_min_n(skill_dir: Path | None = None) -> int:
    """Minimum neighbors before analog_p_win / PF shrink toward the era prior."""
    return _get_int("ANALOG_OVERLAY_MIN_N", 20, skill_dir)


def get_confidence_rank_mode(skill_dir: Path | None = None) -> str:
    """Setup-confidence survival readout: off | shadow | live. Live is not approved."""
    return _get_mode("CONFIDENCE_RANK_MODE", PLUGIN_MODE_VALUES, "shadow", skill_dir)


def get_extension_sort_mode(skill_dir: Path | None = None) -> str:
    """Less-extended shadow sort (inverted pts_52w): off | shadow | live.

    Default ``shadow``. Live is refused in ``core.extension_sort`` — it does
    not change ``SCAN_LIVE_SORT_KEY``.
    """
    return _get_mode("EXTENSION_SORT_MODE", PLUGIN_MODE_VALUES, "shadow", skill_dir)


def get_pead_extension_drop_mode(skill_dir: Path | None = None) -> str:
    """PEAD-only extended-name mark: off | shadow | live.

    Default ``shadow``. Live is refused in ``core.pead_extension_drop``.
    Stage 2 rows are never stamped.
    """
    return _get_mode("PEAD_EXTENSION_DROP_MODE", PLUGIN_MODE_VALUES, "shadow", skill_dir)


def get_pead_extension_drop_min(skill_dir: Path | None = None) -> float:
    """Drop mark when close is at least this far above the 200-day average.

    Default 0.2028 is the 80th percentile on ``pead_primary_aug_fixed_full_c40``.
    """
    val = _get_float("PEAD_EXTENSION_DROP_MIN_SMA200_PCT", 0.2028, skill_dir)
    return max(0.0, min(2.0, val))


def get_confidence_rank_artifact(skill_dir: Path | None = None) -> str:
    """Optional override path for ``research_store/models/scan_confidence.json``."""
    env = _load_env(skill_dir)
    return _env_value("CONFIDENCE_RANK_ARTIFACT", env).strip()


def get_rank_v2_signal_weight(skill_dir: Path | None = None) -> float:
    return _get_float("RANK_V2_SIGNAL_WEIGHT", 0.35, skill_dir)


def get_rank_v2_volume_weight(skill_dir: Path | None = None) -> float:
    return _get_float("RANK_V2_VOLUME_WEIGHT", 0.50, skill_dir)


def get_rank_v2_mirofish_weight(skill_dir: Path | None = None) -> float:
    return _get_float("RANK_V2_MIROFISH_WEIGHT", 0.15, skill_dir)


def get_rank_v2_exclude_52w(skill_dir: Path | None = None) -> bool:
    """Exclude pts_52w from the signal term in rank v2 (harmful at 40d)."""
    return _get_bool("RANK_V2_EXCLUDE_52W", True, skill_dir)


def get_scan_vcp_gate_mode(skill_dir: Path | None = None) -> str:
    """
    VCP gate mode for Stage A:
    - hard: reject candidate when VCP fails
    - shadow: keep candidate, apply score penalty, track would-filter diagnostics
    """
    env = _load_env(skill_dir)
    raw = _env_value("SCAN_VCP_GATE_MODE", env).strip().lower()
    if raw in {"hard", "shadow"}:
        return raw
    return "shadow"


def get_scan_sector_gate_mode(skill_dir: Path | None = None) -> str:
    """
    Sector gate mode for Stage A:
    - hard: reject candidate when sector is unresolved or underperforming
    - shadow: keep candidate, apply score penalty, track would-filter diagnostics
    """
    env = _load_env(skill_dir)
    raw = _env_value("SCAN_SECTOR_GATE_MODE", env).strip().lower()
    if raw in {"hard", "shadow"}:
        return raw
    return "shadow"


def get_scan_vcp_penalty_points(skill_dir: Path | None = None) -> float:
    """Stage A score penalty applied when VCP fails in shadow mode."""
    return _get_float("SCAN_VCP_PENALTY_POINTS", 14.0, skill_dir)


def get_scan_sector_penalty_points(skill_dir: Path | None = None) -> float:
    """Stage A score penalty applied when sector underperforms in shadow mode."""
    return _get_float("SCAN_SECTOR_PENALTY_POINTS", 10.0, skill_dir)


def get_scan_sector_unresolved_penalty_points(skill_dir: Path | None = None) -> float:
    """Stage A score penalty applied when sector mapping is unavailable in shadow mode."""
    return _get_float("SCAN_SECTOR_UNRESOLVED_PENALTY_POINTS", 6.0, skill_dir)


# Scanner: allow scans to run even when SPY is below 200 SMA.
def get_scan_allow_bear_regime(skill_dir: Path | None = None) -> bool:
    return _get_bool("SCAN_ALLOW_BEAR_REGIME", False, skill_dir)


# Breakout confirmation: require intraday price above prior high (minutes from midnight, 570=9:30)
def get_breakout_confirm_min_time(skill_dir: Path | None = None) -> int:
    return _get_int("BREAKOUT_CONFIRM_MIN_TIME", 570, skill_dir)


# Breakout confirmation: enable/disable
def get_breakout_confirm_enabled(skill_dir: Path | None = None) -> bool:
    return _get_bool("BREAKOUT_CONFIRM_ENABLED", True, skill_dir)


def get_breakout_confirm_bars(skill_dir: Path | None = None) -> int:
    """Consecutive bars of breakout follow-through required to confirm.

    1 preserves the legacy single-bar check (latest close/price above the
    prior bar's high). 2 additionally requires the previous daily close to
    have held above its own prior high. Clamped to 1..3.
    """
    val = _get_int("BREAKOUT_CONFIRM_BARS", 1, skill_dir)
    return max(1, min(3, val))


# Data: prefer Schwab, only use yfinance on explicit failure
def get_prefer_schwab_data(skill_dir: Path | None = None) -> bool:
    return _get_bool("PREFER_SCHWAB_DATA", True, skill_dir)


# Volatility sizing: base USD when ATR_mult=1.0
def get_volatility_base_usd(skill_dir: Path | None = None) -> int:
    return _get_int("VOLATILITY_BASE_USD", 5000, skill_dir)


# Volatility sizing: target ATR multiple (2.0 = size for 2 ATR stop)
def get_volatility_atr_mult(skill_dir: Path | None = None) -> float:
    return _get_float("VOLATILITY_ATR_MULT", 2.0, skill_dir)


# Volatility sizing: enable (false = use fixed POSITION_SIZE_USD)
def get_volatility_sizing_enabled(skill_dir: Path | None = None) -> bool:
    return _get_bool("VOLATILITY_SIZING_ENABLED", False, skill_dir)


# Position sizing mode (forward-looking knob)
#
# ``fixed``        — current default; ``POSITION_SIZE_USD`` * conviction multiplier.
# ``vol_target``   — size each entry to a target portfolio volatility contribution
#                    (uses ATR / realised vol; not yet wired into ``execution.py``
#                    end-to-end, but exposed so backtest variants can opt in).
# ``kelly_capped`` — fractional Kelly using advisory model edge & realised vol,
#                    clamped to ``KELLY_MAX_FRACTION``. Also forward-looking.
#
# The runtime continues to honour ``VOLATILITY_SIZING_ENABLED`` until each new
# mode is fully validated. This getter exists so callers (including the new
# advisory model and the planned backtest parity layer) can branch on intent.
def get_position_size_mode(skill_dir: Path | None = None) -> str:
    env = _load_env(skill_dir)
    raw = _env_value("POSITION_SIZE_MODE", env).strip().lower()
    if raw in ("fixed", "vol_target", "kelly_capped"):
        return raw
    if raw:
        # Unknown override: log via stderr is intentionally avoided here
        # (config.py is import-time critical). Return safe default.
        return "fixed"
    # Backwards-compat: if vol sizing was already on, treat as vol_target intent.
    return "vol_target" if get_volatility_sizing_enabled(skill_dir) else "fixed"


def get_kelly_max_fraction(skill_dir: Path | None = None) -> float:
    """Cap on fractional-Kelly position size (default 0.25 = quarter-Kelly)."""
    return _get_float("KELLY_MAX_FRACTION", 0.25, skill_dir)


def get_vol_target_annualized(skill_dir: Path | None = None) -> float:
    """Per-position annualised vol target for ``vol_target`` sizing mode."""
    return _get_float("VOL_TARGET_ANNUALIZED", 0.20, skill_dir)


# ---------------------------------------------------------------------------
# Backtest portfolio simulator
# ---------------------------------------------------------------------------
# Replays per-trade returns through a shared equity book with a hard
# concurrency cap and risk-based (or fixed %) sizing. Replaces the legacy
# (1+r).cumprod() aggregator that treated every trade as a sequential
# 100%-of-equity roll and produced fictional -95% to -99% drawdowns.
def get_backtest_portfolio_enabled(skill_dir: Path | None = None) -> bool:
    """Master switch for the portfolio-level equity simulator (default on)."""
    return _get_bool("BACKTEST_PORTFOLIO_ENABLED", True, skill_dir)


def get_backtest_portfolio_starting_equity(skill_dir: Path | None = None) -> float:
    """Notional starting capital for the portfolio simulator."""
    return _get_float("BACKTEST_PORTFOLIO_STARTING_EQUITY", 100_000.0, skill_dir)


def get_backtest_portfolio_max_positions(skill_dir: Path | None = None) -> int:
    """Hard cap on simultaneous open positions in the portfolio simulator."""
    return max(1, _get_int("BACKTEST_PORTFOLIO_MAX_POSITIONS", 10, skill_dir))


def get_portfolio_analytics_lookback_days(skill_dir: Path | None = None) -> int:
    """Daily-history lookback for live portfolio risk analytics."""
    return max(20, _get_int("PORTFOLIO_ANALYTICS_LOOKBACK_DAYS", 60, skill_dir))


def get_portfolio_analytics_enabled(skill_dir: Path | None = None) -> bool:
    """Attach portfolio analytics to cockpit payloads when explicitly enabled."""
    return _get_bool("PORTFOLIO_ANALYTICS_ENABLED", False, skill_dir)


def get_portfolio_analytics_benchmark(skill_dir: Path | None = None) -> str:
    """Benchmark ticker used for portfolio beta and relative risk."""
    env = _load_env(skill_dir)
    raw = _env_value("PORTFOLIO_ANALYTICS_BENCHMARK", env).strip().upper()
    return raw or "SPY"


def get_portfolio_analytics_risk_free_rate(skill_dir: Path | None = None) -> float:
    """Annual risk-free rate as a decimal, used for Sharpe/Sortino."""
    return max(0.0, _get_float("PORTFOLIO_ANALYTICS_RISK_FREE_RATE", 0.0, skill_dir))


def get_portfolio_equity_snapshot_enabled(skill_dir: Path | None = None) -> bool:
    """Enable daily portfolio equity snapshots for live drawdown curves."""
    return _get_bool("PORTFOLIO_EQUITY_SNAPSHOT_ENABLED", True, skill_dir)


def get_book_ytd_export_enabled(skill_dir: Path | None = None) -> bool:
    """Enable post-close Book YTD Excel refresh in the local bot loop."""
    return _get_bool("BOOK_YTD_EXPORT_ENABLED", True, skill_dir)


def get_book_ytd_export_hhmm(skill_dir: Path | None = None) -> tuple[int, int]:
    """Weekday ET time for Book YTD Excel EOD refresh (default 16:30)."""
    env = _load_env(skill_dir)
    raw = _env_value("BOOK_YTD_EXPORT_HHMM", env).strip()
    if not raw:
        return (16, 30)
    text = raw.replace(".", ":")
    try:
        if ":" in text:
            hh_s, mm_s = text.split(":", 1)
            hour = int(hh_s)
            minute = int(mm_s)
        elif len(text) == 4 and text.isdigit():
            hour = int(text[:2])
            minute = int(text[2:])
        else:
            return (16, 30)
    except (TypeError, ValueError):
        return (16, 30)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return (16, 30)
    return (hour, minute)


def get_book_ytd_export_path(
    skill_dir: Path | None = None,
    *,
    tax_year: int | None = None,
) -> Path:
    """Canonical Book YTD workbook path (supports ``{year}`` in env path)."""
    from datetime import datetime, timezone

    year = int(tax_year or datetime.now(timezone.utc).year)
    env = _load_env(skill_dir)
    raw = _env_value("BOOK_YTD_EXPORT_PATH", env).strip()
    root = skill_dir or SKILL_DIR
    if not raw:
        return root / "exports" / f"book_ytd_{year}.xlsx"
    expanded = raw.replace("{year}", str(year))
    path = Path(expanded)
    if not path.is_absolute():
        path = root / path
    if path.suffix.lower() != ".xlsx":
        path = path / f"book_ytd_{year}.xlsx"
    return path


def get_risk_limit_single_name_pct(skill_dir: Path | None = None) -> float:
    """Single-name weight (% of equity) display limit for dashboard breaches."""
    val = _get_float("RISK_LIMIT_SINGLE_NAME_PCT", 10.0, skill_dir)
    return max(1.0, min(100.0, val))


def get_risk_limit_sector_pct(skill_dir: Path | None = None) -> float:
    """Sector weight (% of equity) display limit for dashboard breaches."""
    val = _get_float("RISK_LIMIT_SECTOR_PCT", 35.0, skill_dir)
    return max(1.0, min(100.0, val))


def get_risk_limit_country_pct(skill_dir: Path | None = None) -> float:
    """Single-country weight (% of equity) display limit for dashboard breaches."""
    val = _get_float("RISK_LIMIT_COUNTRY_PCT", 10.0, skill_dir)
    return max(1.0, min(100.0, val))


def get_risk_fx_shock_em_pct(skill_dir: Path | None = None) -> float:
    """Uniform broad-EM FX shock (%) applied to non-USD exposure in FX stress."""
    val = _get_float("RISK_FX_SHOCK_EM", -15.0, skill_dir)
    return max(-90.0, min(0.0, val))


def get_risk_fx_shock_by_country(skill_dir: Path | None = None) -> dict[str, float]:
    """Per-country FX shock map (%), e.g. ``KZ:-20,CN:-10,CA:-5,KR:-10``."""
    env = _load_env(skill_dir)
    raw = _env_value("RISK_FX_SHOCK_BY_COUNTRY", env).strip()
    defaults = {"KZ": -20.0, "CN": -10.0, "CA": -5.0, "KR": -10.0}
    if not raw:
        return defaults
    out: dict[str, float] = {}
    for pair in raw.split(","):
        if ":" not in pair:
            continue
        code, _, shock = pair.partition(":")
        try:
            out[code.strip().upper()] = max(-90.0, min(0.0, float(shock)))
        except ValueError:
            continue
    return out or defaults


def get_risk_mc_simulations(skill_dir: Path | None = None) -> int:
    """Monte Carlo simulation paths for parametric portfolio VaR."""
    return max(500, min(50_000, _get_int("RISK_MC_SIMULATIONS", 5000, skill_dir)))


def get_backtest_position_size_pct(skill_dir: Path | None = None) -> float:
    """Fallback fixed allocation (fraction of current equity) per entry when
    risk-based sizing cannot be computed (e.g. missing stop distance)."""
    return max(0.001, _get_float("BACKTEST_POSITION_SIZE_PCT", 0.05, skill_dir))


def get_backtest_risk_per_trade_pct(skill_dir: Path | None = None) -> float:
    """Fraction of current equity risked per trade when stop distance is
    available. Default 0.0075 = 0.75% Minervini/O'Neil convention. Set to
    0 to force fixed-% sizing only."""
    return max(0.0, _get_float("BACKTEST_RISK_PER_TRADE_PCT", 0.0075, skill_dir))


def get_backtest_adaptive_guardrails_enabled(skill_dir: Path | None = None) -> bool:
    """Enable data-driven adaptive sizing/filtering guardrails in backtest."""
    return _get_bool("BACKTEST_ADAPTIVE_GUARDRAILS_ENABLED", False, skill_dir)


def get_backtest_adaptive_guardrail_policy_path(skill_dir: Path | None = None) -> str:
    """Relative/absolute JSON policy file path used by adaptive guardrails."""
    env = _load_env(skill_dir)
    raw = _env_value("BACKTEST_ADAPTIVE_GUARDRAIL_POLICY_PATH", env).strip()
    return raw or "backtest_guardrail_policy.json"


def get_backtest_hold_days(skill_dir: Path | None = None) -> int:
    """Max holding window (trading bars) before time exit in portfolio backtest."""
    return max(1, _get_int("BACKTEST_HOLD_DAYS", 40, skill_dir))


def get_backtest_min_hold_days_before_trail(skill_dir: Path | None = None) -> int:
    """Grace period before trailing-stop ratchet activates (entry fixed stop only)."""
    return max(0, _get_int("BACKTEST_MIN_HOLD_DAYS_BEFORE_TRAIL", 15, skill_dir))


def get_backtest_min_hold_defer_soft_exits(skill_dir: Path | None = None) -> bool:
    """Defer SMA50 / VCP invalidation exits until the trail grace period elapses."""
    return _get_bool("BACKTEST_MIN_HOLD_DEFER_SOFT_EXITS", True, skill_dir)


def get_alert_min_conviction(skill_dir: Path | None = None) -> int:
    """Minimum conviction to send any alert (below = suppressed)."""
    return _get_int("ALERT_MIN_CONVICTION", 20, skill_dir)


def get_alert_ping_conviction(skill_dir: Path | None = None) -> int:
    """Conviction threshold above which the user gets a @ping."""
    return _get_int("ALERT_PING_CONVICTION", 50, skill_dir)


def get_alert_ping_score(skill_dir: Path | None = None) -> int:
    """Setup score threshold above which the user gets a @ping."""
    return _get_int("ALERT_PING_SCORE", 60, skill_dir)


ENTRY_PROTECTION_MODES = {"bracket", "separate"}


def get_entry_protection_mode(skill_dir: Path | None = None) -> str:
    """How a BUY carries its protective stop.

    ``bracket`` sends one TRIGGER order whose child is the stop, so Schwab
    holds the stop after the entry fills. ``separate`` is the previous
    two-request path, kept for rollback.
    """
    return _get_mode("ENTRY_PROTECTION_MODE", ENTRY_PROTECTION_MODES, "bracket", skill_dir)


def get_stop_order_duration(skill_dir: Path | None = None) -> str:
    """
    Stop duration for protective trailing stop orders.
    Allowed values are normalized to DAY or GOOD_TILL_CANCEL.
    """
    env = _load_env(skill_dir)
    raw = _env_value("STOP_ORDER_DURATION", env).strip().upper()
    if raw in ("DAY", "GOOD_TILL_CANCEL"):
        return raw
    return "GOOD_TILL_CANCEL"


def get_adaptive_stop_enabled(skill_dir: Path | None = None) -> bool:
    """Enable adaptive stop sizing using ATR + trend regime."""
    return _get_bool("ADAPTIVE_STOP_ENABLED", True, skill_dir)


def get_adaptive_stop_base_pct(skill_dir: Path | None = None) -> float:
    """Base stop percentage fallback when adaptive inputs are unavailable."""
    return _get_float("ADAPTIVE_STOP_BASE_PCT", 0.07, skill_dir)


def get_adaptive_stop_min_pct(skill_dir: Path | None = None) -> float:
    """Minimum adaptive stop percent clamp."""
    return _get_float("ADAPTIVE_STOP_MIN_PCT", 0.05, skill_dir)


def get_adaptive_stop_max_pct(skill_dir: Path | None = None) -> float:
    """Maximum adaptive stop percent clamp."""
    return _get_float("ADAPTIVE_STOP_MAX_PCT", 0.12, skill_dir)


def get_adaptive_stop_atr_mult(skill_dir: Path | None = None) -> float:
    """ATR multiplier for stop distance. 2.5x ATR gives each stock room proportional to its volatility."""
    return _get_float("ADAPTIVE_STOP_ATR_MULT", 2.5, skill_dir)


def get_adaptive_stop_trend_lookback(skill_dir: Path | None = None) -> int:
    """Lookback window for trend regime adjustment."""
    return _get_int("ADAPTIVE_STOP_TREND_LOOKBACK", 20, skill_dir)


def get_execution_shadow_mode(skill_dir: Path | None = None) -> bool:
    """
    If true, execution computes decisions but does not submit live broker orders.
    PAPER_TRADING_ENABLED=1 is an alias for operators who prefer that name.
    """
    if _get_bool("PAPER_TRADING_ENABLED", False, skill_dir):
        return True
    return _get_bool("EXECUTION_SHADOW_MODE", False, skill_dir)


def get_live_trading_kill_switch(skill_dir: Path | None = None) -> bool:
    """Platform-wide halt when LIVE_TRADING_KILL_SWITCH=1 (injected into tenant .env on SaaS)."""
    return _get_bool("LIVE_TRADING_KILL_SWITCH", False, skill_dir)


def user_trading_halt_state_path(skill_dir: Path | None = None) -> Path:
    """Local pause file written by the Discord operator desk."""
    return (skill_dir or SKILL_DIR) / _USER_TRADING_HALT_STATE


def env_user_trading_halted(skill_dir: Path | None = None) -> bool:
    """True when USER_TRADING_HALTED is set. SaaS materializes this from the user row."""
    return _get_bool("USER_TRADING_HALTED", False, skill_dir)


def _read_user_trading_halt_file(skill_dir: Path | None = None) -> bool:
    """True when the local pause file says new buys are paused.

    A missing file means not paused. A file that exists but cannot be read as
    ``{"halted": true|false}`` fails closed and stays paused.
    """
    path = user_trading_halt_state_path(skill_dir)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    except OSError:
        LOG.warning("User trading halt file unreadable, failing closed: %s", path)
        return True
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        LOG.warning("User trading halt file is not valid JSON, failing closed: %s", path)
        return True
    if not isinstance(payload, dict):
        LOG.warning("User trading halt file is not an object, failing closed: %s", path)
        return True
    flag = payload.get("halted")
    if not isinstance(flag, bool):
        LOG.warning("User trading halt file is missing a boolean halted flag, failing closed: %s", path)
        return True
    return flag


def set_user_trading_halt_state(halted: bool, skill_dir: Path | None = None) -> None:
    """Persist the local new-buy pause. Does not change USER_TRADING_HALTED."""
    path = user_trading_halt_state_path(skill_dir)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"halted": bool(halted)}), encoding="utf-8")
    tmp.replace(path)


def get_user_trading_halted(skill_dir: Path | None = None) -> bool:
    """Pause new buys when the env flag or the local halt file says so.

    Either source is enough. Clearing the file does not override USER_TRADING_HALTED.
    """
    if env_user_trading_halted(skill_dir):
        return True
    return _read_user_trading_halt_file(skill_dir)


def get_live_trading_kill_switch_blocks_exits(skill_dir: Path | None = None) -> bool:
    """
    When true with kill switch / user halt, SELL and reducing orders are blocked too.
    Default false: exits still allowed.
    """
    return _get_bool("LIVE_TRADING_KILL_SWITCH_BLOCKS_EXITS", False, skill_dir)


def get_max_sector_account_fraction(skill_dir: Path | None = None) -> float:
    """
    Max fraction of total account equity allowed in one sector ETF bucket (0..1).
    0 disables the check. Uses yfinance-backed sector mapping (cached).
    """
    v = _get_float("MAX_SECTOR_ACCOUNT_FRACTION", 0.0, skill_dir)
    return max(0.0, min(1.0, v))


def get_exec_quality_mode(skill_dir: Path | None = None) -> str:
    """Execution quality plugin mode (OFF|SHADOW|LIVE).

    Default promoted to ``live`` (2026-Q2 promotion) — see
    ``docs/RELEASE_NOTES_PLUGIN_PROMOTIONS.md`` and
    ``scripts/promotion_ledger.jsonl``. Invalid values still fall back to
    the operational default (``live``) rather than silently disabling
    the gate; explicit ``EXEC_QUALITY_MODE=off`` is required to opt out.
    """
    return _get_mode("EXEC_QUALITY_MODE", PLUGIN_MODE_VALUES, "live", skill_dir)


def get_exit_manager_mode(skill_dir: Path | None = None) -> str:
    """Exit manager plugin mode (OFF|SHADOW|LIVE).

    Default promoted to ``live`` after the exit-grace shadow run and explicit
    operator approval. The promoted stack retains the validated 15/40-day hold
    settings and live 1% breakout-buffer entry timing.
    """
    return _get_mode("EXIT_MANAGER_MODE", PLUGIN_MODE_VALUES, "live", skill_dir)


def get_event_risk_mode(skill_dir: Path | None = None) -> str:
    """Event-risk plugin mode (OFF|SHADOW|LIVE).

    Default promoted to ``live`` (2026-Q2 promotion) — see
    ``docs/RELEASE_NOTES_PLUGIN_PROMOTIONS.md`` and
    ``scripts/promotion_ledger.jsonl``. Invalid values still fall back to
    the operational default (``live``); explicit ``EVENT_RISK_MODE=off``
    is required to opt out.
    """
    return _get_mode("EVENT_RISK_MODE", PLUGIN_MODE_VALUES, "live", skill_dir)


def get_correlation_guard_mode(skill_dir: Path | None = None) -> str:
    """Correlation guard plugin mode (OFF|SHADOW|LIVE)."""
    return _get_mode("CORRELATION_GUARD_MODE", PLUGIN_MODE_VALUES, "off", skill_dir)


def get_confluence_gate_mode(skill_dir: Path | None = None) -> str:
    """Confluence gate plugin mode (OFF|SHADOW|LIVE).

    Requires at least CONFLUENCE_REQUIRE_COUNT independent confirmations
    (PEAD-positive or advisory-high) on top of the Stage 2 + VCP base setup.
    SHADOW annotates and counts would-blocks without dropping signals;
    LIVE drops unconfirmed signals from the scan results.

    Default ``shadow``: multi-era signal-gate sweep regressed PF when live.
    """
    return _get_mode("CONFLUENCE_GATE_MODE", PLUGIN_MODE_VALUES, "shadow", skill_dir)


def get_confluence_advisory_min_pup(skill_dir: Path | None = None) -> float:
    """Minimum advisory p_up_10d that counts as an advisory-high confirmation."""
    return _get_float("CONFLUENCE_ADVISORY_MIN_PUP", 0.60, skill_dir)


def get_confluence_require_count(skill_dir: Path | None = None) -> int:
    """Independent confirmations required by the confluence gate (min 1)."""
    return max(1, _get_int("CONFLUENCE_REQUIRE_COUNT", 1, skill_dir))


def get_regime_v2_mode(skill_dir: Path | None = None) -> str:
    """Regime v2 plugin mode (OFF|SHADOW|LIVE)."""
    return _get_mode("REGIME_V2_MODE", PLUGIN_MODE_VALUES, "off", skill_dir)


def get_strategy_pullback_mode(skill_dir: Path | None = None) -> str:
    """Pullback strategy plugin mode (OFF|SHADOW|LIVE)."""
    return _get_mode("STRATEGY_PULLBACK_MODE", PLUGIN_MODE_VALUES, "shadow", skill_dir)


def get_strategy_pead_primary_mode(skill_dir: Path | None = None) -> str:
    """PEAD-primary dual-admit mode (OFF|SHADOW|LIVE).

    Default ``off`` — Stage A stays Stage-2-only until operators opt into
    shadow dual-admit. ``live`` is not executable yet; use
    :func:`get_strategy_pead_primary_effective_mode` in the scanner.
    """
    return _get_mode("STRATEGY_PEAD_PRIMARY_MODE", PLUGIN_MODE_VALUES, "off", skill_dir)


def get_strategy_pead_primary_allow_live(skill_dir: Path | None = None) -> bool:
    """Explicit allow for ``STRATEGY_PEAD_PRIMARY_MODE=live`` (still non-executable)."""
    return _get_bool("STRATEGY_PEAD_PRIMARY_ALLOW_LIVE", False, skill_dir)


def get_strategy_pead_primary_effective_mode(skill_dir: Path | None = None) -> str:
    """Scanner-facing PEAD-primary mode.

    ``live`` without ``STRATEGY_PEAD_PRIMARY_ALLOW_LIVE`` coerces to ``shadow``.
    Even when allowed, PEAD-only names remain non-executable in this release;
    effective mode may report ``live`` for diagnostics only.
    """
    mode = get_strategy_pead_primary_mode(skill_dir)
    if mode != "live":
        return mode
    if get_strategy_pead_primary_allow_live(skill_dir):
        return "live"
    return "shadow"


def get_pead_primary_shadow_max_names(skill_dir: Path | None = None) -> int:
    """Soft cap for PEAD-only shadow names retained in diagnostics (default 50).

    ``0`` keeps counters but omits the shadow name list. Unlike ``_get_int``,
    zero is allowed here. Sort key for the capped list: Stage-A ``edge_score``
    proxy desc, then ticker asc (diagnostics only — Stage-2 remains the
    trading control). Capacity selection uses
    :func:`get_pead_primary_shadow_rank_top_n`.
    """
    env = _load_env(skill_dir)
    v = _env_value("PEAD_PRIMARY_SHADOW_MAX_NAMES", env)
    if not v:
        return 50
    try:
        return max(0, int(float(v)))
    except (ValueError, TypeError):
        return 50


def get_pead_primary_shadow_rank_top_n(skill_dir: Path | None = None) -> int:
    """PEAD-native capacity rank N for shadow diagnostics (default 5).

    Matches capacity CF arm ``top5_by_edge_score``. Diagnostics/provenance only —
    never gates executable Stage-2 shortlists. ``0`` disables the capacity
    top-N annotation while still sorting the soft-capped shadow list by
    edge_score.
    """
    env = _load_env(skill_dir)
    v = _env_value("PEAD_PRIMARY_SHADOW_RANK_TOP_N", env)
    if not v:
        return 5
    try:
        return max(0, int(float(v)))
    except (ValueError, TypeError):
        return 5


def get_pead_primary_lookback_days(skill_dir: Path | None = None) -> int:
    """Earnings lookback for PEAD-primary admit (default: ``PEAD_LOOKBACK_DAYS``).

    Prefer ``PEAD_PRIMARY_LOOKBACK_DAYS`` when set so dual-admit can match
    backtest pead_primary without changing enrichment lookback.
    """
    env = _load_env(skill_dir)
    raw = _env_value("PEAD_PRIMARY_LOOKBACK_DAYS", env).strip()
    if raw:
        try:
            return max(1, int(float(raw)))
        except (ValueError, TypeError):
            pass
    return get_pead_lookback_days(skill_dir)


def get_strategy_regime_router_mode(skill_dir: Path | None = None) -> str:
    """Regime router weighting mode for strategy ensemble (OFF|SHADOW|LIVE)."""
    return _get_mode("STRATEGY_REGIME_ROUTER_MODE", PLUGIN_MODE_VALUES, "shadow", skill_dir)


def get_strategy_ensemble_mode(skill_dir: Path | None = None) -> str:
    """Final ensemble rank mode (OFF|SHADOW|LIVE)."""
    return _get_mode("STRATEGY_ENSEMBLE_MODE", PLUGIN_MODE_VALUES, "shadow", skill_dir)


def get_strategy_weight_breakout_high(skill_dir: Path | None = None) -> float:
    return _get_float("STRATEGY_WEIGHT_BREAKOUT_HIGH", 1.00, skill_dir)


def get_strategy_weight_breakout_med(skill_dir: Path | None = None) -> float:
    return _get_float("STRATEGY_WEIGHT_BREAKOUT_MED", 1.00, skill_dir)


def get_strategy_weight_breakout_low(skill_dir: Path | None = None) -> float:
    return _get_float("STRATEGY_WEIGHT_BREAKOUT_LOW", 0.95, skill_dir)


def get_strategy_weight_pullback_high(skill_dir: Path | None = None) -> float:
    return _get_float("STRATEGY_WEIGHT_PULLBACK_HIGH", 0.90, skill_dir)


def get_strategy_weight_pullback_med(skill_dir: Path | None = None) -> float:
    return _get_float("STRATEGY_WEIGHT_PULLBACK_MED", 1.05, skill_dir)


def get_strategy_weight_pullback_low(skill_dir: Path | None = None) -> float:
    return _get_float("STRATEGY_WEIGHT_PULLBACK_LOW", 1.10, skill_dir)


def get_exec_quality_min_signal_score(skill_dir: Path | None = None) -> int:
    """Execution quality threshold (unused for now)."""
    return _get_int("EXEC_QUALITY_MIN_SIGNAL_SCORE", 55, skill_dir)


def get_exec_spread_max_bps(skill_dir: Path | None = None) -> int:
    """Max allowed bid/ask spread in basis points for execution quality checks."""
    return _get_int("EXEC_SPREAD_MAX_BPS", 35, skill_dir)


def get_exec_slippage_max_bps(skill_dir: Path | None = None) -> int:
    """Max allowed expected slippage in basis points for execution quality checks."""
    return _get_int("EXEC_SLIPPAGE_MAX_BPS", 20, skill_dir)


def get_exec_reprice_attempts(skill_dir: Path | None = None) -> int:
    """Max bounded cancel/replace attempts for limit orders."""
    return _get_int("EXEC_REPRICE_ATTEMPTS", 2, skill_dir)


def get_exec_reprice_interval_sec(skill_dir: Path | None = None) -> int:
    """Seconds to wait between limit-order reprice checks."""
    return _get_int("EXEC_REPRICE_INTERVAL_SEC", 3, skill_dir)


def get_exec_use_limit_for_liquid(skill_dir: Path | None = None) -> bool:
    """Prefer limit orders for liquid symbols under execution quality live mode."""
    return _get_bool("EXEC_USE_LIMIT_FOR_LIQUID", True, skill_dir)


def get_exit_manager_trail_atr_mult(skill_dir: Path | None = None) -> float:
    """Exit manager threshold (unused for now)."""
    return _get_float("EXIT_MANAGER_TRAIL_ATR_MULT", 2.0, skill_dir)


def get_exit_partial_tp_r_mult(skill_dir: Path | None = None) -> float:
    """R-multiple target for first partial take-profit."""
    return _get_float("EXIT_PARTIAL_TP_R_MULT", 1.5, skill_dir)


def get_exit_partial_tp_fraction(skill_dir: Path | None = None) -> float:
    """Fraction of shares to trim at partial take-profit trigger."""
    value = _get_float("EXIT_PARTIAL_TP_FRACTION", 0.5, skill_dir)
    return max(0.05, min(0.95, value))


def get_exit_breakeven_after_partial(skill_dir: Path | None = None) -> bool:
    """Move residual stop to breakeven after partial fill."""
    return _get_bool("EXIT_BREAKEVEN_AFTER_PARTIAL", True, skill_dir)


def get_barrier_profit_r_multiple(skill_dir: Path | None = None) -> float:
    """Profit target in R for triple-barrier labels. Default 2R. Does not place orders."""
    val = _get_float("BARRIER_PROFIT_R_MULTIPLE", 2.0, skill_dir)
    return max(0.5, min(10.0, val))


def get_exit_max_hold_days(skill_dir: Path | None = None) -> int:
    """Maximum hold days before forcing a time-stop exit."""
    return _get_int("EXIT_MAX_HOLD_DAYS", 40, skill_dir)


def get_hold_days(skill_dir: Path | None = None) -> int:
    """Live hold reminder threshold; aligned with backtest max-hold window."""
    return max(1, _get_int("HOLD_DAYS", 40, skill_dir))


def get_exit_min_hold_days_before_trail(skill_dir: Path | None = None) -> int:
    """Calendar-day grace before live exit-manager actions (partial TP, time stop)."""
    return max(0, _get_int("EXIT_MIN_HOLD_DAYS_BEFORE_TRAIL", 15, skill_dir))


def get_event_risk_blackout_minutes(skill_dir: Path | None = None) -> int:
    """Event risk threshold (unused for now)."""
    return _get_int("EVENT_RISK_BLACKOUT_MINUTES", 30, skill_dir)


def get_event_block_earnings_days(skill_dir: Path | None = None) -> int:
    """Flag symbols with earnings within +/-N days."""
    return _get_int("EVENT_BLOCK_EARNINGS_DAYS", 2, skill_dir)


def get_event_macro_blackout_enabled(skill_dir: Path | None = None) -> bool:
    """Enable macro blackout date checks."""
    return _get_bool("EVENT_MACRO_BLACKOUT_ENABLED", False, skill_dir)


def get_event_action(skill_dir: Path | None = None) -> str:
    """Event-risk action policy: block or downsize."""
    env = _load_env(skill_dir)
    raw = _env_value("EVENT_ACTION", env).strip().lower()
    if raw in {"block", "downsize"}:
        return raw
    return "block"


def get_event_downsize_factor(skill_dir: Path | None = None) -> float:
    """Position multiplier used for event-risk downsize action."""
    v = _get_float("EVENT_DOWNSIZE_FACTOR", 0.5, skill_dir)
    return max(0.10, min(1.0, v))


def get_correlation_guard_max_pair_corr(skill_dir: Path | None = None) -> float:
    """Correlation guard pairwise-return threshold.

    Retained for a future true-correlation implementation. The scanner's
    final-ranking guard currently uses a sector-diversity proxy
    (``CORRELATION_GUARD_MAX_PER_SECTOR``) because signals do not carry a
    return series at ranking time.
    """
    return _get_float("CORRELATION_GUARD_MAX_PAIR_CORR", 0.85, skill_dir)


def get_correlation_guard_max_per_sector(skill_dir: Path | None = None) -> int:
    """Max number of same-sector names allowed before the correlation guard
    demotes (live) or flags (shadow) the overflow during final ranking."""
    return _get_int("CORRELATION_GUARD_MAX_PER_SECTOR", 2, skill_dir)


def get_regime_v2_min_confidence(skill_dir: Path | None = None) -> float:
    """Regime v2 threshold (unused for now)."""
    return _get_float("REGIME_V2_MIN_CONFIDENCE", 0.55, skill_dir)


def get_regime_v2_entry_min_score(skill_dir: Path | None = None) -> int:
    """Minimum composite regime score required for new entries."""
    return _get_int("REGIME_V2_ENTRY_MIN_SCORE", 55, skill_dir)


def get_regime_v2_size_mult_high(skill_dir: Path | None = None) -> float:
    """Sizing multiplier for high regime bucket."""
    return _get_float("REGIME_V2_SIZE_MULT_HIGH", 1.0, skill_dir)


def get_regime_v2_size_mult_med(skill_dir: Path | None = None) -> float:
    """Sizing multiplier for medium regime bucket."""
    return _get_float("REGIME_V2_SIZE_MULT_MED", 0.7, skill_dir)


def get_regime_v2_size_mult_low(skill_dir: Path | None = None) -> float:
    """Sizing multiplier for low regime bucket."""
    return _get_float("REGIME_V2_SIZE_MULT_LOW", 0.4, skill_dir)


def get_quality_gates_enabled(skill_dir: Path | None = None) -> bool:
    """Legacy check — prefer get_quality_gates_mode() directly."""
    return get_quality_gates_mode(skill_dir) in {"soft", "hard"}


def get_quality_gates_mode(skill_dir: Path | None = None) -> str:
    """
    Quality gate mode:
    - off: disabled (diagnostics only)
    - shadow: disabled but tracks would-filter counts
    - soft: filter only when multiple weak reasons exist (default)
    - hard: filter on any weak reason
    ``weak_breakout_volume`` does not drop a name by itself in soft mode,
    even when the soft minimum is 1. It counts once another reason is
    present. Hard mode still drops it alone.
    """
    env = _load_env(skill_dir)
    raw = _env_value("QUALITY_GATES_MODE", env).strip().lower()
    if raw in {"off", "shadow", "soft", "hard"}:
        return raw
    enabled = _get_bool("QUALITY_GATES_ENABLED", False, skill_dir)
    return "hard" if enabled else "shadow"


def get_quality_soft_min_reasons(skill_dir: Path | None = None) -> int:
    """Minimum number of weak reasons before filtering in soft mode."""
    return _get_int("QUALITY_SOFT_MIN_REASONS", 2, skill_dir)


def get_quality_min_signal_score(skill_dir: Path | None = None) -> int:
    """Configured minimum score. Prefer get_quality_min_signal_score_effective() at the gate."""
    return _get_int("QUALITY_MIN_SIGNAL_SCORE", 50, skill_dir)


def quality_signal_score_ceiling(skill_dir: Path | None = None) -> float:
    """Highest signal_score that can also pass the breakout-volume gate, without MiroFish.

    Closeness to the 52-week high is worth at most 40. SMA points use the live
    multiplier (0 by default). Volume points at the minimum passing ratio are
    worth at most ``20 - ratio * 20``. MiroFish is left out so a missing model
    cannot be required to clear the floor.
    """
    pts_52w = 40.0
    sma = float(get_score_pts_sma_cap(skill_dir)) * max(0.0, float(get_score_pts_sma_multiplier(skill_dir)))
    if get_quality_require_breakout_volume(skill_dir):
        ratio = max(0.0, float(get_quality_breakout_volume_min_ratio(skill_dir)))
        pts_volume = max(0.0, 20.0 - ratio * 20.0)
    else:
        pts_volume = 20.0
    return pts_52w + sma + pts_volume


def get_quality_min_signal_score_effective(skill_dir: Path | None = None) -> float:
    """Floor on the score the live formula can actually produce.

    ``QUALITY_MIN_SIGNAL_SCORE`` was set when SMA distance could add up to 25
    points. ``SCORE_PTS_SMA_MULTIPLIER`` defaults to 0, which removes that
    budget and leaves a floor of 50 or 52 above the remaining ceiling (~42
    with the volume gate on and MiroFish absent). The effective floor subtracts
    the inactive SMA budget, then caps at that ceiling.
    """
    configured = float(get_quality_min_signal_score(skill_dir))
    sma_cap = float(get_score_pts_sma_cap(skill_dir))
    sma_mult = max(0.0, float(get_score_pts_sma_multiplier(skill_dir)))
    inactive_sma = sma_cap * max(0.0, 1.0 - sma_mult)
    shifted = configured - inactive_sma
    ceiling = quality_signal_score_ceiling(skill_dir)
    return max(0.0, min(shifted, ceiling))


def get_quality_min_continuation_prob(skill_dir: Path | None = None) -> float:
    """Minimum continuation probability (0..1) when quality gates are enabled."""
    return _get_float("QUALITY_MIN_CONTINUATION_PROB", 0.55, skill_dir)


def get_quality_max_bull_trap_prob(skill_dir: Path | None = None) -> float:
    """Maximum acceptable bull-trap probability (0..1) when quality gates are enabled."""
    return _get_float("QUALITY_MAX_BULL_TRAP_PROB", 0.45, skill_dir)


def get_quality_require_breakout_volume(skill_dir: Path | None = None) -> bool:
    """Require latest volume above 50-day average when quality gates are enabled."""
    return _get_bool("QUALITY_REQUIRE_BREAKOUT_VOLUME", False, skill_dir)


def get_quality_breakout_volume_min_ratio(skill_dir: Path | None = None) -> float:
    """Required latest/avg50 volume ratio for breakout quality confirmation."""
    return _get_float("QUALITY_BREAKOUT_VOLUME_MIN_RATIO", 0.90, skill_dir)


def get_quality_watchlist_prefilter_enabled(skill_dir: Path | None = None) -> bool:
    """Reduce universe noise with deterministic prefiltering before scan loop."""
    return _get_bool("QUALITY_WATCHLIST_PREFILTER_ENABLED", False, skill_dir)


def get_quality_watchlist_prefilter_max(skill_dir: Path | None = None) -> int:
    """Maximum symbols after optional prefiltering."""
    return _get_int("QUALITY_WATCHLIST_PREFILTER_MAX", 800, skill_dir)


def get_forensic_enabled(skill_dir: Path | None = None) -> bool:
    """Enable forensic accounting enrichment/checks."""
    return _get_bool("FORENSIC_ENABLED", True, skill_dir)


def get_forensic_filter_mode(skill_dir: Path | None = None) -> str:
    """
    Forensic filter mode:
    - off: disabled
    - shadow: diagnostics-only
    - soft: add quality reasons but do not hard block
    - hard: block entries with forensic flags
    """
    env = _load_env(skill_dir)
    raw = _env_value("FORENSIC_FILTER_MODE", env).strip().lower()
    if raw in {"off", "shadow", "soft", "hard"}:
        return raw
    return "shadow"


def get_forensic_sloan_max(skill_dir: Path | None = None) -> float:
    """Max acceptable Sloan ratio before flagging accrual risk."""
    return _get_float("FORENSIC_SLOAN_MAX", 0.10, skill_dir)


def get_forensic_beneish_max(skill_dir: Path | None = None) -> float:
    """Max acceptable Beneish M-score before manipulation flag."""
    return _get_float("FORENSIC_BENEISH_MAX", -1.78, skill_dir)


def get_forensic_altman_min(skill_dir: Path | None = None) -> float:
    """Min acceptable Altman Z-score before distress flag."""
    return _get_float("FORENSIC_ALTMAN_MIN", 1.80, skill_dir)


def get_forensic_cache_hours(skill_dir: Path | None = None) -> float:
    """TTL for forensic snapshot cache."""
    return _get_float("FORENSIC_CACHE_HOURS", 24.0, skill_dir)


PEAD_DATA_PROVIDER_VALUES = frozenset({"finnhub", "yfinance", "alphavantage", "off"})


def get_pead_data_provider(skill_dir: Path | None = None) -> str:
    """PEAD earnings enrichment source (``finnhub`` | ``yfinance`` | ``alphavantage`` | ``off``).

    Governs only the earnings calendar / EPS surprise provider. Price history
    remains under ``SCHWAB_ONLY_DATA``; Finnhub/Alpha Vantage PEAD is compatible
    with Schwab-only bars.

    When ``PEAD_DATA_PROVIDER`` is unset: ``finnhub`` if ``FINNHUB_API_KEY`` is
    configured, else ``alphavantage`` if ``ALPHA_VANTAGE_API_KEY`` is set,
    otherwise ``off``.
    """
    env = _load_env(skill_dir)
    raw = _env_value("PEAD_DATA_PROVIDER", env).strip().lower()
    if raw in PEAD_DATA_PROVIDER_VALUES:
        return raw
    if raw:
        return "off"
    if get_finnhub_api_key(skill_dir):
        return "finnhub"
    if get_alpha_vantage_api_key(skill_dir):
        return "alphavantage"
    return "off"


def get_alpha_vantage_api_key(skill_dir: Path | None = None) -> str:
    """Optional Alpha Vantage key for PEAD historical EPS (``EARNINGS`` endpoint)."""
    env = _load_env(skill_dir)
    return (_env_value("ALPHA_VANTAGE_API_KEY", env) or "").strip()


def get_pead_min_history_rows(skill_dir: Path | None = None) -> int:
    """Min cached earnings rows before a ticker is treated as warm/complete.

    Shallow Finnhub calendar-only caches (~4 recent quarters) fail multi-era
    PEAD. Default 20 ≈ 5 years once yfinance limit=100 backfill is applied.
    """
    return max(1, min(80, _get_int("PEAD_MIN_HISTORY_ROWS", 20, skill_dir)))


def get_pead_yf_history_fallback(skill_dir: Path | None = None) -> bool:
    """Fill thin Finnhub/AV earnings history from yfinance (earnings only, not bars)."""
    return _get_bool("PEAD_YF_HISTORY_FALLBACK", True, skill_dir)


def get_pead_cache_enabled(skill_dir: Path | None = None) -> bool:
    """Enable on-disk cache for PEAD earnings rows (``.earnings_cache.json``)."""
    return _get_bool("PEAD_CACHE_ENABLED", True, skill_dir)


def get_pead_cache_hours(skill_dir: Path | None = None) -> float:
    """TTL for PEAD earnings cache entries."""
    return max(0.25, min(168.0, _get_float("PEAD_CACHE_HOURS", 24.0, skill_dir)))


def get_pead_warm_history_years(skill_dir: Path | None = None) -> int:
    """Years of Finnhub earnings history to fetch during cache warm."""
    return max(1, min(20, _get_int("PEAD_WARM_HISTORY_YEARS", 15, skill_dir)))


def get_pead_prescan_warm_enabled(skill_dir: Path | None = None) -> bool:
    """Warm missing Finnhub earnings rows before live scans when PEAD is active."""
    return _get_bool("PEAD_PRESCAN_WARM_ENABLED", True, skill_dir)


def get_pead_prescan_warm_max_missing(skill_dir: Path | None = None) -> int:
    """Inline pre-scan warm skips when more than this many tickers lack cache (0 = no cap)."""
    return max(0, _get_int("PEAD_PRESCAN_WARM_MAX_MISSING", 150, skill_dir))


def get_pead_enabled(skill_dir: Path | None = None) -> bool:
    """Enable post-earnings drift enrichment."""
    return _get_bool("PEAD_ENABLED", True, skill_dir)


def get_pead_lookback_days(skill_dir: Path | None = None) -> int:
    """Recent earnings window in days."""
    return _get_int("PEAD_LOOKBACK_DAYS", 10, skill_dir)


def get_pead_score_boost(skill_dir: Path | None = None) -> float:
    """Score boost for positive earnings surprise."""
    return _get_float("PEAD_SCORE_BOOST", 3.0, skill_dir)


def get_pead_score_boost_large(skill_dir: Path | None = None) -> float:
    """Score boost for strong positive earnings surprise."""
    return _get_float("PEAD_SCORE_BOOST_LARGE", 5.0, skill_dir)


def get_pead_score_penalty(skill_dir: Path | None = None) -> float:
    """Score penalty for a small/medium negative earnings surprise."""
    return _get_float("PEAD_SCORE_PENALTY", 3.0, skill_dir)


def get_pead_score_penalty_large(skill_dir: Path | None = None) -> float:
    """Score penalty for a large negative earnings surprise (default mirrors PEAD_SCORE_BOOST_LARGE).

    Symmetric counterpart to ``PEAD_SCORE_BOOST_LARGE``; applied when the
    surprise magnitude is at or below ``-15%``. Falls back to the small-miss
    penalty when unset to preserve historical behaviour.
    """
    fallback = _get_float("PEAD_SCORE_PENALTY", 3.0, skill_dir)
    return _get_float("PEAD_SCORE_PENALTY_LARGE", max(fallback, 5.0), skill_dir)


def get_guidance_score_enabled(skill_dir: Path | None = None) -> bool:
    """Enable guidance-tone score adjustments in scanner ranking."""
    return _get_bool("GUIDANCE_SCORE_ENABLED", True, skill_dir)


def get_guidance_score_boost(skill_dir: Path | None = None) -> float:
    """Score boost when filing guidance is positive."""
    return _get_float("GUIDANCE_SCORE_BOOST", 2.0, skill_dir)


def get_guidance_score_penalty(skill_dir: Path | None = None) -> float:
    """Score penalty when filing guidance is negative."""
    return _get_float("GUIDANCE_SCORE_PENALTY", 2.0, skill_dir)


def get_signal_universe_mode(skill_dir: Path | None = None) -> str:
    """
    Universe selection mode for scanning.
    - broad: keep full loaded watchlist (default when unset)
    - focused: narrows broad universes via prefilter_watchlist
    """
    env = _load_env(skill_dir)
    raw = _env_value("SIGNAL_UNIVERSE_MODE", env).strip().lower()
    if raw in {"focused", "broad"}:
        return raw
    return "broad"


def get_signal_universe_target_size(skill_dir: Path | None = None) -> int:
    """Target size for focused universe mode."""
    return _get_int("SIGNAL_UNIVERSE_TARGET_SIZE", 250, skill_dir)


def get_signal_scan_full_universe(skill_dir: Path | None = None) -> bool:
    """
    Legacy compatibility flag.

    Scan defaults now run strict SP1500 by default and ignore optional universe
    shortening in the main scanner path. Keep reading this env var for backwards
    compatibility with older tooling that may still surface it.
    """
    return _get_bool("SIGNAL_SCAN_FULL_UNIVERSE", False, skill_dir)


def get_sec_enrichment_enabled(skill_dir: Path | None = None) -> bool:
    """Enable SEC enrichment for reports/scanner tags."""
    return _get_bool("SEC_ENRICHMENT_ENABLED", True, skill_dir)


def get_sec_tagging_enabled(skill_dir: Path | None = None) -> bool:
    """Enable attaching SEC tags to signal payloads."""
    return _get_bool("SEC_TAGGING_ENABLED", True, skill_dir)


def get_sec_shadow_mode(skill_dir: Path | None = None) -> bool:
    """When true, SEC score hints are diagnostics-only and do not alter ranking."""
    return _get_bool("SEC_SHADOW_MODE", True, skill_dir)


def get_sec_score_hint_enabled(skill_dir: Path | None = None) -> bool:
    """Enable bounded SEC score hints in scanner ranking logic."""
    return _get_bool("SEC_SCORE_HINT_ENABLED", False, skill_dir)


def get_sec_cache_hours(skill_dir: Path | None = None) -> float:
    """SEC cache TTL in hours (conservative default)."""
    return _get_float("SEC_CACHE_HOURS", 12.0, skill_dir)


_EDGAR_USER_AGENT_DEFAULT = "SchwabTradingBot contact@example.com"


def get_edgar_user_agent(skill_dir: Path | None = None) -> str:
    """
    SEC requests should include a descriptive user-agent with contact info.
    Falls back to a safe placeholder when missing or invalid; callers that
    actually hit SEC EDGAR should additionally call ``is_real_edgar_user_agent``
    and refuse to make the request when it returns False.

    The placeholder is intentionally an obvious example.com address: SEC's
    fair-access policy bans generic / fake contact info and will rate-limit
    or IP-ban offenders. See ``is_real_edgar_user_agent``.
    """
    env = _load_env(skill_dir)
    raw = _env_value("EDGAR_USER_AGENT", env).strip()
    if len(raw) >= 12 and "@" in raw:
        return raw
    return _EDGAR_USER_AGENT_DEFAULT


def is_real_edgar_user_agent(user_agent: str | None) -> bool:
    """Return True only when ``user_agent`` looks like a real operator contact.

    The placeholder string ``contact@example.com`` is rejected because SEC
    EDGAR explicitly forbids fake contact info and may IP-ban requests using
    it (https://www.sec.gov/os/accessing-edgar-data).
    """
    if not user_agent:
        return False
    ua = str(user_agent).strip()
    if len(ua) < 12 or "@" not in ua:
        return False
    lowered = ua.lower()
    if "example.com" in lowered or "yourdomain" in lowered or "test@" in lowered:
        return False
    if ua == _EDGAR_USER_AGENT_DEFAULT:
        return False
    return True


def get_finnhub_api_key(skill_dir: Path | None = None) -> str:
    """Finnhub API key (empty string means Finnhub integrations are disabled)."""
    env = _load_env(skill_dir)
    return _env_value("FINNHUB_API_KEY", env).strip()


PIT_UNIVERSE_PROVIDER_VALUES = frozenset({"stub", "sharadar"})


def get_sharadar_api_key(skill_dir: Path | None = None) -> str:
    """Sharadar direct API key (empty string disables Sharadar)."""
    env = _load_env(skill_dir)
    return _env_value("SHARADAR_API_KEY", env).strip()


def get_sharadar_api_base(skill_dir: Path | None = None) -> str:
    """Sharadar REST base (not Nasdaq Data Link)."""
    env = _load_env(skill_dir)
    raw = _env_value("SHARADAR_API_BASE", env).strip()
    return raw or "https://api.sharadar.com/v1.0"


def get_sharadar_enabled(skill_dir: Path | None = None) -> bool:
    """Master switch for dashboard Sharadar snapshots. Default on when a key is set."""
    env = _load_env(skill_dir)
    raw = _env_value("SHARADAR_ENABLED", env).strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    return bool(get_sharadar_api_key(skill_dir))


def get_sharadar_cache_enabled(skill_dir: Path | None = None) -> bool:
    return _get_bool("SHARADAR_CACHE_ENABLED", True, skill_dir)


def get_sharadar_cache_hours(skill_dir: Path | None = None) -> float:
    return max(0.25, min(72.0, _get_float("SHARADAR_CACHE_HOURS", 12.0, skill_dir)))


def get_sharadar_timeout_sec(skill_dir: Path | None = None) -> float:
    return max(5.0, min(60.0, _get_float("SHARADAR_TIMEOUT_SEC", 30.0, skill_dir)))


def get_companyfacts_enabled(skill_dir: Path | None = None) -> bool:
    """Master switch for the Research pricing-model warehouse."""
    return _get_bool("COMPANYFACTS_ENABLED", True, skill_dir)


def get_companyfacts_cache_hours(skill_dir: Path | None = None) -> float:
    """TTL for successful SEC companyfacts documents."""
    return max(1.0, min(168.0, _get_float("COMPANYFACTS_CACHE_HOURS", 72.0, skill_dir)))


def get_companyfacts_timeout_sec(skill_dir: Path | None = None) -> float:
    return max(5.0, min(120.0, _get_float("COMPANYFACTS_TIMEOUT_SEC", 30.0, skill_dir)))


def get_companyfacts_yahoo_fallback(skill_dir: Path | None = None) -> bool:
    """Yahoo statement fallback for report DCF only — never mixed into Sharadar."""
    if get_schwab_only_data(skill_dir):
        return False
    return _get_bool("COMPANYFACTS_YAHOO_FALLBACK", False, skill_dir)


EVENT_PROB_PROVIDER_VALUES = frozenset({"stub", "polymarket", "historical_file"})


def get_event_prob_enabled(skill_dir: Path | None = None) -> bool:
    """Master switch for the research event-probability layer. Default off."""
    return _get_bool("EVENT_PROB_ENABLED", False, skill_dir)


def get_event_prob_mode(skill_dir: Path | None = None) -> str:
    """Event-probability rollout: off | shadow | live. Live shows the panel only."""
    return _get_mode("EVENT_PROB_MODE", PLUGIN_MODE_VALUES, "off", skill_dir)


def get_event_prob_provider(skill_dir: Path | None = None) -> str:
    """Event-market provider. Allowed: stub, polymarket, historical_file."""
    env = _load_env(skill_dir)
    raw = _env_value("EVENT_PROB_PROVIDER", env).strip().lower()
    if raw in EVENT_PROB_PROVIDER_VALUES:
        return raw
    return "stub"


def get_event_prob_timeout_ms(skill_dir: Path | None = None) -> int:
    """Per-request timeout in milliseconds for event-probability providers."""
    val = _get_int("EVENT_PROB_TIMEOUT_MS", 1500, skill_dir)
    return max(100, min(30000, val))


def get_event_prob_cache_ttl_sec(skill_dir: Path | None = None) -> int:
    """Event-risk payload cache TTL (seconds)."""
    return max(30, min(3600, _get_int("EVENT_PROB_CACHE_TTL_SEC", 300, skill_dir)))


def get_event_prob_max_event_age_hours(skill_dir: Path | None = None) -> float:
    """Age beyond which event weight shifts fully to the prior."""
    val = _get_float("EVENT_PROB_MAX_EVENT_AGE_HOURS", 72.0, skill_dir)
    return max(0.25, min(720.0, val))


def get_event_prob_min_liquidity(skill_dir: Path | None = None) -> float:
    """Liquidity floor used when scoring market trust."""
    val = _get_float("EVENT_PROB_MIN_LIQUIDITY", 1000.0, skill_dir)
    return max(0.0, val)


def get_event_prob_max_spread(skill_dir: Path | None = None) -> float:
    """Spread (0..1) at which market-trust decays to zero."""
    val = _get_float("EVENT_PROB_MAX_SPREAD", 0.08, skill_dir)
    return max(0.0, min(1.0, val))


def get_event_prob_max_market_weight(skill_dir: Path | None = None) -> float:
    """Cap on how much a single market can move a valuation (0..1)."""
    val = _get_float("EVENT_PROB_MAX_MARKET_WEIGHT", 0.85, skill_dir)
    return max(0.0, min(1.0, val))


def get_event_prob_ems_lookback_days(skill_dir: Path | None = None) -> int:
    """Lookback window (days) for the event mispricing score."""
    val = _get_int("EVENT_PROB_EMS_LOOKBACK_DAYS", 7, skill_dir)
    return max(1, min(365, val))


def get_event_prob_historical_snapshot_path(skill_dir: Path | None = None) -> str:
    """Frozen snapshot path required when the provider is historical_file."""
    env = _load_env(skill_dir)
    return _env_value("EVENT_PROB_HISTORICAL_SNAPSHOT_PATH", env).strip()


def get_pit_universe_provider(skill_dir: Path | None = None) -> str:
    """Live/research PIT membership provider. Default stub — do not auto-promote Sharadar."""
    env = _load_env(skill_dir)
    raw = _env_value("PIT_UNIVERSE_PROVIDER", env).strip().lower()
    if raw in PIT_UNIVERSE_PROVIDER_VALUES:
        return raw
    return "stub"


def get_pit_fundamental_coverage_min(skill_dir: Path | None = None) -> float:
    """Minimum non-null fraction before a deferred PIT field may unlock."""
    return max(0.0, min(1.0, _get_float("PIT_FUNDAMENTAL_COVERAGE_MIN", 0.70, skill_dir)))


def get_fred_api_key(skill_dir: Path | None = None) -> str:
    env = _load_env(skill_dir)
    return _env_value("FRED_API_KEY", env).strip()


def get_fred_regime_mode(skill_dir: Path | None = None) -> str:
    """FRED overlay: off | shadow | live. Live is coerced to shadow (never a hard gate)."""
    raw = _get_mode("FRED_REGIME_MODE", PLUGIN_MODE_VALUES, "off", skill_dir)
    if raw == "live":
        return "shadow"
    return raw


def get_finnhub_timeout_sec(skill_dir: Path | None = None) -> float:
    """Per-request timeout in seconds for Finnhub HTTP calls."""
    value = _get_float("FINNHUB_TIMEOUT_SEC", 8.0, skill_dir)
    return max(1.0, min(30.0, value))


def get_finnhub_news_days(skill_dir: Path | None = None) -> int:
    """Lookback window (days) for company-news fetches."""
    value = _get_int("FINNHUB_NEWS_DAYS", 30, skill_dir)
    return max(1, min(90, value))


def get_finnhub_max_news_items(skill_dir: Path | None = None) -> int:
    """Maximum company-news items retained in normalized payloads."""
    value = _get_int("FINNHUB_MAX_NEWS_ITEMS", 12, skill_dir)
    return max(1, min(50, value))


def get_finnhub_quality_priority(skill_dir: Path | None = None) -> bool:
    """Prefer data completeness/accuracy over fetch latency for dossier snapshots."""
    return _get_bool("FINNHUB_QUALITY_PRIORITY", True, skill_dir)


def get_finnhub_cache_enabled(skill_dir: Path | None = None) -> bool:
    """Enable local Finnhub dossier snapshot caching."""
    return _get_bool("FINNHUB_CACHE_ENABLED", True, skill_dir)


def get_finnhub_cache_hours(skill_dir: Path | None = None) -> float:
    """TTL for successful Finnhub dossier snapshots."""
    return max(0.25, min(72.0, _get_float("FINNHUB_CACHE_HOURS", 6.0, skill_dir)))


def get_finnhub_rate_limit_per_min(skill_dir: Path | None = None) -> int:
    """Client-side pacing cap. Lower defaults reduce 429 churn on free tier."""
    default = 45 if get_finnhub_quality_priority(skill_dir) else 55
    value = _get_int("FINNHUB_RATE_LIMIT_PER_MIN", default, skill_dir)
    return max(10, min(60, value))


def get_finnhub_max_retries(skill_dir: Path | None = None) -> int:
    """HTTP retry budget per endpoint call."""
    default = 6 if get_finnhub_quality_priority(skill_dir) else 3
    value = _get_int("FINNHUB_MAX_RETRIES", default, skill_dir)
    return max(0, min(12, value))


def get_finnhub_retry_backoff_cap_sec(skill_dir: Path | None = None) -> float:
    """Cap for exponential retry sleeps."""
    default = 45.0 if get_finnhub_quality_priority(skill_dir) else 30.0
    value = _get_float("FINNHUB_RETRY_BACKOFF_CAP_SEC", default, skill_dir)
    return max(5.0, min(120.0, value))


def get_sec_filing_analysis_enabled(skill_dir: Path | None = None) -> bool:
    """Enable full filing-text analysis endpoints and report enrichment."""
    return _get_bool("SEC_FILING_ANALYSIS_ENABLED", True, skill_dir)


def get_sec_filing_compare_enabled(skill_dir: Path | None = None) -> bool:
    """Enable SEC compare endpoints and dashboard compare panel."""
    return _get_bool("SEC_FILING_COMPARE_ENABLED", True, skill_dir)


def get_sec_filing_cache_hours(skill_dir: Path | None = None) -> float:
    """TTL for full filing text cache."""
    return _get_float("SEC_FILING_CACHE_HOURS", 24.0, skill_dir)


def get_sec_filing_max_chars(skill_dir: Path | None = None) -> int:
    """Max characters to keep per filing after normalization."""
    return _get_int("SEC_FILING_MAX_CHARS", 120000, skill_dir)


def get_sec_filing_max_compare_items(skill_dir: Path | None = None) -> int:
    """UI/API safeguard for compare requests."""
    return _get_int("SEC_FILING_MAX_COMPARE_ITEMS", 2, skill_dir)


def get_sec_filing_llm_summary_enabled(skill_dir: Path | None = None) -> bool:
    """Allow optional LLM summary generation on filing analyses."""
    return _get_bool("SEC_FILING_LLM_SUMMARY_ENABLED", True, skill_dir)


def get_advisory_model_enabled(skill_dir: Path | None = None) -> bool:
    """Enable advisory-only probability scoring on scan signals."""
    return _get_bool("ADVISORY_MODEL_ENABLED", True, skill_dir)


def get_advisory_model_path(skill_dir: Path | None = None) -> str:
    """Path to advisory model artifact JSON (relative to skill dir or absolute)."""
    env = _load_env(skill_dir)
    raw = _env_value("ADVISORY_MODEL_PATH", env).strip()
    return raw or "advisory_model_v1.json"


def get_advisory_confidence_high(skill_dir: Path | None = None) -> float:
    """High-confidence threshold for calibrated P(up_10d)."""
    return _get_float("ADVISORY_CONFIDENCE_HIGH", 0.62, skill_dir)


def get_advisory_confidence_low(skill_dir: Path | None = None) -> float:
    """Medium-confidence threshold for calibrated P(up_10d)."""
    return _get_float("ADVISORY_CONFIDENCE_LOW", 0.52, skill_dir)


def get_advisory_require_model(skill_dir: Path | None = None) -> bool:
    """When true, validation should fail if advisory model is missing."""
    return _get_bool("ADVISORY_REQUIRE_MODEL", False, skill_dir)


# --- Management integrity plugin (OFF|SHADOW|LIVE; default off) ---


def get_management_integrity_mode(skill_dir: Path | None = None) -> str:
    """Management integrity rollout mode (OFF|SHADOW|LIVE).

    OFF — no Stage B enrichment. SHADOW — attach scorecard evidence only.
    LIVE — score nudges deferred until packet cohort lift is confirmed.
    """
    return _get_mode("MANAGEMENT_INTEGRITY_MODE", PLUGIN_MODE_VALUES, "off", skill_dir)


def get_management_integrity_filter_min_score(skill_dir: Path | None = None) -> int:
    """Integrity score below this triggers a shadow would-filter counter."""
    return max(0, min(100, _get_int("MANAGEMENT_INTEGRITY_FILTER_MIN_SCORE", 50, skill_dir)))


# --- Data quality & degraded execution (default off: no behavior change) ---


def get_schwab_only_data(skill_dir: Path | None = None) -> bool:
    """When true, disable Yahoo Finance / Polygon fallbacks for OHLCV and quotes.

    Call sites that pull fundamentals, earnings calendars, or news exclusively
    via yfinance must also consult this flag — ``market_data`` alone cannot
    intercept module-local Yahoo usage inside forensic accounting, PEAD, etc.
    """
    return _get_bool("SCHWAB_ONLY_DATA", False, skill_dir)


def get_history_yfinance_adjusted(skill_dir: Path | None = None) -> bool:
    """When Yahoo Finance is used for OHLCV, True → split/dividend-adjusted closes (auto_adjust=True).

    Set HISTORY_YFINANCE_ADJUSTED=false only when you intentionally want raw closes
    for parity testing — mixing adjusted Schwab vendor series with raw Yahoo series
    corrupts Stage 2 / moving-average logic.
    """
    return _get_bool("HISTORY_YFINANCE_ADJUSTED", True, skill_dir)


def get_data_quality_exec_policy(skill_dir: Path | None = None) -> str:
    """
    How execution treats non-ok data_quality for risk-increasing orders:
    - off: no data-quality gate (default)
    - warn: log + metrics only
    - block_risk_increasing: block BUY / opening legs at guardrail boundary
    """
    env = _load_env(skill_dir)
    raw = _env_value("DATA_QUALITY_EXEC_POLICY", env).strip().lower()
    if raw in {"off", "warn", "block_risk_increasing"}:
        return raw
    return "off"


def get_data_quote_max_age_sec(skill_dir: Path | None = None) -> float:
    """Mark quote stale when last trade / quote timestamp older than this (seconds)."""
    return _get_float("DATA_QUOTE_MAX_AGE_SEC", 600.0, skill_dir)


def get_data_bar_max_staleness_days(skill_dir: Path | None = None) -> int:
    """Mark daily bars stale when last bar is older than this many calendar days."""
    return _get_int("DATA_BAR_MAX_STALENESS_DAYS", 7, skill_dir)


def get_risk_fail_closed_on_data_outage(skill_dir: Path | None = None) -> bool:
    """Whether risk gates (regime, sector filter) fail closed when data is unavailable.

    True (default) — when SPY / sector data can't be fetched, treat the regime
    as bearish and the winning-sector set as empty, blocking new entries.
    False — preserve the legacy permissive behaviour (assume bullish, allow
    every sector). This is dangerous in production: it means any data outage
    silently flips the bot into "trade everything" mode.
    """
    return _get_bool("RISK_FAIL_CLOSED_ON_DATA_OUTAGE", True, skill_dir)


def get_scan_stage_a_max_bar_age_days(skill_dir: Path | None = None) -> int:
    """Reject Stage A candidates whose last daily bar is older than this many calendar days.

    Tighter than DATA_BAR_MAX_STALENESS_DAYS because Stage A breakout / Stage 2
    decisions anchor against the latest bar — comparing today's live price
    against a stale prior-bar high silently mis-classifies setups when the
    feed is lagging. 3 covers a Friday → Monday weekend; 4 covers a 3-day
    holiday weekend.
    """
    return _get_int("SCAN_STAGE_A_MAX_BAR_AGE_DAYS", 4, skill_dir)


def get_data_edgar_max_age_hours(skill_dir: Path | None = None) -> float:
    """When SEC enrichment is on, flag if newest .sec_cache.json entry is older than this."""
    return _get_float("DATA_EDGAR_MAX_AGE_HOURS", 72.0, skill_dir)


def get_data_crosscheck_enabled(skill_dir: Path | None = None) -> bool:
    """Compare quote last to last daily close via yfinance when Schwab history exists."""
    return _get_bool("DATA_CROSSCHECK_ENABLED", False, skill_dir)


def get_data_crosscheck_max_rel_diff(skill_dir: Path | None = None) -> float:
    """Relative price difference that triggers data_quality=conflict when cross-check runs."""
    return _get_float("DATA_CROSSCHECK_MAX_REL_DIFF", 0.012, skill_dir)


def get_data_integrity_min_history_coverage_pct(skill_dir: Path | None = None) -> float:
    """Minimum symbol history coverage percent required by pre-run integrity gate."""
    val = _get_float("DATA_INTEGRITY_MIN_HISTORY_COVERAGE_PCT", 95.0, skill_dir)
    return max(0.0, min(100.0, val))


def get_data_integrity_min_history_bars(skill_dir: Path | None = None) -> int:
    """Minimum bars required for a symbol to count as history-covered."""
    return _get_int("DATA_INTEGRITY_MIN_HISTORY_BARS", 260, skill_dir)


def get_data_integrity_min_pm_coverage_pct(skill_dir: Path | None = None) -> float:
    """Minimum PM PIT coverage percent required by pre-run integrity gate."""
    val = _get_float("DATA_INTEGRITY_MIN_PM_COVERAGE_PCT", 25.0, skill_dir)
    return max(0.0, min(100.0, val))


def get_data_integrity_fail_on_silent_fallback(skill_dir: Path | None = None) -> bool:
    """Fail integrity gate when unclassified/unknown provider rows are detected."""
    return _get_bool("DATA_INTEGRITY_FAIL_ON_SILENT_FALLBACK", True, skill_dir)


def get_data_integrity_max_fallback_unknown_count(skill_dir: Path | None = None) -> int:
    """Maximum allowed unknown fallback classifications before failing gate."""
    return _get_int("DATA_INTEGRITY_MAX_FALLBACK_UNKNOWN_COUNT", 0, skill_dir)


# --- Hypothesis ledger (default off) ---


def get_hypothesis_ledger_enabled(skill_dir: Path | None = None) -> bool:
    return _get_bool("HYPOTHESIS_LEDGER_ENABLED", False, skill_dir)


def get_hypothesis_score_horizons(skill_dir: Path | None = None) -> list[int]:
    """Trading-day horizons for outcome scoring (e.g. 1, 5, 20)."""
    env = _load_env(skill_dir)
    raw = _env_value("HYPOTHESIS_SCORE_HORIZONS", env).strip()
    if not raw:
        return [1, 5, 20]
    out: list[int] = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(max(1, int(float(part))))
        except (ValueError, TypeError):
            continue
    return out or [1, 5, 20]


def get_hypothesis_self_study_merge(skill_dir: Path | None = None) -> bool:
    """Attach hypothesis score summaries into run_self_study() output when ledger exists."""
    return _get_bool("HYPOTHESIS_SELF_STUDY_MERGE", False, skill_dir)


def get_hypothesis_promotion_guard_enabled(skill_dir: Path | None = None) -> bool:
    """When true, advisory promotion scripts consult scored hypothesis hit rates."""
    return _get_bool("HYPOTHESIS_PROMOTION_GUARD_ENABLED", False, skill_dir)


def get_hypothesis_promotion_min_n(skill_dir: Path | None = None) -> int:
    return _get_int("HYPOTHESIS_PROMOTION_MIN_N", 30, skill_dir)


def get_hypothesis_promotion_min_hit_rate(skill_dir: Path | None = None) -> float:
    return _get_float("HYPOTHESIS_PROMOTION_MIN_HIT_RATE", 0.45, skill_dir)


def get_experiment_collector_enabled(skill_dir: Path | None = None) -> bool:
    """Harvest shadow sessions and hypothesis rows after a scan.

    Unset stays on so a missing flag cannot drop the research loop. Set
    EXPERIMENT_COLLECTOR_ENABLED=false to stop harvest and the 16:15 brief.
    """
    return _get_bool("EXPERIMENT_COLLECTOR_ENABLED", True, skill_dir)


# --- Agent intelligence controls (default off / safe) ---


def get_mirofish_weighting_mode(skill_dir: Path | None = None) -> str:
    """Dynamic persona weighting mode (OFF|SHADOW|LIVE)."""
    return _get_mode("MIROFISH_WEIGHTING_MODE", PLUGIN_MODE_VALUES, "off", skill_dir)


def get_mirofish_weighting_window_days(skill_dir: Path | None = None) -> int:
    """Historical window used to compute persona reliability."""
    val = _get_int("MIROFISH_WEIGHTING_WINDOW_DAYS", 60, skill_dir)
    return max(7, min(365, val))


def get_mirofish_weighting_min_samples(skill_dir: Path | None = None) -> int:
    """Minimum labeled outcomes before reliability reweighting engages."""
    val = _get_int("MIROFISH_WEIGHTING_MIN_SAMPLES", 30, skill_dir)
    return max(5, min(1000, val))


def get_mirofish_weighting_decay_half_life_days(skill_dir: Path | None = None) -> float:
    """Time-decay half-life for reliability history weighting."""
    val = _get_float("MIROFISH_WEIGHTING_DECAY_HALF_LIFE_DAYS", 20.0, skill_dir)
    return max(1.0, min(365.0, val))


def get_mirofish_weighting_max_multiplier(skill_dir: Path | None = None) -> float:
    """Upper cap for persona multiplier derived from reliability."""
    val = _get_float("MIROFISH_WEIGHTING_MAX_MULTIPLIER", 1.8, skill_dir)
    return max(1.0, min(4.0, val))


def get_mirofish_weighting_min_multiplier(skill_dir: Path | None = None) -> float:
    """Lower cap for persona multiplier derived from reliability."""
    val = _get_float("MIROFISH_WEIGHTING_MIN_MULTIPLIER", 0.5, skill_dir)
    return max(0.1, min(1.0, val))


def get_mirofish_abstain_mode(skill_dir: Path | None = None) -> str:
    """High persona disagreement rollout (OFF|SHADOW|LIVE).

    Default ``shadow``: record ``mirofish_abstain_would`` and leave
    ``signal_score`` unchanged. ``live`` drops the MiroFish points from the
    score and clears conviction.
    """
    return _get_mode("MIROFISH_ABSTAIN_MODE", PLUGIN_MODE_VALUES, "shadow", skill_dir)


def get_mirofish_abstain_disagreement(skill_dir: Path | None = None) -> float:
    """Disagreement at or above this value can abstain. Matches the debate cap."""
    val = _get_float("MIROFISH_ABSTAIN_DISAGREEMENT", 0.65, skill_dir)
    return max(0.0, min(1.0, val))


def get_debate_grade_mode(skill_dir: Path | None = None) -> str:
    """Checklist grade rollout (OFF|SHADOW).

    Default ``shadow``: attach the grade and log would-skip counters.
    ``live`` is accepted in the environment and coerced to ``shadow`` by
    ``attach_debate_grade``. The grade never changes ``signal_score``.
    """
    return _get_mode("DEBATE_GRADE_MODE", PLUGIN_MODE_VALUES, "shadow", skill_dir)


def get_debate_committee_mode(skill_dir: Path | None = None) -> str:
    """Sampled LLM committee (OFF|SHADOW).

    Default ``shadow`` when an API key exists: at most a few names per scan.
    ``live`` is coerced to ``shadow``. The committee never runs in the backtest
    and never changes ``qual_raw`` or ``signal_score``. With no API key the
    attach path returns without a network call.
    """
    return _get_mode("DEBATE_COMMITTEE_MODE", PLUGIN_MODE_VALUES, "shadow", skill_dir)


def get_debate_committee_max_per_scan(skill_dir: Path | None = None) -> int:
    """Cap on committee calls per scan. The stable hash still skips most names."""
    val = _get_int("DEBATE_COMMITTEE_MAX_PER_SCAN", 3, skill_dir)
    return max(1, min(20, val))


def get_meta_policy_mode(skill_dir: Path | None = None) -> str:
    """Meta-policy rollout mode (OFF|SHADOW|LIVE).

    Default ``shadow``: accumulate counterfactual suppress/downsize evidence
    before live promotion (see promotion-playbook).
    """
    return _get_mode("META_POLICY_MODE", PLUGIN_MODE_VALUES, "shadow", skill_dir)


def get_meta_policy_min_base_score(skill_dir: Path | None = None) -> float:
    """Minimum baseline score required before meta-policy can increase size."""
    val = _get_float("META_POLICY_MIN_BASE_SCORE", 40.0, skill_dir)
    return max(0.0, min(100.0, val))


def get_meta_policy_max_score_delta(skill_dir: Path | None = None) -> float:
    """Absolute clamp for meta-policy score adjustments."""
    val = _get_float("META_POLICY_MAX_SCORE_DELTA", 4.0, skill_dir)
    return max(0.0, min(20.0, val))


def get_meta_policy_size_mult_min(skill_dir: Path | None = None) -> float:
    """Lower bound for meta-policy size multipliers."""
    val = _get_float("META_POLICY_SIZE_MULT_MIN", 0.70, skill_dir)
    return max(0.1, min(1.0, val))


def get_meta_policy_size_mult_max(skill_dir: Path | None = None) -> float:
    """Upper bound for meta-policy size multipliers."""
    val = _get_float("META_POLICY_SIZE_MULT_MAX", 1.10, skill_dir)
    return max(1.0, min(3.0, val))


def get_meta_policy_suppress_threshold(skill_dir: Path | None = None) -> float:
    """Uncertainty threshold above which signals are suppressed."""
    val = _get_float("META_POLICY_SUPPRESS_THRESHOLD", 0.25, skill_dir)
    return max(0.0, min(1.0, val))


def get_meta_policy_downsize_threshold(skill_dir: Path | None = None) -> float:
    """Uncertainty threshold above which size is reduced."""
    val = _get_float("META_POLICY_DOWNSIZE_THRESHOLD", 0.45, skill_dir)
    return max(0.0, min(1.0, val))


def get_uncertainty_mode(skill_dir: Path | None = None) -> str:
    """Uncertainty plugin rollout mode (OFF|SHADOW|LIVE)."""
    return _get_mode("UNCERTAINTY_MODE", PLUGIN_MODE_VALUES, "shadow", skill_dir)


def get_uncertainty_high_threshold(skill_dir: Path | None = None) -> float:
    """High uncertainty threshold."""
    val = _get_float("UNCERTAINTY_HIGH_THRESHOLD", 0.65, skill_dir)
    return max(0.0, min(1.0, val))


def get_uncertainty_med_threshold(skill_dir: Path | None = None) -> float:
    """Medium uncertainty threshold."""
    val = _get_float("UNCERTAINTY_MED_THRESHOLD", 0.45, skill_dir)
    return max(0.0, min(1.0, val))


def get_uncertainty_score_delta_penalty(skill_dir: Path | None = None) -> float:
    """Absolute score penalty applied when uncertainty is elevated."""
    val = _get_float("UNCERTAINTY_SCORE_DELTA_PENALTY", 2.0, skill_dir)
    return max(0.0, min(10.0, val))


def get_uncertainty_size_mult_floor(skill_dir: Path | None = None) -> float:
    """Minimum size multiplier allowed after uncertainty penalty."""
    val = _get_float("UNCERTAINTY_SIZE_MULT_FLOOR", 0.75, skill_dir)
    return max(0.1, min(1.0, val))


def get_counterfactual_logging_enabled(skill_dir: Path | None = None) -> bool:
    """Enable counterfactual logging for filtered/suppressed opportunities."""
    return _get_bool("COUNTERFACTUAL_LOGGING_ENABLED", True, skill_dir)


def get_counterfactual_max_horizon_days(skill_dir: Path | None = None) -> int:
    """Maximum outcome horizon tracked for counterfactual scoring."""
    val = _get_int("COUNTERFACTUAL_MAX_HORIZON_DAYS", 20, skill_dir)
    return max(1, min(252, val))


def get_decision_packet_horizon_days(skill_dir: Path | None = None) -> int:
    """Primary outcome horizon. Default 20 trading days (about four weeks)."""
    val = _get_int("DECISION_PACKET_HORIZON_DAYS", 20, skill_dir)
    return max(1, min(120, val))


def get_decision_packet_early_horizon_days(skill_dir: Path | None = None) -> int:
    """Early-signal horizon. Fixed close-to-close, not the promotion label."""
    val = _get_int("DECISION_PACKET_EARLY_HORIZON_DAYS", 10, skill_dir)
    return max(1, min(60, val))


def get_decision_packet_position_horizon_days(skill_dir: Path | None = None) -> int:
    """Longer swing/position horizon recorded beside the 20-day primary."""
    val = _get_int("DECISION_PACKET_POSITION_HORIZON_DAYS", 40, skill_dir)
    return max(1, min(120, val))


def get_decision_packet_exit_model(skill_dir: Path | None = None) -> str:
    """How primary and position horizons are labeled.

    ``scale_out`` sells a fraction at the R target and trails the rest.
    ``fixed_horizon`` keeps the old close-to-close mark. The early horizon
    stays close-to-close either way. This does not place orders.
    """
    raw = _env_value("DECISION_PACKET_EXIT_MODEL", _load_env(skill_dir)).strip().lower()
    if raw in {"fixed", "fixed_horizon"}:
        return "fixed_horizon"
    return "scale_out"


def get_decision_packet_scale_out_fraction(skill_dir: Path | None = None) -> float:
    """Share sold at the R target. The rest is trailed. Default one half."""
    val = _get_float("DECISION_PACKET_SCALE_OUT_FRACTION", 0.5, skill_dir)
    return max(0.05, min(0.95, val))


def get_decision_packet_scale_out_target_r(skill_dir: Path | None = None) -> float:
    """R multiple that triggers the partial sale. Default 2R."""
    val = _get_float("DECISION_PACKET_SCALE_OUT_TARGET_R", 2.0, skill_dir)
    return max(0.5, min(10.0, val))


def get_decision_packet_scale_out_trail_r(skill_dir: Path | None = None) -> float:
    """Trail distance, in R, under the high after the partial. Default 1R."""
    val = _get_float("DECISION_PACKET_SCALE_OUT_TRAIL_R", 1.0, skill_dir)
    return max(0.25, min(5.0, val))


def get_decision_packet_scan_recording_enabled(skill_dir: Path | None = None) -> bool:
    """Record one staged packet per kept scan signal (ticker, UTC day)."""
    return _get_bool("DECISION_PACKET_SCAN_RECORDING_ENABLED", True, skill_dir)


def get_tab_agent_objective_metric(skill_dir: Path | None = None) -> str:
    """Primary score for every tab agent: sortino (default) or sharpe."""
    raw = _env_value("TAB_AGENT_OBJECTIVE", _load_env(skill_dir)).strip().lower()
    if raw in ("sortino", "sharpe"):
        return raw
    return "sortino"


def get_tab_agent_min_samples(skill_dir: Path | None = None) -> int:
    """Minimum labeled primary outcomes before a prompt comparison is readable."""
    val = _get_int("TAB_AGENT_MIN_SAMPLES", 30, skill_dir)
    return max(5, min(5000, val))


def get_tab_agent_replay_weight(skill_dir: Path | None = None) -> float:
    """Weight of a historical replay sample relative to one live sample. Default 0.5."""
    raw = _env_value("TAB_AGENT_REPLAY_WEIGHT", _load_env(skill_dir))
    if not str(raw).strip():
        return 0.5
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, value))


def get_tab_agent_min_live_samples(skill_dir: Path | None = None) -> int:
    """Live outcomes required before a replay-boosted book can be preferred.

    Replay rows count toward ``TAB_AGENT_MIN_SAMPLES`` at ``TAB_AGENT_REPLAY_WEIGHT``.
    They do not replace this live floor.
    """
    val = _get_int("TAB_AGENT_MIN_LIVE_SAMPLES", 10, skill_dir)
    return max(1, min(5000, val))


def get_tab_agent_replay_usd_per_call(skill_dir: Path | None = None) -> float:
    """Planning price for one replay LLM call. Dry-run multiplies this by the call count."""
    val = _get_float("TAB_AGENT_REPLAY_USD_PER_CALL", 0.01, skill_dir)
    return max(0.0, min(10.0, val))


def get_tab_agent_drawdown_tolerance(skill_dir: Path | None = None) -> float:
    """Absolute max-drawdown guardrail. Default 20%. Values below 20% are raised.

    A candidate is blocked only when its equity drawdown is deeper than this.
    Ordinary drawdowns inside the guardrail do not veto a better Sortino or Sharpe.
    """
    val = _get_float("TAB_AGENT_DRAWDOWN_TOLERANCE", 0.20, skill_dir)
    return max(0.20, min(0.80, val))


def get_tab_agent_drawdown_duration_tolerance_days(skill_dir: Path | None = None) -> int:
    """Reported underwater length. It does not veto promotion or rollback."""
    val = _get_int("TAB_AGENT_DRAWDOWN_DURATION_TOLERANCE_DAYS", 10, skill_dir)
    return max(0, min(252, val))


def get_tab_agent_min_avg_dollar_volume(skill_dir: Path | None = None) -> float:
    """Default liquidity floor in average dollar volume. Market cap is not used."""
    val = _get_float("TAB_AGENT_MIN_AVG_DOLLAR_VOLUME", 20_000_000.0, skill_dir)
    return max(0.0, val)


def get_tab_agent_max_spread_pct(skill_dir: Path | None = None) -> float:
    """Default maximum bid/ask spread as a fraction of price. 0.01 is 1%."""
    val = _get_float("TAB_AGENT_MAX_SPREAD_PCT", 0.01, skill_dir)
    return max(0.0, val)


def get_tab_agent_adv_bounds(skill_dir: Path | None = None) -> tuple[float, float]:
    """Inclusive bounds an agent may use when it retunes the dollar-volume floor."""
    low = _get_float("TAB_AGENT_MIN_AVG_DOLLAR_VOLUME_MIN", 5_000_000.0, skill_dir)
    high = _get_float("TAB_AGENT_MIN_AVG_DOLLAR_VOLUME_MAX", 100_000_000.0, skill_dir)
    if high < low:
        low, high = high, low
    return max(0.0, low), max(low, high)


def get_tab_agent_spread_bounds(skill_dir: Path | None = None) -> tuple[float, float]:
    """Inclusive bounds for the spread cap an agent may propose."""
    low = _get_float("TAB_AGENT_MAX_SPREAD_PCT_MIN", 0.002, skill_dir)
    high = _get_float("TAB_AGENT_MAX_SPREAD_PCT_MAX", 0.03, skill_dir)
    if high < low:
        low, high = high, low
    return max(0.0, low), max(low, high)


def get_tab_agent_quality_threshold(skill_dir: Path | None = None) -> float:
    """Score a name must clear. There is no default top-N."""
    val = _get_float("TAB_AGENT_QUALITY_THRESHOLD", 60.0, skill_dir)
    low, high = get_tab_agent_quality_bounds(skill_dir)
    return max(low, min(high, val))


def get_tab_agent_quality_bounds(skill_dir: Path | None = None) -> tuple[float, float]:
    """Inclusive bounds for the quality threshold an agent may propose."""
    low = _get_float("TAB_AGENT_QUALITY_THRESHOLD_MIN", 40.0, skill_dir)
    high = _get_float("TAB_AGENT_QUALITY_THRESHOLD_MAX", 90.0, skill_dir)
    if high < low:
        low, high = high, low
    return low, high


def get_tab_agent_debate_max_names(skill_dir: Path | None = None) -> int:
    """Name cap for the debate tab. 0 means no cap: every name that clears quality."""
    val = _get_int("TAB_AGENT_DEBATE_MAX_NAMES", 0, skill_dir)
    return max(0, min(5000, val))


def get_tab_agent_review_wait_hours(skill_dir: Path | None = None) -> int:
    """Hours a reviewer verdict may be absent before metrics alone can promote."""
    val = _get_int("TAB_AGENT_REVIEW_WAIT_HOURS", 48, skill_dir)
    return max(1, min(24 * 14, val))


def get_tab_agent_today_enabled(skill_dir: Path | None = None) -> bool:
    """Serve Today prompts from the registry. Default on. Set false to freeze the built-ins."""
    return _get_bool("TAB_AGENT_TODAY_ENABLED", True, skill_dir)


def get_tab_agent_shadow_enabled(skill_dir: Path | None = None) -> bool:
    """Run shadow candidates beside live. They do not change conviction until promoted."""
    return _get_bool("TAB_AGENT_SHADOW_ENABLED", True, skill_dir)


def get_tab_agent_debate_enabled(skill_dir: Path | None = None) -> bool:
    """Version and govern the debate committee prompts. Default on. Does not place orders."""
    return _get_bool("TAB_AGENT_DEBATE_ENABLED", True, skill_dir)


def get_tab_agent_debate_shadow_enabled(skill_dir: Path | None = None) -> bool:
    """Run a shadow committee prompt beside the live one. Off means no extra LLM calls."""
    return _get_bool("TAB_AGENT_DEBATE_SHADOW_ENABLED", True, skill_dir)


def get_tab_agent_research_enabled(skill_dir: Path | None = None) -> bool:
    """Serve Research quick-check prompts from the registry. Default off."""
    return _get_bool("TAB_AGENT_RESEARCH_ENABLED", False, skill_dir)


def get_tab_agent_research_shadow_enabled(skill_dir: Path | None = None) -> bool:
    """Score a shadow quick-check prompt beside the live verdict. Default off."""
    return _get_bool("TAB_AGENT_RESEARCH_SHADOW_ENABLED", False, skill_dir)


def get_tab_agent_backtest_enabled(skill_dir: Path | None = None) -> bool:
    """Serve the strategy-chat prompt from the registry. Default off. Does not place orders."""
    return _get_bool("TAB_AGENT_BACKTEST_ENABLED", False, skill_dir)


def get_tab_agent_backtest_shadow_enabled(skill_dir: Path | None = None) -> bool:
    """Keep a shadow strategy-chat prompt for scoring. Default off."""
    return _get_bool("TAB_AGENT_BACKTEST_SHADOW_ENABLED", False, skill_dir)


def get_tab_agent_system_enabled(skill_dir: Path | None = None) -> bool:
    """Let the system agent revise confidence wording. Default off. Does not place orders."""
    return _get_bool("TAB_AGENT_SYSTEM_ENABLED", False, skill_dir)


def get_tab_agent_system_shadow_enabled(skill_dir: Path | None = None) -> bool:
    """Score a shadow confidence wording beside the live one. Default off."""
    return _get_bool("TAB_AGENT_SYSTEM_SHADOW_ENABLED", False, skill_dir)


def get_tab_agent_lab_enabled(skill_dir: Path | None = None) -> bool:
    """Nightly prompt lab. Default off. The lab never places orders."""
    return _get_bool("TAB_AGENT_LAB_ENABLED", False, skill_dir)


def get_tab_agent_lab_concurrency(skill_dir: Path | None = None) -> int:
    """How many candidates a generation may score at once."""
    return max(1, min(16, _get_int("TAB_AGENT_LAB_CONCURRENCY", 6, skill_dir)))


def get_tab_agent_lab_candidates(skill_dir: Path | None = None) -> int:
    """Rival prompts created in one generation."""
    return max(2, min(16, _get_int("TAB_AGENT_LAB_CANDIDATES", 8, skill_dir)))


def get_tab_agent_lab_model(skill_dir: Path | None = None) -> str:
    """Model id for lab calls. Empty keeps ``LLM_MODEL_NAME``."""
    return _env_value("TAB_AGENT_LAB_MODEL", _load_env(skill_dir)).strip()


def get_tab_agent_lab_token_cap(skill_dir: Path | None = None) -> int:
    """Approximate token budget for one night. 0 means unlimited."""
    raw = _env_value("TAB_AGENT_LAB_TOKEN_CAP", _load_env(skill_dir)).strip()
    if not raw:
        return 0
    try:
        return max(0, int(float(raw)))
    except (TypeError, ValueError):
        return 0


def get_tab_agent_lab_holdout_weeks(skill_dir: Path | None = None) -> int:
    """Most recent weeks kept out of the generator's scores."""
    return max(1, min(26, _get_int("TAB_AGENT_LAB_HOLDOUT_WEEKS", 6, skill_dir)))


def get_tab_agent_lab_margin(skill_dir: Path | None = None) -> float:
    """Sortino points a winner must clear on both the train window and the holdout."""
    val = _get_float("TAB_AGENT_LAB_MARGIN", 0.05, skill_dir)
    return max(0.0, min(5.0, val))


def get_tab_agent_lab_min_holdout_samples(skill_dir: Path | None = None) -> int:
    """Labeled holdout rows required before a candidate can be a winner."""
    return max(5, min(5000, _get_int("TAB_AGENT_LAB_MIN_HOLDOUT", 8, skill_dir)))


def get_tab_agent_lab_min_participation(skill_dir: Path | None = None) -> float:
    """Taken longs required as a fraction of the live book's longs, on each window."""
    val = _get_float("TAB_AGENT_LAB_MIN_PARTICIPATION", 0.60, skill_dir)
    return max(0.05, min(1.0, val))


def get_tab_agent_lab_min_longs(skill_dir: Path | None = None) -> int:
    """Minimum taken longs on a window. Cash rows do not count."""
    return max(1, min(5000, _get_int("TAB_AGENT_LAB_MIN_LONGS", 8, skill_dir)))


def get_tab_agent_lab_max_cases(skill_dir: Path | None = None) -> int:
    """Newest stored cases kept for one night. Caps the replay read."""
    return max(20, min(2000, _get_int("TAB_AGENT_LAB_MAX_CASES", 180, skill_dir)))


def get_tab_agent_lab_drift_weight(skill_dir: Path | None = None) -> float:
    """Subtracted from Sortino for prompt-length drift and extra sentences."""
    val = _get_float("TAB_AGENT_LAB_DRIFT_WEIGHT", 0.15, skill_dir)
    return max(0.0, min(2.0, val))


def get_tab_agent_lab_max_generations(skill_dir: Path | None = None) -> int:
    """Generations per tab. 0 runs until the morning window closes."""
    raw = _env_value("TAB_AGENT_LAB_MAX_GENERATIONS", _load_env(skill_dir)).strip()
    if not raw:
        return 0
    try:
        return max(0, int(float(raw)))
    except (TypeError, ValueError):
        return 0


def get_tab_agent_lab_usd_per_1k(skill_dir: Path | None = None) -> float:
    """Planning price for 1,000 approximate tokens. Used only in the morning report."""
    val = _get_float("TAB_AGENT_LAB_USD_PER_1K", 0.01, skill_dir)
    return max(0.0, min(10.0, val))


def get_tab_agent_lab_tabs(skill_dir: Path | None = None) -> tuple[str, ...]:
    """Tabs the night loop visits. Unknown names are dropped."""
    raw = _env_value("TAB_AGENT_LAB_TABS", _load_env(skill_dir)).strip()
    allowed = ("today", "debate", "research", "backtest", "system")
    if not raw:
        return allowed
    chosen = [part.strip().lower() for part in raw.split(",") if part.strip()]
    picked = tuple(name for name in allowed if name in chosen)
    return picked or allowed


def get_tab_agent_confidence_high_min(skill_dir: Path | None = None) -> float:
    """Stated confidence at or above this takes the labeled long return."""
    low, high = get_tab_agent_confidence_high_bounds(skill_dir)
    val = _get_float("TAB_AGENT_CONFIDENCE_HIGH_MIN", 0.70, skill_dir)
    return max(low, min(high, val))


def get_tab_agent_confidence_high_bounds(skill_dir: Path | None = None) -> tuple[float, float]:
    """Inclusive bounds for the high-confidence cutoff."""
    low = _get_float("TAB_AGENT_CONFIDENCE_HIGH_MIN_BOUND", 0.55, skill_dir)
    high = _get_float("TAB_AGENT_CONFIDENCE_HIGH_MAX_BOUND", 0.90, skill_dir)
    if high < low:
        low, high = high, low
    return low, high


def get_tab_agent_confidence_low_max(skill_dir: Path | None = None) -> float:
    """Stated confidence at or below this is a skip, scored on the return it missed."""
    low, high = get_tab_agent_confidence_low_bounds(skill_dir)
    val = _get_float("TAB_AGENT_CONFIDENCE_LOW_MAX", 0.40, skill_dir)
    return max(low, min(high, val))


def get_tab_agent_confidence_low_bounds(skill_dir: Path | None = None) -> tuple[float, float]:
    """Inclusive bounds for the low-confidence cutoff."""
    low = _get_float("TAB_AGENT_CONFIDENCE_LOW_MIN_BOUND", 0.15, skill_dir)
    high = _get_float("TAB_AGENT_CONFIDENCE_LOW_MAX_BOUND", 0.55, skill_dir)
    if high < low:
        low, high = high, low
    return low, high


def get_counterfactual_min_labeled_samples(skill_dir: Path | None = None) -> int:
    """Minimum labeled samples before counterfactual stats are trusted."""
    val = _get_int("COUNTERFACTUAL_MIN_LABELED_SAMPLES", 100, skill_dir)
    return max(10, min(20000, val))


# --- Trading Cockpit (Phase 0): provider layer + observability ---


def get_cockpit_providers_mode(skill_dir: Path | None = None) -> str:
    """Rollout mode for the cockpit provider/contract layer (OFF|SHADOW|LIVE).

    - off (default): providers are not consumed by any route; no behavior change.
    - shadow: providers run alongside existing endpoints for parity comparison.
    - live: cockpit routes consume normalized DTOs from the provider layer.
    """
    return _get_mode("COCKPIT_PROVIDERS_MODE", PLUGIN_MODE_VALUES, "off", skill_dir)


def get_observability_metrics_enabled(skill_dir: Path | None = None) -> bool:
    """Emit the frozen cockpit observability metrics (latency, fallback, etc.).

    Instrumentation only — writes a rolling-window JSON metrics file and, in
    SaaS, updates Prometheus collectors when present. Default on; set
    OBSERVABILITY_METRICS_ENABLED=false to silence (e.g. read-only sandboxes).
    """
    return _get_bool("OBSERVABILITY_METRICS_ENABLED", True, skill_dir)


def get_pre_trade_gates_mode(skill_dir: Path | None = None) -> str:
    """Cockpit pre-trade quality gates rollout mode (OFF|SHADOW|LIVE).

    - off: checks are not computed (cards omit the pre_trade block).
    - shadow (recommended first): checks computed and surfaced as badges, but
      ``tradeable`` is advisory only — nothing is blocked.
    - live: callers may gray-out / block non-tradeable cards.
    """
    return _get_mode("PRE_TRADE_GATES_MODE", PLUGIN_MODE_VALUES, "shadow", skill_dir)


def get_pretrade_max_spread_bps(skill_dir: Path | None = None) -> float:
    """Maximum acceptable bid/ask spread (basis points) for a tradeable card."""
    val = _get_float("PRETRADE_MAX_SPREAD_BPS", 50.0, skill_dir)
    return max(1.0, min(1000.0, val))


def get_pretrade_min_dollar_volume(skill_dir: Path | None = None) -> float:
    """Minimum 50-day average dollar volume (price * avg_vol_50) for liquidity OK."""
    val = _get_float("PRETRADE_MIN_DOLLAR_VOLUME", 2_000_000.0, skill_dir)
    return max(0.0, val)


# --- Trading Cockpit (Phase 2): expanded Schwab market intelligence ---


def get_market_movers_mode(skill_dir: Path | None = None) -> str:
    """Schwab /movers (market internals) rollout mode (OFF|SHADOW|LIVE). Default live."""
    return _get_mode("MARKET_MOVERS_MODE", PLUGIN_MODE_VALUES, "live", skill_dir)


def get_options_intel_mode(skill_dir: Path | None = None) -> str:
    """Schwab options-chain intelligence rollout mode (OFF|SHADOW|LIVE). Default live."""
    return _get_mode("OPTIONS_INTEL_MODE", PLUGIN_MODE_VALUES, "live", skill_dir)


def get_instruments_mode(skill_dir: Path | None = None) -> str:
    """Schwab /instruments fundamentals+search rollout mode (OFF|SHADOW|LIVE). Default live."""
    return _get_mode("INSTRUMENTS_MODE", PLUGIN_MODE_VALUES, "live", skill_dir)


def get_minute_history_mode(skill_dir: Path | None = None) -> str:
    """Schwab intraday (minute) pricehistory rollout mode (OFF|SHADOW|LIVE). Default off."""
    return _get_mode("MINUTE_HISTORY_MODE", PLUGIN_MODE_VALUES, "off", skill_dir)


def get_options_scoring_mode(skill_dir: Path | None = None) -> str:
    """Feed options-chain intelligence into scan scoring (OFF|SHADOW|LIVE).

    - off: no options scoring.
    - shadow (default): compute + attach an options score delta to top survivors
      for measurement, but DO NOT change ranking.
    - live: apply the delta to rank/composite and re-sort survivors.

    Requires OPTIONS_INTEL_MODE != off (the data source). Bounded to the top
    OPTIONS_SCORING_MAX_SYMBOLS survivors to limit extra Schwab chain calls.
    """
    return _get_mode("OPTIONS_SCORING_MODE", PLUGIN_MODE_VALUES, "shadow", skill_dir)


def get_options_scoring_max_symbols(skill_dir: Path | None = None) -> int:
    """Max top-ranked survivors to fetch option chains for during scoring overlay."""
    val = _get_int("OPTIONS_SCORING_MAX_SYMBOLS", 5, skill_dir)
    return max(1, min(100, val))


def get_scan_delta_improve_min(skill_dir: Path | None = None) -> float:
    """Minimum rank_score increase vs last cycle to count as 'setup improving'."""
    val = _get_float("SCAN_DELTA_IMPROVE_MIN", 5.0, skill_dir)
    return max(0.1, min(100.0, val))


# --- Trading Cockpit (Phase 3): execution policies + post-fill risk ---


def get_exec_policy_mode(skill_dir: Path | None = None) -> str:
    """Smart execution policy rollout mode (OFF|SHADOW|LIVE).

    - off: no policy decision computed.
    - shadow (default): policy decision computed, recorded, and attached to the
      order result as ``_execution_policy`` — but does NOT change order routing.
      Watch the recorded metrics, then promote with EXEC_POLICY_MODE=live.
    - live: place_order applies the decision — limit-vs-market preference,
      reprice loop, and an auto-throttle HOLD (fail-closed block) on
      risk-increasing orders when data quality is degraded/stale/conflict.
    """
    return _get_mode("EXEC_POLICY_MODE", PLUGIN_MODE_VALUES, "shadow", skill_dir)


def get_exec_policy_tight_spread_bps(skill_dir: Path | None = None) -> float:
    """Spread (bps) at/under which the reprice loop uses the aggressive cadence."""
    val = _get_float("EXEC_POLICY_TIGHT_SPREAD_BPS", 10.0, skill_dir)
    return max(1.0, min(500.0, val))


def get_risk_max_concentration_pct(skill_dir: Path | None = None) -> float:
    """Max single-position weight (% of equity) before a concentration drift flag."""
    val = _get_float("RISK_MAX_CONCENTRATION_PCT", 25.0, skill_dir)
    return max(1.0, min(100.0, val))


def get_risk_max_gross_exposure_pct(skill_dir: Path | None = None) -> float:
    """Max gross exposure (% of equity) before an exposure drift flag."""
    val = _get_float("RISK_MAX_GROSS_EXPOSURE_PCT", 150.0, skill_dir)
    return max(10.0, min(1000.0, val))


# --- Multi-sleeve allocator / R7 (constitution 2026-07-27; default off) ---


def get_allocator_mode(skill_dir: Path | None = None) -> str:
    """Multi-sleeve allocator mode (OFF|SHADOW|LIVE). Default off until Phase 2 promote."""
    return _get_mode("ALLOCATOR_MODE", PLUGIN_MODE_VALUES, "off", skill_dir)


def get_s1_live_enabled(skill_dir: Path | None = None) -> bool:
    """When true, S1 PEAD desired weights may enter the live net book (Phase 7)."""
    return _get_bool("MULTI_SLEEVE_S1_LIVE", False, skill_dir)


def get_s1_promoted_cap(skill_dir: Path | None = None) -> bool:
    """When true, S1 cap rises from 15% to 25% (explicit promotion only)."""
    return _get_bool("MULTI_SLEEVE_S1_PROMOTED_CAP", False, skill_dir)


def get_allocator_hysteresis_abs(skill_dir: Path | None = None) -> float:
    """Absolute weight band before rebalance trades (|Δw|)."""
    val = _get_float("ALLOCATOR_HYSTERESIS_ABS", 0.01, skill_dir)
    return max(0.0, min(0.10, val))


def get_r7_min_n(skill_dir: Path | None = None) -> int:
    """Minimum sleeve-local scored hypothesis rows before R7 promote."""
    return max(1, _get_int("R7_PROMOTION_MIN_N", 40, skill_dir))


def get_r7_primary_horizon(skill_dir: Path | None = None) -> str:
    """Primary horizon key for R7 expectancy (default T+5)."""
    env = _load_env(skill_dir)
    raw = _env_value("R7_PRIMARY_HORIZON", env).strip() or "5"
    return raw


def get_r3_capacity_shadow(skill_dir: Path | None = None) -> bool:
    """Annotate S0 rows with capacity-adjusted scores (R3 shadow)."""
    return _get_bool("R3_CAPACITY_SHADOW", False, skill_dir)


def get_r6_vol_damper_enabled(skill_dir: Path | None = None) -> bool:
    """Shrink sleeve caps when book vol spikes (allocator research chip)."""
    return _get_bool("R6_VOL_DAMPER_ENABLED", False, skill_dir)


def get_operator_desk_mode(skill_dir: Path | None = None) -> str:
    """Scheduled operator desk. off skips the clock. shadow and live never place orders."""
    return _get_mode("OPERATOR_DESK_MODE", PLUGIN_MODE_VALUES, "off", skill_dir)


def get_operator_desk_hour(skill_dir: Path | None = None) -> int:
    """New York hour for the weekday operator desk."""
    return max(0, min(23, _get_int("OPERATOR_DESK_HOUR", 8, skill_dir)))


def get_operator_desk_minute(skill_dir: Path | None = None) -> int:
    """New York minute for the weekday operator desk."""
    return max(0, min(59, _get_int("OPERATOR_DESK_MINUTE", 30, skill_dir)))


def get_operator_desk_run_scan(skill_dir: Path | None = None) -> bool:
    """When true, a scheduled desk starts a dashboard scan."""
    return _get_bool("OPERATOR_DESK_RUN_SCAN", True, skill_dir)


def get_operator_desk_notify(skill_dir: Path | None = None) -> bool:
    """When true, a scheduled desk posts the brief to Discord."""
    return _get_bool("OPERATOR_DESK_NOTIFY", True, skill_dir)


def get_operator_desk_queue_backtest(skill_dir: Path | None = None) -> bool:
    """Queue a trailing replay when the backtest worker is idle.

    Unset follows the desk mode: live queues, shadow only reads the latest run.
    """
    env = _load_env(skill_dir)
    raw = _env_value("OPERATOR_DESK_QUEUE_BACKTEST", env).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return get_operator_desk_mode(skill_dir) == "live"


def get_operator_desk_base_url(skill_dir: Path | None = None) -> str:
    """Dashboard origin the desk calls. Local operator default is port 8182."""
    env = _load_env(skill_dir)
    raw = _env_value("OPERATOR_DESK_BASE_URL", env).strip()
    return raw or "https://127.0.0.1:8182"


def get_operator_desk_scan_timeout_sec(skill_dir: Path | None = None) -> int:
    """How long the desk waits for a scan to leave running."""
    return max(30, min(14400, _get_int("OPERATOR_DESK_SCAN_TIMEOUT_SEC", 5400, skill_dir)))


def get_tradeable_brief_enabled(skill_dir: Path | None = None) -> bool:
    """Weekday Discord list of Tradeable scan rows. Does not place orders."""
    return _get_bool("TRADEABLE_BRIEF_ENABLED", True, skill_dir)


def get_tradeable_brief_hour(skill_dir: Path | None = None) -> int:
    """Local hour for the tradeable-universe brief. Default 8."""
    return max(0, min(23, _get_int("TRADEABLE_BRIEF_HOUR", 8, skill_dir)))


def get_tradeable_brief_minute(skill_dir: Path | None = None) -> int:
    """Local minute for the tradeable-universe brief. Default 30."""
    return max(0, min(59, _get_int("TRADEABLE_BRIEF_MINUTE", 30, skill_dir)))


def get_tradeable_brief_timezone(skill_dir: Path | None = None) -> str:
    """IANA zone for the tradeable-universe brief. Default America/Chicago."""
    env = _load_env(skill_dir)
    raw = _env_value("TRADEABLE_BRIEF_TZ", env).strip()
    return raw or "America/Chicago"


# --- Grok agent (default off: no xAI calls) ---


def get_grok_agent_mode(skill_dir: Path | None = None) -> str:
    """Research/review agent rollout (OFF|SHADOW|LIVE).

    Default ``off`` does not call xAI. ``shadow`` and ``live`` may call the
    API. Neither mode places orders or opens pull requests by itself.
    """
    return _get_mode("GROK_AGENT_MODE", PLUGIN_MODE_VALUES, "off", skill_dir)


def get_grok_agent_model(skill_dir: Path | None = None) -> str:
    """xAI model id for the Grok agent. Default is the documented flagship."""
    env = _load_env(skill_dir)
    raw = _env_value("GROK_AGENT_MODEL", env).strip()
    return raw or "grok-4.7"


def get_grok_agent_base_url(skill_dir: Path | None = None) -> str:
    """API origin. Only api.x.ai and us.api.x.ai are accepted by the adapter."""
    env = _load_env(skill_dir)
    raw = _env_value("GROK_AGENT_BASE_URL", env).strip()
    return raw or "https://api.x.ai/v1"


def get_grok_agent_api_key(skill_dir: Path | None = None) -> str:
    """xAI key. Never falls back to OPENAI_API_KEY."""
    env = _load_env(skill_dir)
    return _env_value("XAI_API_KEY", env).strip() or _env_value("MIROFISH_API_KEY", env).strip()


def get_grok_agent_search(skill_dir: Path | None = None) -> bool:
    """When true, chat search_parameters.mode=on (web and X). Default off."""
    return _get_bool("GROK_AGENT_SEARCH", False, skill_dir)


def get_grok_agent_reasoning_effort(skill_dir: Path | None = None) -> str:
    """Optional reasoning_effort. Empty omits the field and keeps the API default."""
    env = _load_env(skill_dir)
    return _env_value("GROK_AGENT_REASONING_EFFORT", env).strip().lower()


def get_grok_agent_timeout_sec(skill_dir: Path | None = None) -> int:
    """HTTP timeout for one xAI call."""
    return max(10, min(180, _get_int("GROK_AGENT_TIMEOUT_SEC", 150, skill_dir)))


def get_grok_agent_max_completion_tokens(skill_dir: Path | None = None) -> int:
    """Cap on visible completion tokens. The API default is much higher."""
    return max(256, min(16000, _get_int("GROK_AGENT_MAX_COMPLETION_TOKENS", 4000, skill_dir)))


def get_grok_agent_max_retries(skill_dir: Path | None = None) -> int:
    """Extra attempts after HTTP 429. Default 2."""
    env = _load_env(skill_dir)
    raw = _env_value("GROK_AGENT_MAX_RETRIES", env).strip()
    if not raw:
        return 2
    try:
        return max(0, min(5, int(float(raw))))
    except (TypeError, ValueError):
        return 2


def get_grok_agent_deferred_polls(skill_dir: Path | None = None) -> int:
    """How many times to GET a deferred chat completion after the first 202."""
    return max(1, min(20, _get_int("GROK_AGENT_DEFERRED_POLLS", 5, skill_dir)))
