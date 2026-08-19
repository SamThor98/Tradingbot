"""
Optional JSON body for POST /api/scan: align live scan env/universe with backtest StrategySpec patterns.
"""

from __future__ import annotations

import os
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.scan_catalog import (
    DEFAULT_UNIVERSE,
    env_overrides_for_strategies,
    known_strategy_ids,
    known_timeframe_ids,
    known_universe_ids,
    resolve_strategy_ids,
)

from .backtest_spec import StrategyOverrides

_TICKER_RE = re.compile(r"^[A-Z0-9.\-]{1,16}$")

UniversePreset = Literal["sp1500", "sp500", "nasdaq100", "sector_etfs", "focused", "custom"]
ScanTimeframe = Literal["intraday", "daily", "weekly", "monthly"]


def scan_max_custom_tickers() -> int:
    return max(1, int(os.getenv("SAAS_SCAN_MAX_CUSTOM_TICKERS", os.getenv("SAAS_BACKTEST_MAX_TICKERS", "40"))))


class ScanRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_overrides: StrategyOverrides | None = None
    universe_mode: Literal["watchlist", "tickers"] | None = None
    universe_preset: UniversePreset | None = None
    tickers: list[str] = Field(default_factory=list)
    strategy_ids: list[str] = Field(default_factory=list)
    scan_timeframe: ScanTimeframe | None = None

    @field_validator("tickers", mode="before")
    @classmethod
    def _upper_tickers(cls, v: Any) -> Any:
        if not isinstance(v, list):
            return v
        return [str(t).strip().upper() for t in v if str(t).strip()]

    @field_validator("tickers")
    @classmethod
    def _tickers_shape(cls, v: list[str]) -> list[str]:
        for t in v:
            if not _TICKER_RE.match(t):
                raise ValueError(f"invalid ticker symbol: {t!r}")
        return v

    @field_validator("strategy_ids", mode="before")
    @classmethod
    def _lower_strategy_ids(cls, v: Any) -> Any:
        if not isinstance(v, list):
            return v
        return [str(s).strip().lower() for s in v if str(s).strip()]

    @field_validator("strategy_ids")
    @classmethod
    def _strategy_ids_known(cls, v: list[str]) -> list[str]:
        known = known_strategy_ids()
        unknown = [s for s in v if s not in known]
        if unknown:
            raise ValueError(f"unknown strategy_ids: {unknown}")
        return v

    @field_validator("universe_preset")
    @classmethod
    def _universe_available(cls, v: str | None) -> str | None:
        if v is None:
            return v
        key = str(v).strip().lower()
        if key not in known_universe_ids(available_only=True):
            raise ValueError(f"universe preset {v!r} is not available")
        return key  # type: ignore[return-value]

    @field_validator("scan_timeframe")
    @classmethod
    def _timeframe_known(cls, v: str | None) -> str | None:
        if v is None:
            return v
        key = str(v).strip().lower()
        if key not in known_timeframe_ids():
            raise ValueError(f"unknown scan_timeframe: {v!r}")
        return key  # type: ignore[return-value]

    @model_validator(mode="after")
    def _universe_consistency(self) -> ScanRunRequest:
        cap = scan_max_custom_tickers()
        custom = self.universe_preset == "custom" or self.universe_mode == "tickers"
        if custom:
            if not self.tickers:
                raise ValueError("tickers required when universe_mode is 'tickers' or universe_preset is 'custom'")
            if len(self.tickers) > cap:
                raise ValueError(f"at most {cap} tickers allowed for scan")
        return self


def parse_scan_run_body(raw: Any) -> dict[str, Any]:
    """
    Validate API body and return a Celery/json-safe dict:
    env_overrides, universe_mode (optional), tickers (optional),
    universe_preset, strategy_ids, scan_timeframe.

    Default behavior is server-side SP1500; custom universe requires
    universe_mode="tickers" (or universe_preset="custom") with explicit symbols.
    """
    if raw is None:
        return {}
    if isinstance(raw, dict) and not raw:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("scan body must be a JSON object")
    req = ScanRunRequest.model_validate(raw)
    strategy_ids = resolve_strategy_ids(list(req.strategy_ids), scan_timeframe=req.scan_timeframe)
    env = req.strategy_overrides.to_env_overrides() if req.strategy_overrides else {}
    extra_env = env_overrides_for_strategies(strategy_ids)
    for k, v in extra_env.items():
        env.setdefault(k, v)
    universe_mode = req.universe_mode
    if req.universe_preset == "custom":
        universe_mode = "tickers"
    out: dict[str, Any] = {
        "env_overrides": env,
        "universe_mode": universe_mode,
        "universe_preset": req.universe_preset,
        "tickers": list(req.tickers) if universe_mode == "tickers" else [],
        "strategy_ids": strategy_ids,
        "scan_timeframe": req.scan_timeframe,
    }
    return out


def scan_runtime_kwargs(parsed: dict[str, Any]) -> dict[str, Any]:
    """Map parse_scan_run_body output to scan_for_signals_detailed keyword args."""
    env_raw = parsed.get("env_overrides") or {}
    env_flat = {str(k): str(v) for k, v in env_raw.items()} if isinstance(env_raw, dict) else {}
    env_overrides = env_flat if env_flat else None
    um = parsed.get("universe_mode")
    tickers = parsed.get("tickers") or []
    watchlist_override = list(tickers) if um == "tickers" and tickers else None
    preset = parsed.get("universe_preset")
    if watchlist_override is not None:
        preset = "custom"
    elif not preset:
        preset = DEFAULT_UNIVERSE
    return {
        "env_overrides": env_overrides,
        "watchlist_override": watchlist_override,
        "universe_preset": preset,
        "strategy_ids": list(parsed.get("strategy_ids") or []),
        "scan_timeframe": parsed.get("scan_timeframe"),
    }
