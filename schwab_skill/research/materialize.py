"""Write research feature panels to Parquet under research_store/."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from research.feature_engine import compute_feature_row, iter_stage2_asof_dates
from research.paths import ensure_research_store_layout, panels_features_dir
from research.registry import FEATURE_SCHEMA_VERSION

LOG = logging.getLogger(__name__)


def rows_to_dataframe(rows: Iterable[dict[str, Any]]) -> pd.DataFrame:
    data = list(rows)
    if not data:
        return pd.DataFrame()
    return pd.DataFrame(data)


def write_feature_panel(
    df: pd.DataFrame,
    *,
    skill_dir: Path | None = None,
    schema_version: int = FEATURE_SCHEMA_VERSION,
    ticker: str | None = None,
) -> list[Path]:
    """
    Persist feature rows partitioned by year.

    Writes ``year=YYYY/{ticker}.parquet`` under the schema features dir.
    Returns written file paths.
    """
    if df is None or df.empty:
        return []
    ensure_research_store_layout(skill_dir, schema_version=schema_version)
    root = panels_features_dir(schema_version=schema_version, skill_dir=skill_dir)
    work = df.copy()
    work["asof_date"] = pd.to_datetime(work["asof_date"])
    work["year"] = work["asof_date"].dt.year.astype(int)
    written: list[Path] = []
    dedupe_cols = ["asof_date", "ticker", "candidate_set_version", "feature_schema_version"]
    for year, group in work.groupby("year"):
        year_dir = root / f"year={int(year)}"
        year_dir.mkdir(parents=True, exist_ok=True)
        label = (ticker or str(group["ticker"].iloc[0])).upper()
        out_path = year_dir / f"{label}.parquet"
        payload = group.drop(columns=["year"])
        payload["asof_date"] = payload["asof_date"].dt.strftime("%Y-%m-%d")
        if out_path.exists():
            try:
                existing = pd.read_parquet(out_path)
                payload = (
                    pd.concat([existing, payload], ignore_index=True)
                    .drop_duplicates(subset=dedupe_cols, keep="last")
                    .sort_values(["asof_date", "ticker"])
                )
            except Exception as exc:
                LOG.warning("Could not merge existing parquet %s: %s", out_path, exc)
        payload.to_parquet(out_path, index=False)
        written.append(out_path)
        LOG.info("Wrote %s rows -> %s", len(payload), out_path)
    return written


def materialize_ticker(
    *,
    ticker: str,
    bars: pd.DataFrame,
    asof_dates: list[str] | None = None,
    extras_by_date: dict[str, dict[str, Any]] | None = None,
    candidate_set_version: str = "stage2_pass_v1",
    feature_schema_version: int = FEATURE_SCHEMA_VERSION,
    bar_provider: str | None = None,
    skill_dir: Path | None = None,
    start: str | None = None,
    end: str | None = None,
    require_stage2: bool = True,
    write: bool = True,
) -> pd.DataFrame:
    """
    Materialize Stage-2 candidate feature rows for one ticker.

    If ``asof_dates`` is None, discover dates via ``iter_stage2_asof_dates``.
    """
    dates = asof_dates
    if dates is None:
        dates = iter_stage2_asof_dates(bars, start=start, end=end, skill_dir=skill_dir)
    rows: list[dict[str, Any]] = []
    extras_map = extras_by_date or {}
    for asof in dates:
        asof_key = str(pd.Timestamp(asof).date())
        row = compute_feature_row(
            ticker=ticker,
            df=bars,
            asof_date=asof,
            extras=extras_map.get(asof_key) or extras_map.get(str(asof)),
            candidate_set_version=candidate_set_version,
            feature_schema_version=feature_schema_version,
            bar_provider=bar_provider,
            skill_dir=skill_dir,
            require_stage2=require_stage2,
        )
        if row is not None:
            rows.append(row)
    frame = rows_to_dataframe(rows)
    if write and not frame.empty:
        write_feature_panel(
            frame,
            skill_dir=skill_dir,
            schema_version=feature_schema_version,
            ticker=ticker,
        )
    return frame
