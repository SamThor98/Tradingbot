"""
Optional JSON body for POST /api/scan: align live scan env/universe with backtest StrategySpec patterns.
"""

from __future__ import annotations

import os
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .backtest_spec import StrategyOverrides

_TICKER_RE = re.compile(r"^[A-Z0-9.\-]{1,16}$")


def scan_max_custom_tickers() -> int:
    return max(1, int(os.getenv("SAAS_SCAN_MAX_CUSTOM_TICKERS", os.getenv("SAAS_BACKTEST_MAX_TICKERS", "40"))))


class ScanRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_overrides: StrategyOverrides | None = None
    universe_mode: Literal["watchlist", "tickers"] | None = None
    tickers: list[str] = Field(default_factory=list)

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

    @model_validator(mode="after")
    def _universe_consistency(self) -> ScanRunRequest:
        cap = scan_max_custom_tickers()
        if self.universe_mode == "tickers":
            if not self.tickers:
                raise ValueError("tickers required when universe_mode is 'tickers'")
            if len(self.tickers) > cap:
                raise ValueError(f"at most {cap} tickers allowed for scan")
        return self


def parse_scan_run_body(raw: Any) -> dict[str, Any]:
    """
    Validate API body and return a Celery/json-safe dict:
    env_overrides, universe_mode (optional), tickers (optional).

    Default behavior is server-side SP1500; custom universe requires
    universe_mode="tickers" with explicit symbols.
    """
    if raw is None:
        return {}
    if isinstance(raw, dict) and not raw:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("scan body must be a JSON object")
    req = ScanRunRequest.model_validate(raw)
    out: dict[str, Any] = {
        "env_overrides": req.strategy_overrides.to_env_overrides() if req.strategy_overrides else {},
        "universe_mode": req.universe_mode,
        "tickers": list(req.tickers) if req.universe_mode == "tickers" else [],
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
    return {"env_overrides": env_overrides, "watchlist_override": watchlist_override}
