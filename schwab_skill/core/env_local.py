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
