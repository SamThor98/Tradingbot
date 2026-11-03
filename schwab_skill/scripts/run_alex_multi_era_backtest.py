"""Five-era Alex-native backtest on the 717-name universe.

Same eras and Schwab-only data contract as
``run_multi_era_backtest_schwab_only.py``. Unlike Stage 2 / pullback / PEAD,
this keeps Alex exits and runs the full 717 as **one book** (max 12 slots).
Do not chunk independent books — that would inflate capacity.

Usage (from schwab_skill/)::

    python scripts/run_alex_multi_era_backtest.py --run-tag alex_native_717
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from backtest import _fetch_history_with_meta  # noqa: E402
from core.alex_strategy import (  # noqa: E402
    DEFAULT_UNIVERSE_PATH,
    load_alex_params,
    load_alex_universe,
    run_alex_backtest,
)
from logger_setup import get_logger, setup_logging  # noqa: E402
from schwab_auth import DualSchwabAuth  # noqa: E402

LOG = logging.getLogger("run_alex_multi_era_backtest")

ERAS: list[tuple[str, str | None, str]] = [
    ("2024-01-01", None, "recent_current"),
    ("2022-01-01", "2023-12-31", "bear_rates"),
    ("2020-01-01", "2021-12-31", "crash_recovery"),
    ("2018-01-01", "2019-12-31", "volatility_chop"),
    ("2015-01-01", "2017-12-31", "late_bull"),
]

FETCH_START = "2014-01-01"
MAX_EMPTY_RATIO = 0.90


def _artifact_dir() -> Path:
    raw = (os.getenv("BACKTEST_ARTIFACT_DIR") or "").strip()
    return Path(raw) if raw else (SKILL_DIR / "validation_artifacts")


def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Alex-native five-era backtest on the 717-name universe")
    p.add_argument("--run-tag", default="alex_native_717")
    p.add_argument("--universe", default=str(DEFAULT_UNIVERSE_PATH))
    p.add_argument("--max-workers", type=int, default=1, help="Parallel Schwab history fetches (1 avoids token races)")
    p.add_argument("--no-resume", action="store_true")
    p.add_argument("--verbose", "-v", action="store_true")
    return p.parse_args()


def _auth_preflight() -> bool:
    os.environ.setdefault("SCHWAB_ONLY_DATA", "true")
    if (os.getenv("MULTI_ERA_SKIP_AUTH_PREFLIGHT") or "").strip().lower() in {"1", "true", "yes", "on"}:
        LOG.info("Auth preflight skipped via MULTI_ERA_SKIP_AUTH_PREFLIGHT")
        return True
    try:
        from scripts.resume_signal_edge_after_auth import _schwab_refresh_ok
    except ImportError:
        from resume_signal_edge_after_auth import _schwab_refresh_ok
    ok, msg = _schwab_refresh_ok()
    if ok:
        LOG.info("Schwab auth preflight OK: %s", msg)
        return True
    LOG.error("Schwab auth preflight FAILED: %s", msg)
    return False


def _cache_path(cache_dir: Path, symbol: str) -> Path:
    safe = "".join(c if c.isalnum() else "_" for c in symbol)
    return cache_dir / f"{safe}.parquet"


def _load_cached(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
    except Exception:
        return None
    if df is None or df.empty:
        return None
    if "date" in df.columns:
        df = df.set_index("date")
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df.index.name = "date"
    return df.sort_index()


def _save_cached(path: Path, df: pd.DataFrame) -> None:
    out = df.copy()
    out.index.name = "date"
    path.parent.mkdir(parents=True, exist_ok=True)
    out.reset_index().to_parquet(path, index=False)


def _fetch_one(
    symbol: str,
    *,
    start_date: str,
    end_date: str,
    cache_dir: Path,
    auth: DualSchwabAuth | None,
    resume: bool,
) -> tuple[str, pd.DataFrame | None, dict[str, Any]]:
    path = _cache_path(cache_dir, symbol)
    if resume:
        cached = _load_cached(path)
        if cached is not None and len(cached) >= 80:
            return symbol, cached, {"provider": "cache", "rows": int(len(cached)), "reason": "cache_hit"}
    df, meta = _fetch_history_with_meta(symbol, start_date, end_date, schwab_auth=auth)
    if df is None or df.empty:
        return symbol, None, meta
    _save_cached(path, df)
    return symbol, df, meta


def _fetch_universe(
    symbols: list[str],
    *,
    start_date: str,
    end_date: str,
    cache_dir: Path,
    max_workers: int,
    resume: bool,
) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    os.environ.setdefault("SCHWAB_ONLY_DATA", "true")
    auth = DualSchwabAuth(skill_dir=SKILL_DIR, auto_refresh=False)
    integrity: dict[str, Any] = {
        "history_fetch_total": 0,
        "history_fetch_empty": 0,
        "history_provider_schwab": 0,
        "history_provider_cache": 0,
        "history_fallback_used": 0,
    }
    frames: dict[str, pd.DataFrame] = {}
    workers = max(1, int(max_workers))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [
            pool.submit(
                _fetch_one,
                symbol,
                start_date=start_date,
                end_date=end_date,
                cache_dir=cache_dir,
                auth=auth,
                resume=resume,
            )
            for symbol in symbols
        ]
        done = 0
        for fut in as_completed(futs):
            symbol, df, meta = fut.result()
            done += 1
            integrity["history_fetch_total"] += 1
            provider = str(meta.get("provider") or "unknown")
            if provider == "cache":
                integrity["history_provider_cache"] += 1
            elif provider == "schwab":
                integrity["history_provider_schwab"] += 1
            if meta.get("used_fallback"):
                integrity["history_fallback_used"] += 1
            if df is None or df.empty:
                integrity["history_fetch_empty"] += 1
                LOG.warning("Empty history for %s (%s)", symbol, meta.get("reason"))
            else:
                frames[symbol] = df
            if done % 25 == 0 or done == len(symbols):
                LOG.info(
                    "Fetched %s/%s (%s empty, %s cache hits)",
                    done,
                    len(symbols),
                    integrity["history_fetch_empty"],
                    integrity["history_provider_cache"],
                )
    return frames, integrity


def _coerce_pf(value: Any) -> float | None:
    if value in (None, "inf"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _era_row(name: str, start: str, end: str | None, result: dict[str, Any], universe_size: int) -> dict[str, Any]:
    pf_net = result.get("profit_factor_net")
    return {
        "era": name,
        "start": start,
        "end": end,
        "profit_factor_net": pf_net,
        "profit_factor_dollar": result.get("profit_factor"),
        "total_return_net_pct": result.get("total_return_pct"),
        "max_drawdown_net_pct": result.get("max_drawdown_pct"),
        "win_rate_net": result.get("win_rate_net"),
        "total_trades": result.get("total_trades"),
        "ending_equity": result.get("ending_equity"),
        "universe_size": universe_size,
        "symbols_loaded": result.get("symbols_loaded"),
        "diagnostics": result.get("diagnostics"),
    }


def _gate_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pfs = [v for v in (_coerce_pf(r.get("profit_factor_net")) for r in rows) if v is not None]
    if not pfs:
        return {"pf_mean": 0.0, "worst_era_pf": 0.0, "eras": 0, "clears_pf_120": False, "clears_worst_era_100": False}
    mean = sum(pfs) / len(pfs)
    worst = min(pfs)
    return {
        "pf_mean": round(mean, 3),
        "worst_era_pf": round(worst, 3),
        "eras": len(pfs),
        "clears_pf_120": mean >= 1.20,
        "clears_worst_era_100": worst >= 1.00,
    }


def main() -> int:
    args = _parse()
    setup_logging(level=logging.DEBUG if args.verbose else logging.INFO)
    get_logger(__name__)
    os.environ.setdefault("SCHWAB_ONLY_DATA", "true")

    if not _auth_preflight():
        print("Refusing to launch: Schwab auth preflight failed.")
        return 5

    art = _artifact_dir()
    run_id = str(args.run_tag)
    cache_dir = art / "multi_era_chunks" / run_id / "price_cache"
    era_dir = art / "multi_era_chunks" / run_id
    era_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    resume = not args.no_resume

    universe = load_alex_universe(Path(args.universe))
    params = load_alex_params(SKILL_DIR)
    end_today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    symbols = list(universe.keys()) + ["SPY", "SHY"]
    LOG.info(
        "Alex native multi-era: run_id=%s universe=%s fetch=%s to %s entry_mode=%s",
        run_id,
        len(universe),
        FETCH_START,
        end_today,
        params.entry_mode,
    )

    frames, integrity = _fetch_universe(
        symbols,
        start_date=FETCH_START,
        end_date=end_today,
        cache_dir=cache_dir,
        max_workers=args.max_workers,
        resume=resume,
    )
    empty_ratio = float(integrity["history_fetch_empty"]) / max(1, int(integrity["history_fetch_total"]))
    if empty_ratio >= MAX_EMPTY_RATIO:
        LOG.error("Data outage fail-closed: empty_ratio=%.3f", empty_ratio)
        return 4
    spy = frames.pop("SPY", pd.DataFrame())
    shy = frames.pop("SHY", pd.DataFrame())
    if spy is None or spy.empty or shy is None or shy.empty:
        LOG.error("SPY or SHY history missing; fail-closed")
        return 4
    price_data = {t: frames[t] for t in universe if t in frames}
    LOG.info("Loaded %s/%s universe names plus SPY/SHY", len(price_data), len(universe))

    completed: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for start, end, name in ERAS:
        era_path = era_dir / f"{name}.json"
        if resume and era_path.exists():
            try:
                completed.append(json.loads(era_path.read_text(encoding="utf-8")))
                LOG.info("Resuming cached era %s", name)
                continue
            except Exception:
                pass
        era_end = end or end_today
        LOG.info("Running era %s (%s to %s)", name, start, era_end)
        result = run_alex_backtest(
            price_data,
            universe=universe,
            spy=spy,
            shy=shy,
            params=params,
            start_date=start,
            end_date=era_end,
        )
        result["symbols_loaded"] = len(price_data)
        row = _era_row(name, start, end, result, universe_size=len(universe))
        era_payload = {**row, "trades": result.get("trades") or []}
        era_path.write_text(json.dumps(era_payload, indent=2, default=str), encoding="utf-8")
        completed.append(row)
        LOG.info(
            "Completed %s: trades=%s pf_net=%s wr_net=%s return_pct=%s",
            name,
            row.get("total_trades"),
            row.get("profit_factor_net"),
            row.get("win_rate_net"),
            row.get("total_return_net_pct"),
        )

    gates = _gate_summary(completed)
    payload = {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "schwab_only": True,
        "strategy": "alex_native",
        "universe": "alex_717",
        "universe_size": len(universe),
        "symbols_loaded": len(price_data),
        "entry_mode": params.entry_mode,
        "starting_equity": params.starting_equity,
        "data_integrity": integrity,
        "results": completed,
        "failed_eras": failed,
        "gates": gates,
    }
    out = art / f"multi_era_backtest_schwab_only_{run_id}.json"
    out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    LOG.info(
        "Alex native gates: pf_mean=%s worst_era_pf=%s clears_1.20=%s clears_worst_1.00=%s",
        gates["pf_mean"],
        gates["worst_era_pf"],
        gates["clears_pf_120"],
        gates["clears_worst_era_100"],
    )
    LOG.info("Wrote %s", out)
    print(json.dumps({"artifact": str(out), "gates": gates, "eras": completed}, indent=2, default=str))
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
