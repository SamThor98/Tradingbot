"""Leakage checks for rank datasets and walk-forward splits."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from research.labels import LABEL_COLUMNS

# Columns that must never appear as model inputs
FORBIDDEN_FEATURE_PREFIXES = (
    "ret_",  # only *_fwd labels; ret_*d_prev is allowed as feature
    "y_up_",
    "drawdown_",
    "net_return",
    "r_multiple",
    "strategy_",
)

# Explicit allowlist exception: prior returns are features
ALLOWED_RET_FEATURES = {
    "ret_5d_prev",
    "ret_10d_prev",
    "ret_20d_prev",
    "ret_60d_prev",
    "ret_120d_prev",
    "ret_252d_prev",
}

META_COLUMNS = {
    "asof_date",
    "ticker",
    "candidate_set_version",
    "feature_schema_version",
    "bar_provider",
    "feature_coverage",
    "era",
    "dataset_id",
    "fold",
    "split",
}


@dataclass(frozen=True)
class LeakageReport:
    ok: bool
    errors: list[str]
    warnings: list[str]


def assert_no_label_columns_in_features(feature_cols: list[str]) -> list[str]:
    errors: list[str] = []
    label_set = set(LABEL_COLUMNS)
    for col in feature_cols:
        if col in label_set:
            errors.append(f"label column used as feature: {col}")
            continue
        if col in ALLOWED_RET_FEATURES:
            continue
        if col.startswith("ret_") and col.endswith("_fwd"):
            errors.append(f"forward return used as feature: {col}")
        if col.startswith("y_up_") or col.startswith("drawdown_"):
            errors.append(f"label-like column used as feature: {col}")
        if col in ("net_return", "r_multiple") or col.startswith("strategy_"):
            errors.append(f"strategy outcome used as feature: {col}")
    return errors


def validate_dataset_leakage(df: pd.DataFrame, feature_cols: list[str]) -> LeakageReport:
    """Structural leakage checks on a built dataset."""
    errors: list[str] = []
    warnings: list[str] = []
    errors.extend(assert_no_label_columns_in_features(feature_cols))

    if "asof_date" not in df.columns:
        errors.append("missing asof_date")
    else:
        dates = pd.to_datetime(df["asof_date"], errors="coerce")
        if dates.isna().any():
            errors.append("asof_date has unparseable values")

    if "ret_40d_fwd" in df.columns and "asof_date" in df.columns:
        # Label horizon sanity: rows missing labels near the end are OK; silent zeros are not
        if (df["ret_40d_fwd"] == 0).mean() > 0.85 and len(df) >= 50:
            warnings.append("ret_40d_fwd is zero for >85% of rows — check label join")

    if "feature_coverage" in df.columns:
        cov = pd.to_numeric(df["feature_coverage"], errors="coerce")
        if cov.notna().any() and float(cov.mean()) < 0.3:
            warnings.append("mean feature_coverage < 0.3")

    return LeakageReport(ok=not errors, errors=errors, warnings=warnings)


def purge_gap_mask(
    train_dates: pd.Series,
    test_dates: pd.Series,
    *,
    purge_days: int = 40,
) -> tuple[pd.Series, pd.Series]:
    """
    Return boolean masks (train_keep, test_keep) enforcing a purge gap.

    Drops train rows whose asof_date is within ``purge_days`` calendar days
    before the earliest test asof_date.
    """
    tr = pd.to_datetime(train_dates)
    te = pd.to_datetime(test_dates)
    if te.empty or tr.empty:
        return pd.Series([True] * len(tr), index=tr.index), pd.Series([True] * len(te), index=te.index)
    test_start = te.min()
    cutoff = test_start - pd.Timedelta(days=int(purge_days))
    train_keep = tr <= cutoff
    return train_keep, pd.Series([True] * len(te), index=te.index)


def assign_era(asof_date: str | pd.Timestamp, era_bounds: dict[str, tuple[str, str | None]]) -> str | None:
    ts = pd.Timestamp(asof_date).normalize()
    for name, (start, end) in era_bounds.items():
        if ts < pd.Timestamp(start):
            continue
        if end is None or ts <= pd.Timestamp(end):
            return name
    return None


def walk_forward_splits(
    df: pd.DataFrame,
    era_bounds: dict[str, tuple[str, str | None]],
    *,
    purge_days: int = 40,
    min_train_rows: int = 50,
) -> list[dict[str, Any]]:
    """
    Expanding walk-forward splits aligned to catalog eras.

    For each test era after the first, train = all prior eras with purge gap.
    """
    work = df.copy()
    work["asof_date"] = pd.to_datetime(work["asof_date"])
    work["era"] = work["asof_date"].map(lambda d: assign_era(d, era_bounds))
    era_order = list(era_bounds.keys())
    folds: list[dict[str, Any]] = []
    for i, test_era in enumerate(era_order):
        if i == 0:
            continue
        train_eras = era_order[:i]
        train_df = work[work["era"].isin(train_eras)].copy()
        test_df = work[work["era"] == test_era].copy()
        if train_df.empty or test_df.empty:
            continue
        keep_tr, _ = purge_gap_mask(train_df["asof_date"], test_df["asof_date"], purge_days=purge_days)
        train_df = train_df.loc[keep_tr]
        if len(train_df) < min_train_rows:
            continue
        # Chronological validation = last 20% of train window
        train_df = train_df.sort_values("asof_date")
        split_i = max(1, int(len(train_df) * 0.8))
        folds.append(
            {
                "fold_id": f"test_{test_era}",
                "test_era": test_era,
                "train_eras": train_eras,
                "train_idx": train_df.index[:split_i].tolist(),
                "valid_idx": train_df.index[split_i:].tolist(),
                "test_idx": test_df.index.tolist(),
            }
        )
    return folds
