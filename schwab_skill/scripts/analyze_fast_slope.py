#!/usr/bin/env python3
"""Screen fast-average slopes on the frozen Stage 2 book.

Does not change the scanner and does not run a new multi-era backtest.
It measures, on the entry bar of each stored trade, the slope of a short
EMA, a Wilder EMA, a 10-day volume-weighted average, and an anchored VWAP.
Those values are scored against the trade's realized net return.

The stored ``ohlc_path`` starts on the entry day, so it has no lookback.
Bars come from a local cache under ``validation_artifacts/fast_slope/price_cache``,
filled from yfinance (auto-adjusted) when ``--fetch`` is on. One provider is
used for every name so a mixed adjustment convention cannot create the slope.

Usage (from schwab_skill/):
  python scripts/analyze_fast_slope.py
  python scripts/analyze_fast_slope.py --no-fetch
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from research.counterfactual import _era_pf_mean_worst, _safe_pf, select_by_percentile  # noqa: E402
from research.fast_slope import (  # noqa: E402
    FEATURE_COLUMNS,
    MIN_BARS,
    compute_fast_slope_frame,
    promotion_verdict,
)
from scripts.analyze_scan_confidence import MIN_COVERAGE, screen_feature  # noqa: E402
from scripts.compare_confidence_across_books import _load_book  # noqa: E402

ART = SKILL_DIR / "validation_artifacts" / "fast_slope"
LABELED = (
    SKILL_DIR
    / "validation_artifacts"
    / "prob_rank_control_confirm"
    / "scored_features_control_legacy_aug_labeled.parquet"
)
FETCH_START = "2014-06-01"
STEEP_PERCENTILE = 75.0
ASOF_TOLERANCE = pd.Timedelta("7D")
ALIGNMENT_FLOOR = 0.85
RUN_ID = "control_legacy_aug"

CONTROL_COLUMNS = ("sma_20_slope", "sma20_slope_10", "panel_sma_20_slope", "panel_ret_5d_prev")


def _cache_dir() -> Path:
    path = ART / "price_cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_path(ticker: str) -> Path:
    safe = "".join(ch if ch.isalnum() else "_" for ch in ticker.upper())
    return _cache_dir() / f"{safe}.parquet"


def _read_cache(ticker: str) -> pd.DataFrame | None:
    path = _cache_path(ticker)
    if not path.is_file():
        return None
    try:
        frame = pd.read_parquet(path)
    except Exception:
        return None
    if frame is None or frame.empty:
        return None
    if "date" in frame.columns:
        frame = frame.set_index("date")
    frame.index = pd.to_datetime(frame.index).tz_localize(None).normalize()
    frame = frame.sort_index()
    frame = frame[~frame.index.duplicated(keep="last")]
    return frame


def _write_cache(ticker: str, frame: pd.DataFrame) -> None:
    out = frame.copy()
    out.index.name = "date"
    out.reset_index().to_parquet(_cache_path(ticker), index=False)


def _normalize_downloaded(raw: pd.DataFrame) -> pd.DataFrame | None:
    if raw is None or raw.empty:
        return None
    renamed = raw.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "volume": "volume",
        }
    )
    needed = ["open", "high", "low", "close", "volume"]
    if any(col not in renamed.columns for col in needed):
        return None
    out = renamed[needed].apply(pd.to_numeric, errors="coerce")
    out = out.dropna(subset=["close"])
    out = out[out["close"] > 0]
    if out.empty:
        return None
    out.index = pd.to_datetime(out.index).tz_localize(None).normalize()
    out.index.name = "date"
    return out.sort_index()


def _split_download(raw: pd.DataFrame, tickers: list[str]) -> dict[str, pd.DataFrame]:
    if raw is None or raw.empty:
        return {}
    frames: dict[str, pd.DataFrame] = {}
    if not isinstance(raw.columns, pd.MultiIndex):
        if len(tickers) == 1:
            normalized = _normalize_downloaded(raw)
            if normalized is not None:
                frames[tickers[0]] = normalized
        return frames
    levels = [set(map(str, raw.columns.get_level_values(i))) for i in range(raw.columns.nlevels)]
    ticker_level = 0 if any(ticker in levels[0] for ticker in tickers) else 1
    for ticker in tickers:
        if ticker not in raw.columns.get_level_values(ticker_level):
            continue
        normalized = _normalize_downloaded(raw.xs(ticker, axis=1, level=ticker_level))
        if normalized is not None:
            frames[ticker] = normalized
    return frames


def _cache_covers(frame: pd.DataFrame | None, needed_end: pd.Timestamp) -> bool:
    if frame is None or frame.empty or len(frame) < MIN_BARS:
        return False
    return pd.Timestamp(frame.index.max()) >= needed_end - ASOF_TOLERANCE


def fetch_missing(tickers: list[str], needed_end: dict[str, pd.Timestamp], *, batch_size: int) -> dict[str, int]:
    """Fill the local cache from yfinance. Returns counts of fetched and failed names."""
    import yfinance as yf

    missing = [ticker for ticker in tickers if not _cache_covers(_read_cache(ticker), needed_end[ticker])]
    end = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()
    fetched = 0
    failed: list[str] = []
    print(f"Price cache: {len(tickers) - len(missing)} ready, {len(missing)} to fetch", flush=True)
    for offset in range(0, len(missing), batch_size):
        batch = missing[offset : offset + batch_size]
        try:
            raw = yf.download(
                batch,
                start=FETCH_START,
                end=end,
                auto_adjust=True,
                group_by="ticker",
                threads=True,
                progress=False,
            )
        except Exception as exc:
            print(f"  batch failed ({batch[0]}..): {exc}", flush=True)
            failed.extend(batch)
            continue
        got = _split_download(raw, batch)
        for ticker in batch:
            frame = got.get(ticker)
            if frame is None or len(frame) < MIN_BARS:
                failed.append(ticker)
                continue
            _write_cache(ticker, frame)
            fetched += 1
        print(f"  fetched {min(offset + batch_size, len(missing))}/{len(missing)}", flush=True)
    return {"fetched": fetched, "failed": len(failed), "failed_sample": failed[:20]}


def _attach_panel(trades: pd.DataFrame) -> pd.DataFrame:
    if not LABELED.is_file():
        trades["panel_sma_20_slope"] = np.nan
        trades["panel_ret_5d_prev"] = np.nan
        trades["ret_40d_fwd"] = np.nan
        return trades
    panel = pd.read_parquet(
        LABELED,
        columns=["asof_date", "ticker", "sma_20_slope", "ret_5d_prev", "ret_40d_fwd"],
    )
    panel["entry_date"] = pd.to_datetime(panel["asof_date"], errors="coerce").dt.normalize()
    panel["ticker"] = panel["ticker"].astype(str).str.upper()
    panel = panel.dropna(subset=["entry_date"]).drop_duplicates(subset=["ticker", "entry_date"], keep="last")
    panel = panel.rename(
        columns={
            "sma_20_slope": "panel_sma_20_slope",
            "ret_5d_prev": "panel_ret_5d_prev",
        }
    )
    keep = ["ticker", "entry_date", "panel_sma_20_slope", "panel_ret_5d_prev", "ret_40d_fwd"]
    out = trades.copy()
    out["entry_date"] = pd.to_datetime(out["entry_date"], errors="coerce").dt.normalize()
    out["ticker"] = out["ticker"].astype(str).str.upper()
    return out.merge(panel[keep], on=["ticker", "entry_date"], how="left")


def _features_for_trades(trades: pd.DataFrame) -> pd.DataFrame:
    pieces: list[pd.DataFrame] = []
    grouped = list(trades.groupby("ticker", sort=False))
    for i, (ticker, grp) in enumerate(grouped, start=1):
        bars = _read_cache(str(ticker))
        left = grp.sort_values("entry_date").copy()
        left["entry_date"] = pd.to_datetime(left["entry_date"]).astype("datetime64[ns]")
        if bars is None or len(bars) < MIN_BARS:
            pieces.append(left)
        else:
            features = compute_fast_slope_frame(bars).reset_index()
            date_col = "date" if "date" in features.columns else features.columns[0]
            features = features.rename(columns={date_col: "bar_date"})
            features["bar_date"] = pd.to_datetime(features["bar_date"]).astype("datetime64[ns]")
            features = features.sort_values("bar_date")
            merged = pd.merge_asof(
                left,
                features,
                left_on="entry_date",
                right_on="bar_date",
                direction="backward",
                tolerance=ASOF_TOLERANCE,
            )
            pieces.append(merged)
        if i % 200 == 0:
            print(f"  features {i}/{len(grouped)}", flush=True)
    out = pd.concat(pieces, ignore_index=True)
    for col in FEATURE_COLUMNS:
        if col not in out.columns:
            out[col] = np.nan
    return out


def _arm(name: str, part: pd.DataFrame, total: int) -> dict[str, Any]:
    pf_mean, worst = _era_pf_mean_worst(part)
    return {
        "arm": name,
        "n": int(len(part)),
        "retention": round(len(part) / max(1, total), 4),
        "pf_mean": pf_mean,
        "worst_era_pf": worst,
        "pf_pooled": _safe_pf(part["net_return"]) if not part.empty else None,
    }


def _feature_block(df: pd.DataFrame, col: str) -> dict[str, Any]:
    covered = df.dropna(subset=[col]).copy()
    screen = screen_feature(df, col)
    baseline = _arm("baseline_covered", covered, len(df))
    steep = select_by_percentile(covered, score_col=col, min_percentile=STEEP_PERCENTILE)
    inverted = covered.copy()
    inverted["_inv"] = -pd.to_numeric(inverted[col], errors="coerce")
    flat = select_by_percentile(inverted, score_col="_inv", min_percentile=STEEP_PERCENTILE)
    steep_arm = _arm("steep_p75", steep, len(covered) if len(covered) else len(df))
    flat_arm = _arm("flat_p75", flat, len(covered) if len(covered) else len(df))
    steep_verdict = promotion_verdict(steep_arm.get("pf_mean"), steep_arm.get("worst_era_pf"), baseline.get("pf_mean"))
    flat_verdict = promotion_verdict(flat_arm.get("pf_mean"), flat_arm.get("worst_era_pf"), baseline.get("pf_mean"))
    if float(screen.get("coverage") or 0.0) < MIN_COVERAGE:
        steep_verdict = "inconclusive"
        flat_verdict = "inconclusive"
    return {
        "screen": screen,
        "baseline": baseline,
        "steep": steep_arm,
        "flat": flat_arm,
        "steep_verdict": steep_verdict,
        "flat_verdict": flat_verdict,
        "lift_steep": _lift(steep_arm.get("pf_mean"), baseline.get("pf_mean")),
        "lift_flat": _lift(flat_arm.get("pf_mean"), baseline.get("pf_mean")),
    }


def _lift(arm_pf: float | None, baseline_pf: float | None) -> float | None:
    if arm_pf is None or baseline_pf is None:
        return None
    return round(float(arm_pf) - float(baseline_pf), 4)


def _fmt(value: Any) -> str:
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return "—"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _alignment(df: pd.DataFrame) -> dict[str, Any]:
    panel = pd.to_numeric(df.get("panel_sma_20_slope"), errors="coerce")
    coverage = float(panel.notna().mean()) if len(df) else 0.0
    both = df[["sma_20_slope", "panel_sma_20_slope"]].apply(pd.to_numeric, errors="coerce").dropna()
    if coverage < 0.5 or len(both) < 50:
        return {"n": int(len(both)), "spearman": None, "aligned": False, "applicable": False}
    corr = float(both["sma_20_slope"].corr(both["panel_sma_20_slope"], method="spearman"))
    return {
        "n": int(len(both)),
        "spearman": round(corr, 4),
        "aligned": bool(corr >= ALIGNMENT_FLOOR),
        "applicable": True,
    }


def _alignment_sentence(align: dict[str, Any]) -> str:
    if not align.get("applicable", True):
        return (
            "The labeled research panel does not cover this book, so there is no panel alignment check. "
            "Slopes are still computed from the same daily cache as the control screen."
        )
    agreed = (
        "The two series agree, so this screen is on the same prices as the earlier slope study."
        if align.get("aligned")
        else "The two series do not agree. Treat these profit factors as a separate measurement, not a rerun of the labeled-panel study."
    )
    return (
        "Alignment check: recomputed `sma_20_slope` versus the labeled research panel "
        f"(Spearman {_fmt(align.get('spearman'))} on {align.get('n')} trades). "
        + agreed
    )


def _quintile_line(screen: dict[str, Any]) -> str:
    parts = []
    for row in screen.get("quintiles") or []:
        parts.append(f"Q{row['quintile']} {_fmt(row.get('pf'))} (n={row['n']})")
    return "; ".join(parts) if parts else "—"


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    align = payload["alignment"]
    fetch = payload["fetch"]
    lines = [
        "# Fast-average slope screen",
        "",
        f"Run `{payload['run_id']}` on {payload['generated_at']}. "
        f"{payload['n_trades']} stored trades, {payload['n_tickers']} tickers. "
        "No scanner change. No new multi-era backtest.",
        "",
        "Steep means the top quartile of that feature (75th percentile and above). "
        "Flat means the bottom quartile. Profit factor is the mean across eras on the "
        "trades that have the feature. A candidate must clear mean PF 1.20, worst-era PF 1.00, "
        "and a lift of at least 0.03 versus its own covered baseline. Coverage below "
        f"{MIN_COVERAGE:.0%} is inconclusive.",
        "",
        "## Price source",
        "",
        f"Cache fills: fetched {fetch.get('fetched', 0)}, failed {fetch.get('failed', 0)}. "
        f"History starts {FETCH_START}, yfinance auto-adjusted daily bars. "
        "Features use the last bar on or before the entry date, within 7 days.",
        "",
        _alignment_sentence(align),
        "",
        "## Results",
        "",
        "| Feature | Role | Coverage | IC vs net | IC vs 40d | Steep PF / worst | Flat PF / worst | Steep lift | Flat lift | Steep | Flat |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for name, block in payload["features"].items():
        screen = block["screen"]
        role = "control" if name in CONTROL_COLUMNS else "candidate"
        lines.append(
            "| `{name}` | {role} | {cov:.2f} | {ic} | {fwd} | {steep} / {steep_w} | {flat} / {flat_w} | {ls} | {lf} | {sv} | {fv} |".format(
                name=name,
                role=role,
                cov=float(screen.get("coverage") or 0.0),
                ic=_fmt(screen.get("ic_net_return")),
                fwd=_fmt(screen.get("ic_ret_40d_fwd")),
                steep=_fmt(block["steep"].get("pf_mean")),
                steep_w=_fmt(block["steep"].get("worst_era_pf")),
                flat=_fmt(block["flat"].get("pf_mean")),
                flat_w=_fmt(block["flat"].get("worst_era_pf")),
                ls=_fmt(block.get("lift_steep")),
                lf=_fmt(block.get("lift_flat")),
                sv=block["steep_verdict"],
                fv=block["flat_verdict"],
            )
        )
    lines.extend(["", "## Quintile profit factor", "", "Q0 is the lowest value of the feature. Q4 is the highest.", ""])
    for name, block in payload["features"].items():
        lines.append(f"- `{name}`: {_quintile_line(block['screen'])}")
    lines.extend(
        [
            "",
            "## Reading",
            "",
            payload["reading"],
            "",
            "Session VWAP is not in this screen. These are daily bars, and a session VWAP is one point per day. "
            "`vwma10` is the volume-weighted average over 10 days. `avwap` is anchored at the earliest lowest low "
            "from 63 sessions ago through 10 sessions before the entry, so the breakout bar is not the anchor.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _reading(features: dict[str, Any], aligned: bool) -> str:
    """State the level result and the acceleration result separately.

    A top-quartile profit factor can clear the numeric gate on a U-shaped
    feature whose correlation is about zero. Sign consistency across eras is
    what keeps that from being read as an edge.
    """
    prefix = "" if aligned else "Price alignment failed, so this is not a confirmation of the labeled panel. "
    level_names = ("ema8_slope_10", "wema10_slope_10", "vwma10_slope_10", "ema21_slope_10", "avwap_slope_10", "avwap_dist_pct")
    level_flat = [name for name in level_names if features.get(name, {}).get("flat_verdict") == "candidate"]
    level_steep = [name for name in level_names if features.get(name, {}).get("steep_verdict") == "candidate"]
    accel = features.get("ema8_minus_sma20_slope_10") or {}
    accel_screen = accel.get("screen") or {}
    parts = [prefix + "The level of a fast average is the same result as the 20-day SMA."]
    if level_flat and not level_steep:
        parts.append(
            "The calm quartile cleared it for "
            + ", ".join(f"`{name}`" for name in level_flat)
            + ". The steep quartile did not. Names whose average was already rising quickly were the ones that got stopped out."
        )
    elif level_flat:
        parts.append(
            "The calm quartile still cleared it for " + ", ".join(f"`{name}`" for name in level_flat) + "."
        )
    elif not level_steep:
        parts.append("Neither tail of those level features cleared the worst-era floor.")
    if level_steep:
        parts.append(
            "A steep quartile cleared the numeric gate for " + ", ".join(f"`{name}`" for name in level_steep) + "."
        )
        weak = [
            name
            for name in level_steep
            if (features[name]["screen"].get("ic_net_return") or 0) <= 0
        ]
        if weak:
            parts.append(
                "Their correlation with realized return is still negative or zero, so that pass is not a reason to rank by how steep the average is."
            )
    if accel.get("steep_verdict") == "candidate":
        consistent = accel_screen.get("sign_consistent_eras")
        n_eras = accel_screen.get("n_eras_with_ic")
        parts.append(
            "The spread between the 8-day EMA slope and the 20-day SMA slope is different: "
            f"its steep quartile is a numeric candidate, with information coefficient "
            f"{_fmt(accel_screen.get('ic_net_return'))} and the same sign in {consistent} of {n_eras} eras. "
            "That is acceleration versus the slower average, not a steep average by itself. "
            "It is a research candidate, not a live filter."
        )
    elif accel:
        parts.append("The EMA-minus-SMA spread did not clear the gate either.")
    return " ".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description="Screen fast-average slopes on a frozen trade book.")
    parser.add_argument("--run-id", default=RUN_ID)
    parser.add_argument("--no-fetch", action="store_true", help="Use the local price cache only.")
    parser.add_argument("--batch-size", type=int, default=40)
    args = parser.parse_args()

    print(f"Loading {args.run_id}", flush=True)
    trades = _attach_panel(_load_book(args.run_id))
    trades["entry_date"] = pd.to_datetime(trades["entry_date"], errors="coerce").dt.normalize()
    trades = trades.dropna(subset=["entry_date", "ticker"]).reset_index(drop=True)
    needed_end = {
        str(ticker): pd.Timestamp(grp["entry_date"].max())
        for ticker, grp in trades.groupby(trades["ticker"].astype(str).str.upper())
    }
    tickers = sorted(needed_end)
    if args.no_fetch:
        fetch_stats: dict[str, Any] = {"fetched": 0, "failed": 0, "skipped": True}
    else:
        fetch_stats = fetch_missing(tickers, needed_end, batch_size=max(1, int(args.batch_size)))

    print("Computing slopes", flush=True)
    featured = _features_for_trades(trades)
    alignment = _alignment(featured)
    order = list(FEATURE_COLUMNS) + ["panel_sma_20_slope", "panel_ret_5d_prev"]
    blocks = {col: _feature_block(featured, col) for col in order if col in featured.columns}
    panel_aligned = bool(alignment.get("aligned")) or not alignment.get("applicable", True)
    reading = _reading(blocks, panel_aligned)
    payload = {
        "run_id": args.run_id,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "n_trades": int(len(featured)),
        "n_tickers": int(featured["ticker"].nunique()),
        "fetch": fetch_stats,
        "alignment": alignment,
        "features": blocks,
        "reading": reading,
    }
    ART.mkdir(parents=True, exist_ok=True)
    safe_id = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(args.run_id))
    json_path = ART / f"screen_{safe_id}.json"
    report_path = ART / f"report_{safe_id}.md"
    encoded = json.dumps(payload, indent=2, default=str)
    json_path.write_text(encoded, encoding="utf-8")
    _write_report(report_path, payload)
    if args.run_id == RUN_ID:
        (ART / "screen.json").write_text(encoded, encoding="utf-8")
        _write_report(ART / "report.md", payload)
    print(reading, flush=True)
    print(f"Wrote {report_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
