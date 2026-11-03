"""Combinatorial purged K-fold with embargo (López de Prado CPCV).

Embargo length is >= max holding period so no training label uses bars that
fall in or after the test window's embargo.
"""

from __future__ import annotations

from itertools import combinations
from typing import Any

import numpy as np
import pandas as pd


def _event_end(asof: pd.Timestamp, holding_days: int) -> pd.Timestamp:
    return pd.Timestamp(asof) + pd.Timedelta(days=int(max(0, holding_days)))


def combinatorial_purged_splits(
    df: pd.DataFrame,
    *,
    n_groups: int = 6,
    n_test_groups: int = 2,
    embargo_days: int = 40,
    holding_col: str = "barrier_holding_days",
    default_holding_days: int = 40,
    min_train_rows: int = 20,
) -> list[dict[str, Any]]:
    """
    Split unique asof dates into ``n_groups`` contiguous groups.

    Each combination of ``n_test_groups`` groups is a test fold. Train rows
    whose label window [asof, asof+holding] overlaps the test interval, or
    that fall inside the embargo after the test interval, are dropped.
    """
    if df is None or df.empty or "asof_date" not in df.columns:
        return []
    work = df.copy()
    work["asof_date"] = pd.to_datetime(work["asof_date"])
    if holding_col in work.columns:
        hold = pd.to_numeric(work[holding_col], errors="coerce").fillna(default_holding_days)
    else:
        hold = pd.Series(default_holding_days, index=work.index)
    work["_t1"] = [
        _event_end(a, int(h)) for a, h in zip(work["asof_date"], hold)
    ]
    dates = np.array(sorted(work["asof_date"].unique()))
    if len(dates) < n_groups:
        return []
    # Contiguous date groups
    splits = np.array_split(dates, n_groups)
    groups = [pd.DatetimeIndex(s) for s in splits if len(s)]
    n_groups_eff = len(groups)
    n_test = min(max(1, n_test_groups), n_groups_eff - 1)
    folds: list[dict[str, Any]] = []
    embargo = pd.Timedelta(days=int(max(embargo_days, default_holding_days)))
    for fold_i, test_ids in enumerate(combinations(range(n_groups_eff), n_test)):
        test_dates = pd.DatetimeIndex(np.concatenate([groups[i].to_numpy() for i in test_ids]))
        test_start = test_dates.min()
        test_end = test_dates.max()
        embargo_end = test_end + embargo
        test_set = set(test_dates)
        test_mask = work["asof_date"].isin(test_set)
        # Purge: label window overlaps [test_start, test_end]
        overlaps = (work["_t1"] >= test_start) & (work["asof_date"] <= test_end)
        # Embargo: asof in (test_end, embargo_end]
        in_embargo = (work["asof_date"] > test_end) & (work["asof_date"] <= embargo_end)
        train_mask = ~test_mask & ~overlaps & ~in_embargo
        train_idx = work.index[train_mask].tolist()
        test_idx = work.index[test_mask].tolist()
        if len(train_idx) < min_train_rows or len(test_idx) < 5:
            continue
        train_sorted = work.loc[train_idx].sort_values("asof_date")
        split_i = max(1, int(len(train_sorted) * 0.8))
        folds.append(
            {
                "fold_id": f"cpcv_{fold_i}",
                "test_groups": list(test_ids),
                "test_start": str(test_start.date()),
                "test_end": str(test_end.date()),
                "embargo_end": str(embargo_end.date()),
                "embargo_days": int(embargo.days),
                "train_idx": train_sorted.index[:split_i].tolist(),
                "valid_idx": train_sorted.index[split_i:].tolist(),
                "test_idx": test_idx,
                "n_purged": int((overlaps & ~test_mask).sum()),
                "n_embargoed": int(in_embargo.sum()),
            }
        )
    return folds


def assert_train_labels_respect_embargo(
    df: pd.DataFrame,
    fold: dict[str, Any],
    *,
    holding_col: str = "barrier_holding_days",
    default_holding_days: int = 40,
) -> list[str]:
    """Return errors if any train label uses bars after the embargo start (test_start)."""
    errors: list[str] = []
    work = df.copy()
    work["asof_date"] = pd.to_datetime(work["asof_date"])
    if holding_col in work.columns:
        hold = pd.to_numeric(work[holding_col], errors="coerce").fillna(default_holding_days)
    else:
        hold = pd.Series(default_holding_days, index=work.index)
    test_start = pd.Timestamp(fold["test_start"])
    cutoff = test_start
    # Stronger: t1 must be < test_start (purged) and asof not in embargo after test_end
    embargo_end = pd.Timestamp(fold["embargo_end"])
    test_end = pd.Timestamp(fold["test_end"])
    train_ids = list(fold.get("train_idx") or []) + list(fold.get("valid_idx") or [])
    for idx in train_ids:
        asof = work.at[idx, "asof_date"]
        t1 = _event_end(asof, int(hold.at[idx]))
        if t1 >= test_start and asof <= test_end:
            errors.append(f"train idx {idx} label window overlaps test ({asof.date()}->{t1.date()})")
        if test_end < asof <= embargo_end:
            errors.append(f"train idx {idx} asof {asof.date()} falls inside embargo")
        if t1 >= cutoff and asof < test_start and t1 >= test_start:
            errors.append(f"train idx {idx} uses bars after embargo/test start ({t1.date()})")
    return errors
