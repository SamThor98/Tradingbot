"""Config for the SEC companyfacts pricing warehouse."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class CompanyfactsConfig:
    enabled: bool
    cache_hours: float
    timeout_sec: float
    yahoo_fallback: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "cache_hours": self.cache_hours,
            "timeout_sec": self.timeout_sec,
            "yahoo_fallback": self.yahoo_fallback,
        }


def load_companyfacts_config(skill_dir: Path | None = None) -> CompanyfactsConfig:
    from config import (
        get_companyfacts_cache_hours,
        get_companyfacts_enabled,
        get_companyfacts_timeout_sec,
        get_companyfacts_yahoo_fallback,
    )

    return CompanyfactsConfig(
        enabled=get_companyfacts_enabled(skill_dir),
        cache_hours=get_companyfacts_cache_hours(skill_dir),
        timeout_sec=get_companyfacts_timeout_sec(skill_dir),
        yahoo_fallback=get_companyfacts_yahoo_fallback(skill_dir),
    )
