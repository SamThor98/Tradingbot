"""Small helpers for updating local .env files (no secrets)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

ENTRY_TIMING_EXPERIMENT_ENV: dict[str, str] = {
    "ENTRY_TIMING_SHADOW_MODE": "shadow",
    "ENTRY_SHADOW_DISABLE_SMA50_FILTERS": "true",
    "ENTRY_SHADOW_MIN_BREAKOUT_BUFFER_PCT": "0.01",
}

ENTRY_TIMING_LIVE_ENV: dict[str, str] = {
    **ENTRY_TIMING_EXPERIMENT_ENV,
    "ENTRY_TIMING_SHADOW_MODE": "live",
}

# Offline-validated stack: exit grace (15d defer) + 1% breakout buffer in shadow only.
# Use for clean counterfactual weeks — forces entry back to shadow and turns off
# already-promoted plugins (event risk / exec quality). Prefer SIGNAL_STACK_ENFORCED_ENV
# for normal local operation after entry-timing live promotion.
SIGNAL_STACK_SHADOW_ENV: dict[str, str] = {
    **ENTRY_TIMING_EXPERIMENT_ENV,
    "EXIT_MANAGER_MODE": "shadow",
    "EXIT_MIN_HOLD_DAYS_BEFORE_TRAIL": "15",
    "EXIT_MAX_HOLD_DAYS": "40",
    "HOLD_DAYS": "40",
    "BACKTEST_HOLD_DAYS": "40",
    "BACKTEST_MIN_HOLD_DAYS_BEFORE_TRAIL": "15",
    "BACKTEST_MIN_HOLD_DEFER_SOFT_EXITS": "true",
    "COUNTERFACTUAL_LOGGING_ENABLED": "true",
    "META_POLICY_MODE": "shadow",
    "UNCERTAINTY_MODE": "shadow",
    "CONFLUENCE_GATE_MODE": "shadow",
    "EVENT_RISK_MODE": "off",
    "EXEC_QUALITY_MODE": "off",
}

# Promoted operating stack: live 1% breakout buffer + live exit grace (15/40)
# + live rank-v2 p75 trim. Does not demote EVENT_RISK / EXEC_QUALITY.
SIGNAL_STACK_ENFORCED_ENV: dict[str, str] = {
    **ENTRY_TIMING_LIVE_ENV,
    "EXIT_MANAGER_MODE": "live",
    "RANK_FILTER_V2_MODE": "live",
    "RANK_FILTER_SHADOW_MIN_PERCENTILE_RANK_V2": "75",
    "EXIT_MIN_HOLD_DAYS_BEFORE_TRAIL": "15",
    "EXIT_MAX_HOLD_DAYS": "40",
    "HOLD_DAYS": "40",
    "BACKTEST_HOLD_DAYS": "40",
    "BACKTEST_MIN_HOLD_DAYS_BEFORE_TRAIL": "15",
    "BACKTEST_MIN_HOLD_DEFER_SOFT_EXITS": "true",
    "COUNTERFACTUAL_LOGGING_ENABLED": "true",
}


def apply_entry_timing_live_env(env_path: Path) -> list[str]:
    """Upsert breakout-buffer-only live enforcement vars."""
    return upsert_env_file(env_path, ENTRY_TIMING_LIVE_ENV)


def upsert_env_file(path: Path, updates: dict[str, str]) -> list[str]:
    """Insert or replace KEY=value lines. Returns keys that were added or changed."""
    changed: list[str] = []
    lines: list[str] = []
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()

    index_by_key: dict[str, int] = {}
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        index_by_key[key] = idx

    for key, value in updates.items():
        new_line = f"{key}={value}"
        if key in index_by_key:
            idx = index_by_key[key]
            if lines[idx] != new_line:
                lines[idx] = new_line
                changed.append(key)
        else:
            lines.append(new_line)
            changed.append(key)

    if changed or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return changed


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=value pairs from a dotenv file (no variable expansion)."""
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key, val = stripped.split("=", 1)
        out[key.strip()] = val.strip()
    return out


def _env_bool(raw: str | None, default: bool = False) -> bool:
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_float(raw: str | None, default: float) -> float:
    if raw is None:
        return default
    try:
        return float(str(raw).strip())
    except ValueError:
        return default


