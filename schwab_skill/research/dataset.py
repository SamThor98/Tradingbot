"""Build frozen rank-training datasets from feature panels + labels."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from research.feature_engine import compute_feature_row, iter_stage2_asof_dates
from research.labels import (
    LABEL_COLUMNS,
    attach_forward_labels,
    join_strategy_labels,
    strategy_label_frame_from_trades,
)
from research.leakage import validate_dataset_leakage, walk_forward_splits
from research.paths import datasets_dir, ensure_research_store_layout, panels_features_dir
from research.registry import FEATURE_SCHEMA_VERSION, enabled_feature_names

LOG = logging.getLogger(__name__)

ERA_BOUNDS: dict[str, tuple[str, str | None]] = {
    "late_bull": ("2015-01-01", "2017-12-31"),
    "volatility_chop": ("2018-01-01", "2019-12-31"),
    "crash_recovery": ("2020-01-01", "2021-12-31"),
    "bear_rates": ("2022-01-01", "2023-12-31"),
    "recent_current": ("2024-01-01", None),
}


def _dataset_id(
    *,
    candidate_set: str,
    schema_version: int,
    date_start: str | None,
    date_end: str | None,
    label_set: str,
    n_rows: int,
    feature_hash: str,
) -> str:
    raw = "|".join(
        [
            candidate_set,
            str(schema_version),
            date_start or "",
            date_end or "",
            label_set,
            str(n_rows),
            feature_hash,
        ]
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"rank_{candidate_set}_s{schema_version}_{digest}"


def load_feature_panels(
    *,
    skill_dir: Path | None = None,
    schema_version: int = FEATURE_SCHEMA_VERSION,
    tickers: Iterable[str] | None = None,
    date_start: str | None = None,
    date_end: str | None = None,
) -> pd.DataFrame:
    """Load materialized feature parquet panels."""
    root = panels_features_dir(schema_version=schema_version, skill_dir=skill_dir)
    if not root.exists():
        return pd.DataFrame()
    paths = sorted(root.glob("year=*/**/*.parquet")) + sorted(root.glob("year=*/*.parquet"))
    # de-dupe
    seen: set[Path] = set()
    frames: list[pd.DataFrame] = []
    want = {t.upper() for t in tickers} if tickers else None
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        try:
            part = pd.read_parquet(path)
        except Exception as exc:
            LOG.warning("Skip unreadable parquet %s: %s", path, exc)
            continue
        if part.empty:
            continue
        if want is not None:
            part = part[part["ticker"].astype(str).str.upper().isin(want)]
        if date_start:
            part = part[pd.to_datetime(part["asof_date"]) >= pd.Timestamp(date_start)]
        if date_end:
            part = part[pd.to_datetime(part["asof_date"]) <= pd.Timestamp(date_end)]
        if not part.empty:
            frames.append(part)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    return out.drop_duplicates(
        subset=["asof_date", "ticker", "candidate_set_version", "feature_schema_version"],
        keep="last",
    )


def materialize_features_from_bars(
    ticker_bars: dict[str, pd.DataFrame],
    *,
    candidate_set: str = "stage2_pass_v1",
    schema_version: int = FEATURE_SCHEMA_VERSION,
    date_start: str | None = None,
    date_end: str | None = None,
    skill_dir: Path | None = None,
    require_stage2: bool = True,
) -> pd.DataFrame:
    """Build feature rows in-memory from ticker → OHLCV maps (tests / small runs)."""
    rows: list[dict[str, Any]] = []
    for ticker, bars in ticker_bars.items():
        dates = iter_stage2_asof_dates(bars, start=date_start, end=date_end, skill_dir=skill_dir)
        if not require_stage2 and not dates:
            # Sample last bar only for debug
            if bars is not None and len(bars):
                dates = [str(pd.Timestamp(normalize_last(bars)).date())]
        for asof in dates:
            row = compute_feature_row(
                ticker=ticker,
                df=bars,
                asof_date=asof,
                candidate_set_version=candidate_set,
                feature_schema_version=schema_version,
                skill_dir=skill_dir,
                require_stage2=require_stage2,
            )
            if row is not None:
                rows.append(row)
    return pd.DataFrame(rows)


def normalize_last(bars: pd.DataFrame) -> pd.Timestamp:
    from research.feature_engine import normalize_ohlcv

    return normalize_ohlcv(bars).index[-1]


def attach_labels_to_features(
    features: pd.DataFrame,
    ticker_bars: dict[str, pd.DataFrame],
    *,
    strategy_trades: list[dict[str, Any]] | pd.DataFrame | None = None,
    label_set: str = "fwd40+strategy",
) -> pd.DataFrame:
    """Attach forward labels (and optional strategy outcomes) to feature rows."""
    if features.empty:
        return features.copy()
    labeled_rows: list[dict[str, Any]] = []
    for _, row in features.iterrows():
        ticker = str(row["ticker"]).upper()
        bars = ticker_bars.get(ticker)
        if bars is None:
            continue
        attached = attach_forward_labels(row.to_dict(), bars)
        if attached is None:
            continue
        labeled_rows.append(attached)
    out = pd.DataFrame(labeled_rows)
    if out.empty:
        return out
    if "strategy" in label_set and strategy_trades is not None:
        strat = strategy_label_frame_from_trades(strategy_trades)
        out = join_strategy_labels(out, strat)
    return out


def resolve_feature_columns(df: pd.DataFrame, registry_names: list[str] | None = None) -> list[str]:
    names = registry_names if registry_names is not None else enabled_feature_names(ohlcv_only=False)
    label_set = set(LABEL_COLUMNS)
    cols = []
    for name in names:
        if name in label_set:
            continue
        if name in df.columns:
            cols.append(name)
    # Always include OHLCV-enabled names that exist even if registry lists extras
    for name in enabled_feature_names(ohlcv_only=True):
        if name in df.columns and name not in cols and name not in label_set:
            cols.append(name)
    return cols


def build_rank_dataset(
    *,
    candidate_set: str = "stage2_pass_v1",
    schema_version: int = FEATURE_SCHEMA_VERSION,
    date_start: str | None = None,
    date_end: str | None = None,
    label_set: str = "fwd40+strategy",
    skill_dir: Path | None = None,
    ticker_bars: dict[str, pd.DataFrame] | None = None,
    features: pd.DataFrame | None = None,
    strategy_trades: list[dict[str, Any]] | pd.DataFrame | None = None,
    write: bool = True,
) -> tuple[pd.DataFrame, Path | None, dict[str, Any]]:
    """
    Build a frozen rank dataset.

    Returns ``(dataframe, path_or_none, manifest)``.
    """
    ensure_research_store_layout(skill_dir, schema_version=schema_version)

    if features is None:
        if ticker_bars:
            features = materialize_features_from_bars(
                ticker_bars,
                candidate_set=candidate_set,
                schema_version=schema_version,
                date_start=date_start,
                date_end=date_end,
                skill_dir=skill_dir,
            )
        else:
            features = load_feature_panels(
                skill_dir=skill_dir,
                schema_version=schema_version,
                date_start=date_start,
                date_end=date_end,
            )

    if features is None or features.empty:
        raise ValueError("No feature rows available for dataset build")

    bars_map = ticker_bars or {}
    if not bars_map:
        # Forward labels require bars; without them only strategy labels can attach
        if "fwd" in label_set or "fwd40" in label_set:
            raise ValueError("ticker_bars required when label_set includes forward returns")
        ds = features.copy()
        if strategy_trades is not None:
            ds = join_strategy_labels(ds, strategy_label_frame_from_trades(strategy_trades))
    else:
        ds = attach_labels_to_features(
            features,
            bars_map,
            strategy_trades=strategy_trades,
            label_set=label_set,
        )

    if ds.empty:
        raise ValueError("Dataset empty after label join (need 40 forward bars for fwd labels)")

    feature_cols = resolve_feature_columns(ds)
    leakage = validate_dataset_leakage(ds, feature_cols)
    if not leakage.ok:
        raise ValueError(f"Leakage validation failed: {leakage.errors}")

    feat_hash = hashlib.sha256(",".join(feature_cols).encode("utf-8")).hexdigest()[:10]
    ds_id = _dataset_id(
        candidate_set=candidate_set,
        schema_version=schema_version,
        date_start=date_start,
        date_end=date_end,
        label_set=label_set,
        n_rows=len(ds),
        feature_hash=feat_hash,
    )
    ds = ds.copy()
    ds["dataset_id"] = ds_id

    folds = walk_forward_splits(ds, ERA_BOUNDS)
    manifest: dict[str, Any] = {
        "dataset_id": ds_id,
        "candidate_set": candidate_set,
        "schema_version": schema_version,
        "label_set": label_set,
        "date_start": date_start,
        "date_end": date_end,
        "n_rows": int(len(ds)),
        "feature_columns": feature_cols,
        "label_columns": [c for c in LABEL_COLUMNS if c in ds.columns],
        "leakage": {"ok": leakage.ok, "errors": leakage.errors, "warnings": leakage.warnings},
        "n_folds": len(folds),
        "fold_ids": [f["fold_id"] for f in folds],
    }

    out_path: Path | None = None
    if write:
        out_dir = datasets_dir(skill_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{ds_id}.parquet"
        ds.to_parquet(out_path, index=False)
        man_path = out_dir / f"{ds_id}.manifest.json"
        man_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        LOG.info("Wrote dataset %s rows=%s -> %s", ds_id, len(ds), out_path)

    return ds, out_path, manifest
