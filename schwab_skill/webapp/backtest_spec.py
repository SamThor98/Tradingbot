"""
Versioned StrategySpec for user backtests and strategy chat tools.

Maps to run_backtest(..., skill_dir=..., env_overrides=...) with strict caps.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.scan_catalog import (
    DEFAULT_UNIVERSE,
    default_strategy_ids_for_timeframe,
    env_overrides_for_strategies,
    known_strategy_ids,
    known_timeframe_ids,
    known_universe_ids,
    resolve_strategy_ids,
)

_TICKER_RE = re.compile(r"^[A-Z0-9.\-]{1,16}$")
_SP1500_PRESETS = frozenset({"sp1500", "watchlist", ""})
# Named indexes follow the catalog size. The live account focus stays on scans.
_FULL_INDEX_PRESETS = frozenset({"sp1500", "sp500", "nasdaq100", "sector_etfs"})


class StrategyOverrides(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quality_gates_mode: Literal["off", "shadow", "soft", "hard"] | None = None
    quality_min_signal_score: int | None = Field(default=None, ge=0, le=100)
    quality_require_breakout_volume: bool | None = None
    quality_breakout_volume_min_ratio: float | None = Field(default=None, ge=0.5, le=5.0)
    breakout_confirm_enabled: bool | None = None
    forensic_enabled: bool | None = None
    forensic_filter_mode: Literal["off", "shadow", "soft", "hard"] | None = None
    pead_enabled: bool | None = None
    skip_mirofish: bool | None = None
    # Universe selection (used by Run Scan vs Test Scan affordance).
    signal_universe_mode: Literal["broad", "focused"] | None = None
    signal_universe_target_size: int | None = Field(default=None, ge=20, le=1600)
    quality_watchlist_prefilter_enabled: bool | None = None
    quality_watchlist_prefilter_max: int | None = Field(default=None, ge=20, le=1600)
    backtest_portfolio_starting_equity: float | None = Field(default=None, gt=1000.0, le=5_000_000.0)
    backtest_portfolio_max_positions: int | None = Field(default=None, ge=1, le=100)
    backtest_position_size_pct: float | None = Field(default=None, ge=0.001, le=1.0)
    backtest_risk_per_trade_pct: float | None = Field(default=None, ge=0.0, le=0.1)
    adaptive_stop_enabled: bool | None = None
    adaptive_stop_base_pct: float | None = Field(default=None, ge=0.01, le=0.3)
    backtest_adaptive_guardrails_enabled: bool | None = None
    meta_policy_mode: Literal["off", "shadow", "live"] | None = None
    event_risk_mode: Literal["off", "shadow", "live"] | None = None
    exit_manager_mode: Literal["off", "shadow", "live"] | None = None
    exec_quality_mode: Literal["off", "shadow", "live"] | None = None
    stage2_52w_pct: float | None = Field(default=None, ge=0.5, le=1.0)
    stage2_sma_upward_days: int | None = Field(default=None, ge=1, le=120)
    vcp_days: int | None = Field(default=None, ge=1, le=30)
    scan_vcp_gate_mode: Literal["hard", "shadow"] | None = None
    backtest_hold_days: int | None = Field(default=None, ge=1, le=120)
    backtest_min_hold_days_before_trail: int | None = Field(default=None, ge=0, le=60)
    rank_filter_shadow_min_percentile_rank_v2: int | None = Field(default=None, ge=0, le=95)

    def to_env_overrides(self) -> dict[str, str]:
        out: dict[str, str] = {}
        if self.quality_gates_mode is not None:
            out["QUALITY_GATES_MODE"] = self.quality_gates_mode
        if self.quality_min_signal_score is not None:
            out["QUALITY_MIN_SIGNAL_SCORE"] = str(int(self.quality_min_signal_score))
        if self.quality_require_breakout_volume is not None:
            out["QUALITY_REQUIRE_BREAKOUT_VOLUME"] = (
                "true" if self.quality_require_breakout_volume else "false"
            )
        if self.quality_breakout_volume_min_ratio is not None:
            out["QUALITY_BREAKOUT_VOLUME_MIN_RATIO"] = str(float(self.quality_breakout_volume_min_ratio))
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
        if self.signal_universe_mode is not None:
            out["SIGNAL_UNIVERSE_MODE"] = self.signal_universe_mode
        if self.signal_universe_target_size is not None:
            out["SIGNAL_UNIVERSE_TARGET_SIZE"] = str(int(self.signal_universe_target_size))
        if self.quality_watchlist_prefilter_enabled is not None:
            out["QUALITY_WATCHLIST_PREFILTER_ENABLED"] = (
                "true" if self.quality_watchlist_prefilter_enabled else "false"
            )
        if self.quality_watchlist_prefilter_max is not None:
            out["QUALITY_WATCHLIST_PREFILTER_MAX"] = str(int(self.quality_watchlist_prefilter_max))
        if self.backtest_portfolio_starting_equity is not None:
            out["BACKTEST_PORTFOLIO_STARTING_EQUITY"] = str(float(self.backtest_portfolio_starting_equity))
        if self.backtest_portfolio_max_positions is not None:
            out["BACKTEST_PORTFOLIO_MAX_POSITIONS"] = str(int(self.backtest_portfolio_max_positions))
        if self.backtest_position_size_pct is not None:
            out["BACKTEST_POSITION_SIZE_PCT"] = str(float(self.backtest_position_size_pct))
        if self.backtest_risk_per_trade_pct is not None:
            out["BACKTEST_RISK_PER_TRADE_PCT"] = str(float(self.backtest_risk_per_trade_pct))
        if self.adaptive_stop_enabled is not None:
            out["ADAPTIVE_STOP_ENABLED"] = "true" if self.adaptive_stop_enabled else "false"
        if self.adaptive_stop_base_pct is not None:
            out["ADAPTIVE_STOP_BASE_PCT"] = str(float(self.adaptive_stop_base_pct))
        if self.backtest_adaptive_guardrails_enabled is not None:
            out["BACKTEST_ADAPTIVE_GUARDRAILS_ENABLED"] = (
                "true" if self.backtest_adaptive_guardrails_enabled else "false"
            )
        if self.meta_policy_mode is not None:
            out["META_POLICY_MODE"] = self.meta_policy_mode
        if self.event_risk_mode is not None:
            out["EVENT_RISK_MODE"] = self.event_risk_mode
        if self.exit_manager_mode is not None:
            out["EXIT_MANAGER_MODE"] = self.exit_manager_mode
        if self.exec_quality_mode is not None:
            out["EXEC_QUALITY_MODE"] = self.exec_quality_mode
        if self.stage2_52w_pct is not None:
            out["STAGE2_52W_PCT"] = str(float(self.stage2_52w_pct))
        if self.stage2_sma_upward_days is not None:
            out["STAGE2_SMA_UPWARD_DAYS"] = str(int(self.stage2_sma_upward_days))
        if self.vcp_days is not None:
            out["VCP_DAYS"] = str(int(self.vcp_days))
        if self.scan_vcp_gate_mode is not None:
            out["SCAN_VCP_GATE_MODE"] = self.scan_vcp_gate_mode
        if self.backtest_hold_days is not None:
            out["BACKTEST_HOLD_DAYS"] = str(int(self.backtest_hold_days))
        if self.backtest_min_hold_days_before_trail is not None:
            out["BACKTEST_MIN_HOLD_DAYS_BEFORE_TRAIL"] = str(int(self.backtest_min_hold_days_before_trail))
        if self.rank_filter_shadow_min_percentile_rank_v2 is not None:
            out["RANK_FILTER_SHADOW_MIN_PERCENTILE_RANK_V2"] = str(
                int(self.rank_filter_shadow_min_percentile_rank_v2)
            )
        return out


class StrategySpecV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    theory_name: str | None = Field(default=None, max_length=200)
    universe_mode: Literal["watchlist", "tickers"]
    universe_preset: str | None = None
    tickers: list[str] = Field(default_factory=list)
    strategy_ids: list[str] = Field(default_factory=list)
    scan_timeframe: str | None = None
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

    @field_validator("universe_preset", mode="before")
    @classmethod
    def _normalize_universe_preset(cls, v: Any) -> Any:
        if v is None:
            return v
        key = str(v).strip().lower()
        if not key:
            return None
        if key in _SP1500_PRESETS:
            return "sp1500"
        if key in {"tickers"}:
            return "custom"
        return key

    @field_validator("universe_preset")
    @classmethod
    def _universe_available(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if v not in known_universe_ids(available_only=True):
            raise ValueError(f"universe preset {v!r} is not available")
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

    @field_validator("scan_timeframe")
    @classmethod
    def _timeframe_known(cls, v: str | None) -> str | None:
        if v is None or str(v).strip() == "":
            return None
        key = str(v).strip().lower()
        if key not in known_timeframe_ids():
            raise ValueError(f"unknown scan_timeframe: {v!r}")
        return key

    @model_validator(mode="after")
    def _universe_and_range(self) -> StrategySpecV1:
        max_tickers = max(1, int(os.getenv("SAAS_BACKTEST_MAX_TICKERS", "40")))
        max_days = max(30, int(os.getenv("SAAS_BACKTEST_MAX_RANGE_DAYS", "3652")))
        min_days = max(1, int(os.getenv("SAAS_BACKTEST_MIN_RANGE_DAYS", "30")))

        preset = self.universe_preset
        if preset is None:
            preset = "custom" if self.universe_mode == "tickers" else DEFAULT_UNIVERSE
            self.universe_preset = preset
        custom = preset == "custom" or self.universe_mode == "tickers"
        if custom:
            self.universe_mode = "tickers"
            self.universe_preset = "custom"
            if not self.tickers:
                raise ValueError("tickers required when universe_mode is 'tickers' or universe_preset is 'custom'")
            if len(self.tickers) > max_tickers:
                raise ValueError(f"at most {max_tickers} tickers allowed")
        else:
            self.universe_mode = "watchlist"
            self.tickers = []

        if self.strategy_ids:
            self.strategy_ids = resolve_strategy_ids(list(self.strategy_ids), scan_timeframe=self.scan_timeframe)
        elif self.scan_timeframe:
            self.strategy_ids = default_strategy_ids_for_timeframe(self.scan_timeframe)

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
        o: dict[str, str] = {}
        if self.overrides:
            o.update(self.overrides.to_env_overrides())
        extra = env_overrides_for_strategies(list(self.strategy_ids or []))
        for key, val in extra.items():
            o.setdefault(str(key), str(val))
        preset = str(self.universe_preset or "")
        if self.universe_mode != "tickers" and preset in _FULL_INDEX_PRESETS and "SIGNAL_UNIVERSE_MODE" not in o:
            o["SIGNAL_UNIVERSE_MODE"] = "broad"
        return o or None


def parse_strategy_spec(raw: dict[str, Any]) -> StrategySpecV1:
    ver = raw.get("schema_version", 1)
    if ver != 1:
        raise ValueError(f"unsupported schema_version: {ver!r}")
    return StrategySpecV1.model_validate(raw)


def run_strategy_backtest(
    skill_dir: Path,
    spec: StrategySpecV1,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    from backtest import run_backtest

    tickers: list[str] | None = spec.tickers if spec.universe_mode == "tickers" else None
    preset = spec.universe_preset if spec.universe_mode != "tickers" else None
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
        universe_preset=preset,
        strategy_ids=list(spec.strategy_ids or []) or None,
        on_progress=on_progress,
    )


def spec_preview_dict(spec: StrategySpecV1) -> dict[str, Any]:
    return {
        "schema_version": spec.schema_version,
        "theory_name": spec.theory_name,
        "universe_mode": spec.universe_mode,
        "universe_preset": spec.universe_preset,
        "tickers": spec.tickers,
        "strategy_ids": list(spec.strategy_ids or []),
        "scan_timeframe": spec.scan_timeframe,
        "start_date": spec.start_date,
        "end_date": spec.end_date,
        "slippage_bps_per_side": spec.slippage_bps_per_side,
        "fee_per_share": spec.fee_per_share,
        "min_fee_per_order": spec.min_fee_per_order,
        "max_adv_participation": spec.max_adv_participation,
        "overrides": spec.overrides.model_dump(exclude_none=True) if spec.overrides else None,
        "env_overrides": spec.env_overrides_merged(),
    }