def entry_timing_experiment_readiness_from_values(values: dict[str, str]) -> dict[str, Any]:
    """Evaluate experiment readiness from a flat env map (file or process)."""
    expected_profile = "breakout_buffer_only_0.010"
    mode = str(values.get("ENTRY_TIMING_SHADOW_MODE", "shadow")).strip().lower()
    disable_sma50 = _env_bool(values.get("ENTRY_SHADOW_DISABLE_SMA50_FILTERS"), False)
    buffer = max(0.0, min(0.05, _env_float(values.get("ENTRY_SHADOW_MIN_BREAKOUT_BUFFER_PCT"), 0.002)))

    if mode == "off":
        profile = "off"
    elif disable_sma50:
        profile = f"breakout_buffer_only_{buffer:.3f}"
    else:
        profile = "default_sma50_and_buffer"

    missing_env: list[str] = []
    if mode not in {"shadow", "live"}:
        missing_env.append("ENTRY_TIMING_SHADOW_MODE=shadow|live")
    if not disable_sma50:
        missing_env.append("ENTRY_SHADOW_DISABLE_SMA50_FILTERS=true")
    if abs(buffer - 0.01) > 1e-9:
        missing_env.append("ENTRY_SHADOW_MIN_BREAKOUT_BUFFER_PCT=0.01")

    recommended_env = dict(ENTRY_TIMING_EXPERIMENT_ENV)
    profile_ready = profile == expected_profile and not missing_env
    shadow_ready = profile_ready and mode == "shadow"
    return {
        "ready": shadow_ready,
        "profile_ready": profile_ready,
        "mode": mode,
        "live_enforced": mode == "live",
        "profile": profile,
        "expected_profile": expected_profile,
        "missing_env": missing_env,
        "recommended_env": recommended_env,
    }


def entry_timing_experiment_file_readiness(env_path: Path) -> dict[str, Any]:
    return entry_timing_experiment_readiness_from_values(parse_env_file(env_path))


def apply_entry_timing_experiment_env(env_path: Path) -> list[str]:
    """Enable P0 breakout-buffer-only shadow experiment vars in a local .env file."""
    return upsert_env_file(env_path, ENTRY_TIMING_EXPERIMENT_ENV)


def signal_stack_shadow_readiness_from_values(values: dict[str, str]) -> dict[str, Any]:
    """Evaluate offline-validated stack shadow rollout readiness from env map."""
    entry = entry_timing_experiment_readiness_from_values(values)
    exit_mode = str(values.get("EXIT_MANAGER_MODE", "off")).strip().lower()
    min_hold = max(0, int(_env_float(values.get("EXIT_MIN_HOLD_DAYS_BEFORE_TRAIL"), 15)))
    max_hold = max(1, int(_env_float(values.get("EXIT_MAX_HOLD_DAYS"), 40)))
    missing: list[str] = []
    if exit_mode != "shadow":
        missing.append("EXIT_MANAGER_MODE=shadow")
    if min_hold != 15:
        missing.append("EXIT_MIN_HOLD_DAYS_BEFORE_TRAIL=15")
    if max_hold != 40:
        missing.append("EXIT_MAX_HOLD_DAYS=40")
    if entry.get("mode") == "live":
        missing.append("ENTRY_TIMING_SHADOW_MODE must not be live for stack shadow rollout")
    if not entry.get("ready"):
        missing.extend(entry.get("missing_env") or [])
    ready = not missing and bool(entry.get("ready"))
    return {
        "ready": ready,
        "exit_manager_mode": exit_mode,
        "exit_min_hold_days_before_trail": min_hold,
        "exit_max_hold_days": max_hold,
        "entry_timing": entry,
        "missing_env": missing,
        "recommended_env": dict(SIGNAL_STACK_SHADOW_ENV),
    }


def signal_stack_shadow_file_readiness(env_path: Path) -> dict[str, Any]:
    return signal_stack_shadow_readiness_from_values(parse_env_file(env_path))


def apply_signal_stack_shadow_env(env_path: Path) -> list[str]:
    """Enable P0 stack shadow vars (exit grace + breakout buffer) in a local .env file."""
    return upsert_env_file(env_path, SIGNAL_STACK_SHADOW_ENV)


