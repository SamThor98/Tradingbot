#!/usr/bin/env python3
"""Measure entry traits of Stage 2 winners. Does not change a live gate.

Pre-registered rules (fixed before the numbers are interpreted):

1. Executed book: validation_artifacts/entry_timing_replay_cache_stage2_only_aug.json.
   Outcome is path net_return. Holdout era is recent_current. Discovery is the
   other four eras.
2. Volume ratio is not on that tape. It is measured on
   validation_artifacts/scoring_audit_dataset_full.csv, which is Stage 2 + VCP
   days for 78 names with close-to-close forward returns, not stopped trades.
3. Audit rows are de-overlapped to one entry per ticker per 20 calendar days.
   Discovery is entry_date < 2022-01-01. Confirmation is the rest.
4. Primary audit metric is the profit factor of ret_20d_fwd. ret_40d_fwd is a
   sensitivity. A candidate gate needs discovery n >= 150 and retention between
   20% and 80%. The frozen gate is the discovery maximum. It is "supported"
   only if confirmation profit factor beats the ungated confirmation book.
5. Two gate families are scored: volume_ratio >= t and volume_ratio <= t,
   t from 0.40 to 1.60 step 0.05. The family must match the sign of the
   discovery Spearman correlation. A correlation inside ±0.02 is treated as
   no linear association, and no gate is frozen.
6. Other entry traits on the executed book are descriptive. They are not a
   second search for a live cutoff.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent.parent
ARTIFACTS = SKILL_DIR / "validation_artifacts"
REPLAY = ARTIFACTS / "entry_timing_replay_cache_stage2_only_aug.json"
AUDIT = ARTIFACTS / "scoring_audit_dataset_full.csv"
HOLDOUT_ERA = "recent_current"
AUDIT_CUT = pd.Timestamp("2022-01-01")
DEOVERLAP_DAYS = 20
MIN_GATE_N = 150
RETAIN_LO = 0.20
RETAIN_HI = 0.80
IC_FLOOR = 0.02


def _pf(values: pd.Series | np.ndarray) -> float | None:
    r = pd.to_numeric(pd.Series(values), errors="coerce").dropna().to_numpy(dtype=float)
    if r.size == 0:
        return None
    wins = float(r[r > 0].sum())
    losses = float(-r[r <= 0].sum())
    if losses <= 1e-12:
        return None if wins <= 1e-12 else 999.0
    return wins / losses


def _mean(values: pd.Series | np.ndarray) -> float | None:
    r = pd.to_numeric(pd.Series(values), errors="coerce").dropna()
    if r.empty:
        return None
    return float(r.mean())


def _spearman(x: pd.Series, y: pd.Series) -> float | None:
    frame = pd.DataFrame({"x": pd.to_numeric(x, errors="coerce"), "y": pd.to_numeric(y, errors="coerce")}).dropna()
    if len(frame) < 30 or frame["x"].nunique() < 3:
        return None
    return float(frame["x"].corr(frame["y"], method="spearman"))


def _round(value: float | None, ndigits: int = 4) -> float | None:
    if value is None or not math.isfinite(float(value)):
        return None
    return round(float(value), ndigits)


def _book(frame: pd.DataFrame, col: str) -> dict[str, Any]:
    return {
        "n": int(len(frame)),
        "pf": _round(_pf(frame[col])),
        "mean": _round(_mean(frame[col])),
        "win_rate": _round(float((pd.to_numeric(frame[col], errors="coerce") > 0).mean())) if len(frame) else None,
    }


def _deoverlap(frame: pd.DataFrame) -> pd.DataFrame:
    ordered = frame.sort_values(["ticker", "entry_date"])
    keep: list[Any] = []
    last: dict[str, pd.Timestamp] = {}
    for idx, row in ordered.iterrows():
        ticker = str(row["ticker"])
        day = row["entry_date"]
        prev = last.get(ticker)
        if prev is None or (day - prev).days >= DEOVERLAP_DAYS:
            keep.append(idx)
            last[ticker] = day
    return ordered.loc[keep]


def _gate_table(frame: pd.DataFrame, outcome: str, *, direction: str) -> list[dict[str, Any]]:
    base_n = max(1, len(frame))
    rows: list[dict[str, Any]] = []
    thresholds = [round(float(x), 2) for x in np.arange(0.40, 1.601, 0.05)]
    vol = pd.to_numeric(frame["volume_ratio"], errors="coerce")
    for threshold in thresholds:
        mask = vol >= threshold if direction == "at_least" else vol <= threshold
        part = frame.loc[mask.fillna(False)]
        rows.append(
            {
                "threshold": threshold,
                "direction": direction,
                "n": int(len(part)),
                "retention": _round(len(part) / base_n),
                "pf": _round(_pf(part[outcome])),
                "mean": _round(_mean(part[outcome])),
            }
        )
    return rows


def _pick_gate(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    eligible = [
        row
        for row in rows
        if row["n"] >= MIN_GATE_N
        and row["pf"] is not None
        and row["retention"] is not None
        and RETAIN_LO <= row["retention"] <= RETAIN_HI
    ]
    if not eligible:
        return None
    return max(eligible, key=lambda row: (row["pf"], row["n"]))


def _quintiles(frame: pd.DataFrame, feature: str, outcome: str) -> list[dict[str, Any]]:
    work = frame[[feature, outcome]].apply(pd.to_numeric, errors="coerce").dropna()
    if len(work) < 50 or work[feature].nunique() < 5:
        return []
    try:
        work = work.copy()
        work["q"] = pd.qcut(work[feature], 5, labels=False, duplicates="drop")
    except ValueError:
        return []
    out: list[dict[str, Any]] = []
    for q, part in work.groupby("q"):
        out.append(
            {
                "quintile": int(q) + 1,
                "n": int(len(part)),
                "feature_median": _round(float(part[feature].median()), 4),
                "pf": _round(_pf(part[outcome])),
                "mean": _round(_mean(part[outcome])),
            }
        )
    return out


def _trait_row(discovery: pd.DataFrame, holdout: pd.DataFrame, feature: str) -> dict[str, Any]:
    def _side(frame: pd.DataFrame) -> dict[str, Any]:
        values = pd.to_numeric(frame[feature], errors="coerce")
        ret = pd.to_numeric(frame["net_return"], errors="coerce")
        valid = frame.loc[values.notna() & ret.notna()].copy()
        if valid.empty:
            return {"n": 0}
        cut = float(pd.to_numeric(valid["net_return"], errors="coerce").quantile(0.90))
        winners = valid.loc[pd.to_numeric(valid["net_return"], errors="coerce") >= cut]
        rest = valid.loc[pd.to_numeric(valid["net_return"], errors="coerce") < cut]
        return {
            "n": int(len(valid)),
            "spearman_vs_net_return": _round(_spearman(valid[feature], valid["net_return"])),
            "winner_cut_net_return": _round(cut),
            "winner_n": int(len(winners)),
            "winner_median": _round(float(pd.to_numeric(winners[feature], errors="coerce").median())),
            "rest_median": _round(float(pd.to_numeric(rest[feature], errors="coerce").median())),
            "p10": _round(float(pd.to_numeric(valid[feature], errors="coerce").quantile(0.10))),
            "p50": _round(float(pd.to_numeric(valid[feature], errors="coerce").quantile(0.50))),
            "p90": _round(float(pd.to_numeric(valid[feature], errors="coerce").quantile(0.90))),
        }

    return {"feature": feature, "discovery": _side(discovery), "holdout": _side(holdout)}


def analyze_replay() -> dict[str, Any]:
    trades = pd.DataFrame(json.loads(REPLAY.read_text(encoding="utf-8")))
    trades["entry_date"] = pd.to_datetime(trades["entry_date"])
    trades["net_return"] = pd.to_numeric(trades["net_return"], errors="coerce")
    trades["pts_volume"] = pd.to_numeric(trades["pts_volume"], errors="coerce")
    # pts_volume = clip(20 - 20 * avg_vcp_volume_ratio, 0, 20). Zero is censored at ratio >= 1.
    trades["implied_vcp_ratio"] = (20.0 - trades["pts_volume"]) / 20.0
    discovery = trades.loc[trades["era"] != HOLDOUT_ERA].copy()
    holdout = trades.loc[trades["era"] == HOLDOUT_ERA].copy()
    features = [
        "pct_from_52w_high",
        "close_vs_sma200_pct",
        "pct_above_sma50",
        "breakout_buffer_pct",
        "signal_score",
        "pts_volume",
        "implied_vcp_ratio",
        "rank_score_v2",
        "stop_pct",
    ]
    buffer = pd.to_numeric(trades["breakout_buffer_pct"], errors="coerce")

    def _buffer_split(frame: pd.DataFrame) -> dict[str, Any]:
        buf = pd.to_numeric(frame["breakout_buffer_pct"], errors="coerce")
        return {
            "below_1pct": _book(frame.loc[buf < 0.01], "net_return"),
            "at_least_1pct": _book(frame.loc[buf >= 0.01], "net_return"),
        }

    return {
        "source": REPLAY.name,
        "n": int(len(trades)),
        "date_min": str(trades["entry_date"].min().date()),
        "date_max": str(trades["entry_date"].max().date()),
        "eras": {str(k): int(v) for k, v in trades["era"].value_counts().items()},
        "discovery_eras": sorted(set(discovery["era"].astype(str)) - {HOLDOUT_ERA}),
        "holdout_era": HOLDOUT_ERA,
        "ungated": {"discovery": _book(discovery, "net_return"), "holdout": _book(holdout, "net_return")},
        "pts_volume_zero_share": _round(float((trades["pts_volume"] <= 0).mean())),
        "pts_volume_at_cap_share": _round(float((trades["pts_volume"] >= 20).mean())),
        "breakout_buffer_missing_share": _round(float(buffer.isna().mean())),
        "buffer_1pct_split": {"discovery": _buffer_split(discovery), "holdout": _buffer_split(holdout)},
        "traits": [_trait_row(discovery, holdout, feature) for feature in features],
        "quintiles_discovery": {feature: _quintiles(discovery, feature, "net_return") for feature in features},
        "quintiles_holdout": {feature: _quintiles(holdout, feature, "net_return") for feature in features},
    }


def analyze_audit() -> dict[str, Any]:
    raw = pd.read_csv(AUDIT)
    raw["entry_date"] = pd.to_datetime(raw["entry_date"])
    raw["volume_ratio"] = pd.to_numeric(raw["volume_ratio"], errors="coerce")
    deoverlapped = _deoverlap(raw.dropna(subset=["volume_ratio", "ret_20d_fwd"]))
    discovery = deoverlapped.loc[deoverlapped["entry_date"] < AUDIT_CUT].copy()
    confirm = deoverlapped.loc[deoverlapped["entry_date"] >= AUDIT_CUT].copy()
    ic_discovery = _spearman(discovery["volume_ratio"], discovery["ret_20d_fwd"])
    family = None
    if ic_discovery is not None and ic_discovery >= IC_FLOOR:
        family = "at_least"
    elif ic_discovery is not None and ic_discovery <= -IC_FLOOR:
        family = "at_most"

    families = {}
    frozen = None
    confirm_frozen = None
    for direction in ("at_least", "at_most"):
        disc_rows = _gate_table(discovery, "ret_20d_fwd", direction=direction)
        conf_rows = _gate_table(confirm, "ret_20d_fwd", direction=direction)
        picked = _pick_gate(disc_rows) if direction == family else None
        families[direction] = {
            "discovery": disc_rows,
            "confirmation": conf_rows,
            "picked_on_discovery": picked,
        }
        if picked is not None:
            match = next(row for row in conf_rows if row["threshold"] == picked["threshold"])
            frozen = picked
            confirm_frozen = match

    ungated_confirm = _book(confirm, "ret_20d_fwd")
    supported = False
    if frozen is not None and confirm_frozen is not None and ungated_confirm["pf"] is not None:
        supported = confirm_frozen["pf"] is not None and confirm_frozen["pf"] > ungated_confirm["pf"]

    def _dist(frame: pd.DataFrame) -> dict[str, Any]:
        v = pd.to_numeric(frame["volume_ratio"], errors="coerce").dropna()
        return {
            "n": int(len(v)),
            "p10": _round(float(v.quantile(0.10))),
            "p25": _round(float(v.quantile(0.25))),
            "p50": _round(float(v.quantile(0.50))),
            "p75": _round(float(v.quantile(0.75))),
            "p90": _round(float(v.quantile(0.90))),
            "share_below_0_9": _round(float((v < 0.9).mean())),
            "share_below_1": _round(float((v < 1.0).mean())),
        }

    def _winner_volume(frame: pd.DataFrame, outcome: str) -> dict[str, Any]:
        work = frame.dropna(subset=["volume_ratio", outcome])
        if work.empty:
            return {"n": 0}
        cut = float(pd.to_numeric(work[outcome], errors="coerce").quantile(0.90))
        winners = work.loc[pd.to_numeric(work[outcome], errors="coerce") >= cut]
        rest = work.loc[pd.to_numeric(work[outcome], errors="coerce") < cut]
        return {
            "outcome": outcome,
            "winner_n": int(len(winners)),
            "winner_volume_median": _round(float(winners["volume_ratio"].median())),
            "rest_volume_median": _round(float(rest["volume_ratio"].median())),
            "winner_volume_p90": _round(float(winners["volume_ratio"].quantile(0.90))),
        }

    return {
        "source": AUDIT.name,
        "raw_rows": int(len(raw)),
        "raw_tickers": int(raw["ticker"].nunique()),
        "date_min": str(raw["entry_date"].min().date()),
        "date_max": str(raw["entry_date"].max().date()),
        "deoverlap_days": DEOVERLAP_DAYS,
        "deoverlapped_rows": int(len(deoverlapped)),
        "deoverlapped_tickers": int(deoverlapped["ticker"].nunique()),
        "cut": str(AUDIT_CUT.date()),
        "volume_distribution": {"all": _dist(deoverlapped), "discovery": _dist(discovery), "confirmation": _dist(confirm)},
        "ungated": {
            "discovery_20d": _book(discovery, "ret_20d_fwd"),
            "confirmation_20d": ungated_confirm,
            "discovery_40d": _book(discovery, "ret_40d_fwd"),
            "confirmation_40d": _book(confirm, "ret_40d_fwd"),
        },
        "spearman_volume_vs_ret20": {
            "discovery": _round(ic_discovery),
            "confirmation": _round(_spearman(confirm["volume_ratio"], confirm["ret_20d_fwd"])),
        },
        "spearman_volume_vs_ret40": {
            "discovery": _round(_spearman(discovery["volume_ratio"], discovery["ret_40d_fwd"])),
            "confirmation": _round(_spearman(confirm["volume_ratio"], confirm["ret_40d_fwd"])),
        },
        "spearman_vcp_vs_ret20": {
            "discovery": _round(_spearman(discovery["avg_vcp_volume_ratio"], discovery["ret_20d_fwd"])),
            "confirmation": _round(_spearman(confirm["avg_vcp_volume_ratio"], confirm["ret_20d_fwd"])),
        },
        "frozen_family": family,
        "frozen_gate": frozen,
        "confirmation_at_frozen_gate": confirm_frozen,
        "supported_out_of_sample": supported,
        "families": families,
        "quintile_volume_ret20": {
            "discovery": _quintiles(discovery, "volume_ratio", "ret_20d_fwd"),
            "confirmation": _quintiles(confirm, "volume_ratio", "ret_20d_fwd"),
        },
        "quintile_vcp_ret20": {
            "discovery": _quintiles(discovery, "avg_vcp_volume_ratio", "ret_20d_fwd"),
            "confirmation": _quintiles(confirm, "avg_vcp_volume_ratio", "ret_20d_fwd"),
        },
        "winner_volume": {
            "discovery_20d": _winner_volume(discovery, "ret_20d_fwd"),
            "confirmation_20d": _winner_volume(confirm, "ret_20d_fwd"),
            "discovery_40d": _winner_volume(discovery, "ret_40d_fwd"),
            "confirmation_40d": _winner_volume(confirm, "ret_40d_fwd"),
        },
        "other_feature_spearman_ret20": {
            feature: {
                "discovery": _round(_spearman(discovery[feature], discovery["ret_20d_fwd"])),
                "confirmation": _round(_spearman(confirm[feature], confirm["ret_20d_fwd"])),
            }
            for feature in (
                "pct_from_52w_high",
                "close_vs_sma200_pct",
                "close_vs_sma50_pct",
                "atr_pct",
                "ret_5d_prev",
                "ret_20d_prev",
                "sector_rel_21d",
                "signal_score",
                "avg_vcp_volume_ratio",
            )
            if feature in discovery.columns
        },
    }


def main() -> int:
    payload = {
        "replay": analyze_replay(),
        "audit": analyze_audit(),
    }
    out = ARTIFACTS / "winner_entry_traits.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {out}")
    audit = payload["audit"]
    print(
        "volume family",
        audit["frozen_family"],
        "gate",
        audit["frozen_gate"],
        "confirm",
        audit["confirmation_at_frozen_gate"],
        "supported",
        audit["supported_out_of_sample"],
    )
    print("spearman20", audit["spearman_volume_vs_ret20"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
