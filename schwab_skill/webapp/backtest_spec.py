"""
Versioned StrategySpec for user backtests and strategy chat tools.

Maps to run_backtest(..., skill_dir=..., env_overrides=...) with strict caps.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_TICKER_RE = re.compile(r"^[A-Z0-9.\-]{1,16}$")


class StrategyOverrides(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quality_gates_mode: Literal["off", "shadow", "soft", "hard"] | None = None
    breakout_confirm_enabled: bool | None = None
    forensic_enabled: bool | None = None
    forensic_filter_mode: Literal["off", "shadow", "soft", "hard"] | None = None
    pead_enabled: bool | None = None
    skip_mirofish: bool | None = None

    def to_env_overrides(self) -> dict[str, str]:
        out: dict[str, str] = {}
        if self.quality_gates_mode is not None:
            out["QUALITY_GATES_MODE"] = self.quality_gates_mode
        if self.breakout_confirm_enabled is not None:
            out["BREAKOUT_CONFIRM_ENABLED"] = "true" if self.breakout_confirm_enabled else "false"
        if self.forensic_enabled is not None:
            out["FORENSIC_ENABLED"] = "true" if self.forensic_enabled else "false"
        if self.forensic_filter_mode is not None:
            out["FORENSIC_FILTER_MODE"] = self.forensic_filter_mode
        if self.pead_enabled is not None:
            out["PEAD_ENABLED"] = "true" if self.pead_enabled else "false"
        if self.skip_mirofish is not None:
            out["BACKTEST_SKIP_MIROFISH"] = "1" if self.skip_mirofish else "0"
        return out


class StrategySpecV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    theory_name: str | None = Field(default=None, max_length=200)
    universe_mode: Literal["watchlist", "tickers"]
    tickers: list[str] = Field(default_factory=list)
    start_date: str = Field(min_length=10, max_length=10)
    end_date: str = Field(min_length=10, max_length=10)
    slippage_bps_per_side: float = Field(default=15.0, ge=0.0, le=200.0)
    fee_per_share: float = Field(default=0.005, ge=0.0, le=1.0)
    min_fee_per_order: float = Field(default=1.0, ge=0.0, le=50.0)
    max_adv_participation: float = Field(default=0.02, ge=0.001, le=0.1)
    overrides: StrategyOverrides | None = None

    @field_validator("tickers", mode="before")
    @classmethod
    def _upper_tickers(cls, v: Any) -> Any:
        if not isinstance(v, list):
            return v
        return [str(t).strip().upper() for t in v if str(t).strip()]

    @field_validator("start_date", "end_date")
    @classmethod
    def _iso_date(cls, v: str) -> str:
        datetime.strptime(v, "%Y-%m-%d")
        return v

    @field_validator("tickers")
    @classmethod
    def _tickers_shape(cls, v: list[str]) -> list[str]:
        for t in v:
            if not _TICKER_RE.match(t):
                raise ValueError(f"invalid ticker symbol: {t!r}")
        return v

    @model_validator(mode="after")
    def _universe_and_range(self) -> StrategySpecV1:
        max_tickers = max(1, int(os.getenv("SAAS_BACKTEST_MAX_TICKERS", "40")))
        max_days = max(30, int(os.getenv("SAAS_BACKTEST_MAX_RANGE_DAYS", "3652")))
        min_days = max(1, int(os.getenv("SAAS_BACKTEST_MIN_RANGE_DAYS", "30")))

        if self.universe_mode == "tickers":
            if not self.tickers:
                raise ValueError("tickers required when universe_mode is 'tickers'")
            if len(self.tickers) > max_tickers:
                raise ValueError(f"at most {max_tickers} tickers allowed")
        else:
            self.tickers = []

        start = datetime.strptime(self.start_date, "%Y-%m-%d")
        end = datetime.strptime(self.end_date, "%Y-%m-%d")
        if end <= start:
            raise ValueError("end_date must be after start_date")
        span = (end - start).days
        if span < min_days:
            raise ValueError(f"date range must be at least {min_days} days")
        if span > max_days:
            raise ValueError(f"date range must be at most {max_days} days")
        return self

    def env_overrides_merged(self) -> dict[str, str] | None:
        if not self.overrides:
            return None
        o = self.overrides.to_env_overrides()
        return o or None


def parse_strategy_spec(raw: dict[str, Any]) -> StrategySpecV1:
    ver = raw.get("schema_version", 1)
    if ver != 1:
        raise ValueError(f"unsupported schema_version: {ver!r}")
    return StrategySpecV1.model_validate(raw)


def run_strategy_backtest(skill_dir: Path, spec: StrategySpecV1) -> dict[str, Any]:
    from backtest import run_backtest

    tickers: list[str] | None = spec.tickers if spec.universe_mode == "tickers" else None
    return run_backtest(
        tickers=tickers,
        start_date=spec.start_date,
        end_date=spec.end_date,
        slippage_bps_per_side=spec.slippage_bps_per_side,
        fee_per_share=spec.fee_per_share,
        min_fee_per_order=spec.min_fee_per_order,
        max_adv_participation=spec.max_adv_participation,
        skill_dir=skill_dir,
        env_overrides=spec.env_overrides_merged(),
    )


def spec_preview_dict(spec: StrategySpecV1) -> dict[str, Any]:
    return {
        "schema_version": spec.schema_version,
        "theory_name": spec.theory_name,
        "universe_mode": spec.universe_mode,
        "tickers": spec.tickers,
        "start_date": spec.start_date,
        "end_date": spec.end_date,
        "slippage_bps_per_side": spec.slippage_bps_per_side,
        "fee_per_share": spec.fee_per_share,
        "min_fee_per_order": spec.min_fee_per_order,
        "max_adv_participation": spec.max_adv_participation,
        "overrides": spec.overrides.model_dump(exclude_none=True) if spec.overrides else None,
        "env_overrides": spec.env_overrides_merged(),
    }