def signal_stack_enforced_readiness_from_values(values: dict[str, str]) -> dict[str, Any]:
    """Evaluate live-entry + live-exit-grace operating stack readiness."""
    mode = str(values.get("ENTRY_TIMING_SHADOW_MODE", "live")).strip().lower()
    disable_sma50 = _env_bool(values.get("ENTRY_SHADOW_DISABLE_SMA50_FILTERS"), True)
    buffer = max(0.0, min(0.05, _env_float(values.get("ENTRY_SHADOW_MIN_BREAKOUT_BUFFER_PCT"), 0.01)))
    exit_mode = str(values.get("EXIT_MANAGER_MODE", "off")).strip().lower()
    rank_filter_mode = str(values.get("RANK_FILTER_V2_MODE", "live")).strip().lower()
    rank_filter_percentile = max(
        0,
        min(95, int(_env_float(values.get("RANK_FILTER_SHADOW_MIN_PERCENTILE_RANK_V2"), 75))),
    )
    min_hold = max(0, int(_env_float(values.get("EXIT_MIN_HOLD_DAYS_BEFORE_TRAIL"), 15)))
    max_hold = max(1, int(_env_float(values.get("EXIT_MAX_HOLD_DAYS"), 40)))
    hold_days = max(1, int(_env_float(values.get("HOLD_DAYS"), 40)))
    bt_hold = max(1, int(_env_float(values.get("BACKTEST_HOLD_DAYS"), 40)))
    bt_min = max(0, int(_env_float(values.get("BACKTEST_MIN_HOLD_DAYS_BEFORE_TRAIL"), 15)))
    bt_defer = _env_bool(values.get("BACKTEST_MIN_HOLD_DEFER_SOFT_EXITS"), True)
    cf_log = _env_bool(values.get("COUNTERFACTUAL_LOGGING_ENABLED"), True)

    if disable_sma50:
        profile = f"breakout_buffer_only_{buffer:.3f}"
    else:
        profile = "default_sma50_and_buffer"

    missing: list[str] = []
    if mode != "live":
        missing.append("ENTRY_TIMING_SHADOW_MODE=live")
    if not disable_sma50:
        missing.append("ENTRY_SHADOW_DISABLE_SMA50_FILTERS=true")
    if abs(buffer - 0.01) > 1e-9:
        missing.append("ENTRY_SHADOW_MIN_BREAKOUT_BUFFER_PCT=0.01")
    if exit_mode != "live":
        missing.append("EXIT_MANAGER_MODE=live")
    if rank_filter_mode != "live":
        missing.append("RANK_FILTER_V2_MODE=live")
    if rank_filter_percentile != 75:
        missing.append("RANK_FILTER_SHADOW_MIN_PERCENTILE_RANK_V2=75")
    if min_hold != 15:
        missing.append("EXIT_MIN_HOLD_DAYS_BEFORE_TRAIL=15")
    if max_hold != 40:
        missing.append("EXIT_MAX_HOLD_DAYS=40")
    if hold_days != 40:
        missing.append("HOLD_DAYS=40")
    if bt_hold != 40:
        missing.append("BACKTEST_HOLD_DAYS=40")
    if bt_min != 15:
        missing.append("BACKTEST_MIN_HOLD_DAYS_BEFORE_TRAIL=15")
    if not bt_defer:
        missing.append("BACKTEST_MIN_HOLD_DEFER_SOFT_EXITS=true")
    if not cf_log:
        missing.append("COUNTERFACTUAL_LOGGING_ENABLED=true")

    ready = not missing and profile == "breakout_buffer_only_0.010"
    return {
        "ready": ready,
        "profile": profile,
        "expected_profile": "breakout_buffer_only_0.010",
        "entry_timing_mode": mode,
        "exit_manager_mode": exit_mode,
        "rank_filter_v2_mode": rank_filter_mode,
        "rank_filter_v2_min_percentile": rank_filter_percentile,
        "exit_min_hold_days_before_trail": min_hold,
        "exit_max_hold_days": max_hold,
        "missing_env": missing,
        "recommended_env": dict(SIGNAL_STACK_ENFORCED_ENV),
    }


def signal_stack_enforced_file_readiness(env_path: Path) -> dict[str, Any]:
    return signal_stack_enforced_readiness_from_values(parse_env_file(env_path))


def apply_signal_stack_enforced_env(env_path: Path) -> list[str]:
    """Enable live 1% breakout buffer + live exit-grace stack in a local .env file."""
    return upsert_env_file(env_path, SIGNAL_STACK_ENFORCED_ENV)


def reload_env_file_into_process(env_path: Path, keys: list[str] | None = None) -> dict[str, str | None]:
    """Load .env keys into os.environ; return prior values for restore."""
    import os

    target_keys = keys
    if target_keys is None:
        target_keys = list(parse_env_file(env_path).keys())
    saved = {key: os.environ.get(key) for key in target_keys}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key, val = stripped.split("=", 1)
        key = key.strip()
        if target_keys is None or key in target_keys:
            os.environ[key] = val.strip()
    return saved


def restore_process_env(saved: dict[str, str | None]) -> None:
    import os

    for key, prior in saved.items():
        if prior is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = prior
