"""Winner-neighborhood analog overlay — shadow score from realized outcomes.

k-NN in a small entry-time feature space. The same extract + normalize path
is used offline (library build / walk-forward) and at scan time so the number
on the row is the number the counterfactual scored.

Distance is **Euclidean** on fitted-p1/p99 normalized 0-100 features.
Ticker identity is never a distance coordinate. Forward returns, hold days,
and exit reasons are labels / display only.

Unscored rows return ``None`` (never 0) so thin or missing ≠ weak.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from core.scoring_historical import volume_points
from core.scoring_rank_v2 import apply_rank_v2_risk_caps
from research.leakage import assert_no_label_columns_in_features

LOG = logging.getLogger(__name__)

SKILL_DIR = Path(__file__).resolve().parent.parent

DEFAULT_ARTIFACT_PATH = "research_store/models/analog_library.json"
DEFAULT_K = 40
DEFAULT_MIN_N = 20
# Floor for a useful era slice in the built library (winner+loser labeled).
MIN_ERA_LABELED_ROWS = 40
LABEL_QUINTILE = 0.20
EARLY_STOP_HOLD_DAYS = 20
MID_WINNER_HOLD_MIN = 21
MID_WINNER_HOLD_MAX = 40
STAGE2_FLOOR_DEFAULT = 0.85
PTS_52W_CAP = 40.0

# Entry-time analog coordinates. Keep this list short — curse of dimensionality
# is the failure mode. Regime keys condition the slice; they are not columns.
FEATURE_NAMES: tuple[str, ...] = (
    "close_vs_sma200_pct",
    "pct_from_52w_high",
    "volume_points",
    "pts_mirofish",
    "p_up_calibrated",
)

FORBIDDEN_INPUTS: frozenset[str] = frozenset(
    {
        "net_return",
        "hold_days",
        "exit_reason",
        "expected_return_40d",
        "ml_expected_return_norm",
        "ticker",
        "y_win",
        "label",
        "is_winner",
        "is_loser",
    }
)

# Fallback bounds only for hand-written fixtures. Production artifacts store
# fitted p1/p99 — do not reuse TREND_PCT_CAP 0.20 here.
FALLBACK_BOUNDS: dict[str, tuple[float, float]] = {
    "close_vs_sma200_pct": (0.0, 0.80),
    "pct_from_52w_high": (0.70, 1.00),
    "volume_points": (0.0, 20.0),
    "pts_mirofish": (0.0, 15.0),
    "p_up_calibrated": (0.35, 0.70),
}

DISTANCE_METRIC = "euclidean"


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(value)))


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out:  # NaN
        return None
    return out


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def reconstruct_score_components(row: dict[str, Any]) -> dict[str, Any]:
    """Rebuild nested ``score_components`` from a flat trade-frame row."""
    comps = _as_dict(row.get("score_components"))
    for key in (
        "pts_volume",
        "pts_mirofish",
        "pts_52w",
        "pct_from_52w_high",
        "avg_vcp_volume_ratio",
    ):
        if comps.get(key) is None and row.get(key) is not None:
            comps[key] = row.get(key)
    return comps


def pct_from_52w_from_pts(
    pts_52w: Any,
    *,
    stage2_floor: float = STAGE2_FLOOR_DEFAULT,
) -> float | None:
    """Invert the Stage-2 0-40 mapping when only ``pts_52w`` was persisted."""
    pts = _safe_float(pts_52w)
    if pts is None:
        return None
    floor = max(0.5, min(0.99, float(stage2_floor)))
    span = max(0.01, 1.0 - floor)
    return float(floor + (max(0.0, min(PTS_52W_CAP, pts)) / PTS_52W_CAP) * span)


def raw_features_from_signal_row(signal_row: dict[str, Any]) -> dict[str, float | None]:
    """Un-normalized analog inputs for one live or flattened trade row.

    Reuses historical-rank helpers for trend / volume / 52w so the two
    shadow scores cannot drift on those shared names. ``expected_return_40d``
    is intentionally omitted (unpurged LightGBM is a parallel track).
    """
    leakage = assert_no_label_columns_in_features(list(FEATURE_NAMES))
    if leakage:
        raise ValueError(f"analog feature list leaks labels: {leakage}")

    row = signal_row or {}
    comps = reconstruct_score_components(row)
    advisory = _as_dict(row.get("advisory"))
    prob_rank = _as_dict(row.get("prob_rank"))

    # Forbidden keys may sit on the row (trade frame) but must not be read
    # into the analog vector. Touching them here would be a silent leak.
    for banned in FORBIDDEN_INPUTS:
        if banned in FEATURE_NAMES:
            raise ValueError(f"forbidden analog feature: {banned}")

    trend_pct = _safe_float(row.get("close_vs_sma200_pct"))
    if trend_pct is None:
        price = _safe_float(row.get("price")) or _safe_float(row.get("entry_price_ref")) or 0.0
        sma_200 = _safe_float(row.get("sma_200")) or _safe_float(row.get("entry_sma_200")) or 0.0
        trend_pct = (price / sma_200) - 1.0 if price > 0 and sma_200 > 0 else None
    if trend_pct is None:
        # Research-panel alias (see research.registry OPS_TO_REGISTRY_ALIASES).
        trend_pct = _safe_float(row.get("dist_sma200_pct"))

    pct_52w = _safe_float(_first_present(comps.get("pct_from_52w_high"), row.get("pct_from_52w_high")))
    if pct_52w is None:
        pct_52w = pct_from_52w_from_pts(
            _first_present(comps.get("pts_52w"), row.get("pts_52w"))
        )

    p_up = _safe_float(
        _first_present(
            row.get("p_up_calibrated"),
            advisory.get("p_up_10d"),
            advisory.get("p_up_10d_raw"),
            row.get("p_up_10d"),
            row.get("advisory_p_up"),
            prob_rank.get("p_up_calibrated"),
        )
    )
    if p_up is not None and p_up > 1.0 and p_up <= 100.0:
        p_up = p_up / 100.0

    miro = _safe_float(_first_present(comps.get("pts_mirofish"), row.get("pts_mirofish")))

    return {
        "close_vs_sma200_pct": trend_pct,
        "pct_from_52w_high": pct_52w,
        "volume_points": volume_points(
            pts_volume=_first_present(comps.get("pts_volume"), row.get("pts_volume")),
            avg_vcp_volume_ratio=_first_present(
                comps.get("avg_vcp_volume_ratio"), row.get("avg_vcp_volume_ratio")
            ),
            volume_ratio=row.get("volume_ratio"),
            latest_volume=row.get("latest_volume"),
            avg_vol_50=row.get("avg_vol_50"),
        ),
        "pts_mirofish": miro,
        "p_up_calibrated": p_up,
    }


def normalize_feature(name: str, raw: Any, bounds: dict[str, Any] | None) -> float | None:
    """Linear clip of one raw value onto 0-100 using fitted (or fallback) bounds."""
    value = _safe_float(raw)
    if value is None:
        return None
    span = (bounds or {}).get(name)
    lo = hi = None
    if isinstance(span, dict):
        lo, hi = _safe_float(span.get("lo")), _safe_float(span.get("hi"))
    if lo is None or hi is None or hi <= lo:
        fallback = FALLBACK_BOUNDS.get(name)
        if fallback is None:
            return None
        lo, hi = fallback
    return _clamp(((value - lo) / (hi - lo)) * 100.0)


def features_from_signal_row(
    signal_row: dict[str, Any],
    *,
    bounds: dict[str, Any] | None = None,
) -> dict[str, float | None]:
    raw = raw_features_from_signal_row(signal_row)
    return {name: normalize_feature(name, raw.get(name), bounds) for name in FEATURE_NAMES}


def fit_feature_bounds(raw_rows: list[dict[str, float | None]]) -> dict[str, dict[str, float]]:
    """p1/p99 clip band per feature. Degenerate columns are omitted."""
    bounds: dict[str, dict[str, float]] = {}
    for name in FEATURE_NAMES:
        vals = [float(row[name]) for row in raw_rows if _safe_float((row or {}).get(name)) is not None]
        if len(vals) < 8:
            continue
        arr = np.asarray(vals, dtype=float)
        lo = float(np.quantile(arr, 0.01))
        hi = float(np.quantile(arr, 0.99))
        if hi <= lo:
            continue
        bounds[name] = {"lo": round(lo, 6), "hi": round(hi, 6)}
    return bounds


def resolve_regime_key(row: dict[str, Any]) -> str:
    """Neighborhood slice key. Era first; SPY state is an optional suffix."""
    era = str(row.get("era") or row.get("analog_regime") or "").strip() or "unknown"
    spy = row.get("regime_above_200")
    if spy is True or str(spy).lower() in {"1", "true", "yes"}:
        return f"{era}|spy_above_200"
    if spy is False or str(spy).lower() in {"0", "false", "no"}:
        return f"{era}|spy_below_200"
    bucket = str(row.get("regime_bucket") or "").strip()
    if bucket:
        return f"{era}|{bucket}"
    return era


def analog_model_id(
    *,
    features: tuple[str, ...] | list[str],
    bounds: dict[str, Any],
    label_rule: str,
    sources: list[str],
    k: int,
    min_n: int,
) -> str:
    payload = {
        "features": list(features),
        "bounds": bounds,
        "label_rule": label_rule,
        "sources": sorted(str(s) for s in sources),
        "k": int(k),
        "min_n": int(min_n),
        "distance": DISTANCE_METRIC,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return f"analog_{digest[:10]}"


def label_outcome_rows(
    rows: list[dict[str, Any]],
    *,
    outcome_key: str = "net_return",
) -> list[dict[str, Any]]:
    """Top vs bottom quintile of realized outcome; Phase-2 diagnostic tags."""
    values: list[tuple[int, float]] = []
    for i, row in enumerate(rows):
        ret = _safe_float(row.get(outcome_key))
        if ret is None and outcome_key != "ret_40d_fwd":
            ret = _safe_float(row.get("ret_40d_fwd"))
        if ret is None:
            continue
        values.append((i, ret))
    labeled = [dict(row) for row in rows]
    if len(values) < 5:
        for row in labeled:
            row.setdefault("is_winner", False)
            row.setdefault("is_loser", False)
            row.setdefault("early_stopout", False)
            row.setdefault("mid_winner", False)
        return labeled

    rets = np.asarray([v for _, v in values], dtype=float)
    lo = float(np.quantile(rets, LABEL_QUINTILE))
    hi = float(np.quantile(rets, 1.0 - LABEL_QUINTILE))
    by_idx = {i: ret for i, ret in values}
    for i, row in enumerate(labeled):
        ret = by_idx.get(i)
        if ret is None:
            row["is_winner"] = False
            row["is_loser"] = False
        else:
            row["is_winner"] = bool(ret >= hi)
            row["is_loser"] = bool(ret <= lo)
            row["net_return"] = ret if row.get("net_return") is None else row.get("net_return")
        hold = _safe_float(row.get("hold_days"))
        if hold is None and row.get("entry_date") is not None and row.get("exit_date") is not None:
            try:
                import pandas as pd

                hold = float((pd.Timestamp(row["exit_date"]) - pd.Timestamp(row["entry_date"])).days)
                row["hold_days"] = int(hold)
            except Exception:
                hold = None
        loss = ret is not None and ret <= 0
        win = ret is not None and ret > 0
        row["early_stopout"] = bool(hold is not None and hold <= EARLY_STOP_HOLD_DAYS and loss)
        row["mid_winner"] = bool(
            hold is not None
            and MID_WINNER_HOLD_MIN <= hold <= MID_WINNER_HOLD_MAX
            and win
        )
    return labeled


def _safe_pf(returns: list[float]) -> float | None:
    """Same convention as ``research.counterfactual._safe_pf``."""
    s = [float(x) for x in returns if _safe_float(x) is not None]
    if not s:
        return None
    wins = float(sum(x for x in s if x > 0))
    losses = float(-sum(x for x in s if x <= 0))
    if losses <= 0:
        return None if wins <= 0 else 99.0
    return round(wins / losses, 4)


def era_priors(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_era: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        era = str(row.get("era") or "unknown")
        by_era.setdefault(era, []).append(row)
    out: dict[str, dict[str, Any]] = {}
    for era, grp in by_era.items():
        rets = [_safe_float(r.get("net_return")) for r in grp]
        clean = [float(x) for x in rets if x is not None]
        labeled_w = sum(1 for r in grp if r.get("is_winner"))
        labeled_l = sum(1 for r in grp if r.get("is_loser"))
        denom = labeled_w + labeled_l
        p_win = (labeled_w / denom) if denom else None
        if p_win is None and clean:
            p_win = sum(1 for x in clean if x > 0) / len(clean)
        out[era] = {
            "n": len(grp),
            "labeled": denom,
            "p_win": None if p_win is None else round(float(p_win), 4),
            "pf": _safe_pf(clean),
        }
    return out


@dataclass
class AnalogLibrary:
    """In-memory k-NN library. Feature matrix excludes ticker and outcomes."""

    model_id: str
    feature_names: tuple[str, ...]
    bounds: dict[str, Any]
    k: int = DEFAULT_K
    min_n: int = DEFAULT_MIN_N
    rows: list[dict[str, Any]] = field(default_factory=list)
    matrix: np.ndarray | None = None
    era_index: dict[str, np.ndarray] = field(default_factory=dict)
    regime_index: dict[str, np.ndarray] = field(default_factory=dict)
    priors: dict[str, dict[str, Any]] = field(default_factory=dict)
    calibration_deciles: list[dict[str, Any]] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.matrix is None:
            self.rebuild_matrix()

    def rebuild_matrix(self) -> None:
        n = len(self.rows)
        f = len(self.feature_names)
        mat = np.full((n, f), np.nan, dtype=float)
        eras: dict[str, list[int]] = {}
        regimes: dict[str, list[int]] = {}
        for i, row in enumerate(self.rows):
            feats = row.get("features") if isinstance(row.get("features"), dict) else {}
            for j, name in enumerate(self.feature_names):
                val = _safe_float(feats.get(name) if feats else row.get(name))
                if val is not None:
                    mat[i, j] = val
            era = str(row.get("era") or "unknown")
            eras.setdefault(era, []).append(i)
            regime = str(row.get("regime") or era)
            regimes.setdefault(regime, []).append(i)
        self.matrix = mat
        self.era_index = {k: np.asarray(v, dtype=int) for k, v in eras.items()}
        self.regime_index = {k: np.asarray(v, dtype=int) for k, v in regimes.items()}


def library_from_artifact(artifact: dict[str, Any]) -> AnalogLibrary | None:
    rows = artifact.get("rows")
    if not isinstance(rows, list) or not rows:
        return None
    names = tuple(artifact.get("features") or FEATURE_NAMES)
    return AnalogLibrary(
        model_id=str(artifact.get("model_id") or "analog_unknown"),
        feature_names=names,
        bounds=artifact.get("feature_bounds") if isinstance(artifact.get("feature_bounds"), dict) else {},
        k=int(artifact.get("k") or DEFAULT_K),
        min_n=int(artifact.get("min_n") or DEFAULT_MIN_N),
        rows=list(rows),
        priors=artifact.get("era_priors") if isinstance(artifact.get("era_priors"), dict) else {},
        calibration_deciles=list(artifact.get("calibration_deciles") or []),
        sources=list(artifact.get("sources") or []),
    )


def load_analog_artifact(
    skill_dir: Path | None = None,
    override: str | None = None,
) -> dict[str, Any] | None:
    sd = skill_dir or SKILL_DIR
    raw = str(override or "").strip() or DEFAULT_ARTIFACT_PATH
    path = Path(raw)
    if not path.is_absolute():
        path = Path(sd) / path
    if not path.is_file():
        return None
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        LOG.warning("Analog library unreadable at %s: %s", path, exc)
        return None
    if not isinstance(artifact, dict) or not artifact.get("rows"):
        LOG.warning("Analog library has no rows: %s", path)
        return None
    leakage = assert_no_label_columns_in_features(list(artifact.get("features") or FEATURE_NAMES))
    if leakage:
        LOG.warning("Analog library leaks label columns: %s", leakage)
        return None
    artifact["_path"] = str(path)
    return artifact


def _euclidean(query: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """Pairwise Euclidean distance; NaN dimensions are ignored (not zero-filled)."""
    q = query.reshape(1, -1)
    valid = np.isfinite(q) & np.isfinite(matrix)
    delta = np.where(valid, matrix - q, 0.0)
    dist = np.sqrt(np.sum(delta * delta, axis=1))
    overlap = valid.sum(axis=1)
    dist = np.where(overlap > 0, dist, np.inf)
    return dist


def _band_reason(name: str, raw: float | None) -> str | None:
    if raw is None:
        return None
    if name == "close_vs_sma200_pct":
        return f"trend {raw * 100:.1f}%"
    if name == "pct_from_52w_high":
        return f"52w {raw * 100:.0f}%"
    if name == "volume_points":
        return f"vol pts {raw:.1f}"
    return None


def _shrink(value: float | None, prior: float | None, n: int, min_n: int) -> float | None:
    if value is None:
        return prior
    if prior is None or n >= min_n:
        return value
    w = n / float(min_n)
    return (w * float(value)) + ((1.0 - w) * float(prior))


def _calibration_note(p_win: float, deciles: list[dict[str, Any]]) -> str | None:
    if not deciles:
        return None
    # Deciles are stored as mean analog_p_win + realized PF / mean return.
    best: dict[str, Any] | None = None
    best_gap = 1e9
    for row in deciles:
        mean_score = _safe_float(row.get("mean_score") or row.get("mean_analog_p_win"))
        if mean_score is None:
            continue
        gap = abs(mean_score - p_win)
        if gap < best_gap:
            best_gap = gap
            best = row
    if best is None:
        return None
    pf = _safe_float(best.get("pf") or best.get("mean_pf"))
    if pf is None:
        return None
    return f"this analog decile historically realized PF {pf:.2f}"


def score_analog(
    signal_row: dict[str, Any],
    library: AnalogLibrary | dict[str, Any],
    *,
    k: int | None = None,
    min_n: int | None = None,
    regime: str | None = None,
) -> dict[str, Any] | None:
    """Score one signal against the winner/loser library.

    Returns None when the row cannot be scored (missing features, empty
    library, or no overlapping dimensions). Never returns a zero score for
    an unscored row.
    """
    lib = library if isinstance(library, AnalogLibrary) else library_from_artifact(library)
    if lib is None or lib.matrix is None or lib.matrix.size == 0:
        return None

    k_use = max(1, int(k if k is not None else lib.k or DEFAULT_K))
    min_use = max(1, int(min_n if min_n is not None else lib.min_n or DEFAULT_MIN_N))

    raw = raw_features_from_signal_row(signal_row)
    feats = {name: normalize_feature(name, raw.get(name), lib.bounds) for name in lib.feature_names}
    query = np.asarray([feats.get(name) if feats.get(name) is not None else np.nan for name in lib.feature_names], dtype=float)
    if not np.isfinite(query).any():
        return None

    row_regime = regime or resolve_regime_key(signal_row)
    era = str(signal_row.get("era") or (row_regime.split("|", 1)[0] if row_regime else "unknown"))

    idx = lib.regime_index.get(row_regime)
    used_regime = row_regime
    if idx is None or len(idx) < min_use:
        idx = lib.era_index.get(era)
        used_regime = era
    if idx is None or len(idx) < min_use:
        idx = np.arange(len(lib.rows), dtype=int)
        used_regime = "all_eras"
    if idx.size == 0:
        return None

    # Never let the query match itself when the library includes the same trade
    # (walk-forward tests + accidental self-join). Identity is ticker+date, not
    # a distance feature.
    q_ticker = str(signal_row.get("ticker") or "").upper()
    q_date = str(signal_row.get("entry_date") or signal_row.get("asof_date") or "")[:10]
    if q_ticker or q_date:
        keep: list[int] = []
        for i in idx.tolist():
            lib_row = lib.rows[i]
            same_ticker = str(lib_row.get("ticker") or "").upper() == q_ticker
            same_date = str(lib_row.get("entry_date") or "")[:10] == q_date
            if same_ticker and same_date and q_ticker and q_date:
                continue
            keep.append(i)
        idx = np.asarray(keep, dtype=int) if keep else idx
    if idx.size == 0:
        return None

    dist = _euclidean(query, lib.matrix[idx])
    order = np.argsort(dist)
    take_n = min(k_use, int(order.size))
    if take_n <= 0:
        return None
    chosen = idx[order[:take_n]]
    neighbors = [lib.rows[int(i)] for i in chosen]
    analog_n = len(neighbors)
    if analog_n <= 0:
        return None

    wins = [r for r in neighbors if r.get("is_winner")]
    losses = [r for r in neighbors if r.get("is_loser")]
    labeled_n = len(wins) + len(losses)
    raw_p_win = (len(wins) / labeled_n) if labeled_n else None
    neighbor_rets = [_safe_float(r.get("net_return")) for r in neighbors]
    neighbor_rets_f = [float(x) for x in neighbor_rets if x is not None]
    raw_pf = _safe_pf(neighbor_rets_f)

    prior = lib.priors.get(era) or lib.priors.get("all") or {}
    prior_p = _safe_float(prior.get("p_win"))
    prior_pf = _safe_float(prior.get("pf"))
    thin = analog_n < min_use
    p_win = _shrink(raw_p_win, prior_p, analog_n, min_use)
    hood_pf = _shrink(raw_pf, prior_pf, analog_n, min_use)
    if p_win is None and hood_pf is None:
        return None

    reasons: list[str] = []
    for name in ("close_vs_sma200_pct", "pct_from_52w_high", "volume_points"):
        note = _band_reason(name, raw.get(name))
        if note:
            reasons.append(note)
    if used_regime == "all_eras":
        reasons.append("thin neighborhood")
        thin = True
    elif thin:
        reasons.append("thin neighborhood")
    if used_regime not in {"all_eras", ""}:
        reasons.append(used_regime)

    display_neighbors: list[dict[str, Any]] = []
    for r in neighbors[:3]:
        display_neighbors.append(
            {
                "ticker": r.get("ticker"),
                "entry_date": str(r.get("entry_date") or "")[:10] or None,
                "net_return": None if _safe_float(r.get("net_return")) is None else round(float(r["net_return"]), 4),
                "hold_days": int(r["hold_days"]) if _safe_float(r.get("hold_days")) is not None else None,
                "exit_reason": r.get("exit_reason"),
            }
        )

    display_score = None if p_win is None else _clamp(float(p_win) * 100.0)
    if display_score is not None:
        display_score = apply_rank_v2_risk_caps(
            display_score,
            reliability_score=_safe_float(signal_row.get("reliability_score")),
            execution_score=_safe_float(signal_row.get("execution_score")),
            sec_risk_tag=str(signal_row.get("sec_risk_tag") or "unknown"),
            forensic_flags=list(signal_row.get("forensic_flags") or []),
        )

    cal = _calibration_note(float(p_win), lib.calibration_deciles) if p_win is not None else None

    return {
        "analog_p_win": None if p_win is None else round(float(p_win), 4),
        "analog_neighborhood_pf": None if hood_pf is None else round(float(hood_pf), 4),
        "analog_n": analog_n,
        "analog_thin": bool(thin),
        "analog_regime": used_regime,
        "analog_reasons": reasons[:4],
        "analog_neighbors": display_neighbors,
        "analog_score": None if display_score is None else round(float(display_score), 2),
        "analog_calibration_note": cal,
        "analog_model_id": lib.model_id,
        "analog_distance": DISTANCE_METRIC,
        "analog_raw_p_win": None if raw_p_win is None else round(float(raw_p_win), 4),
        "analog_raw_neighborhood_pf": None if raw_pf is None else round(float(raw_pf), 4),
    }


def compact_library_row(
    row: dict[str, Any],
    *,
    bounds: dict[str, Any],
) -> dict[str, Any] | None:
    """Normalized features + labels + display fields for one library member."""
    raw = raw_features_from_signal_row(row)
    feats = {name: normalize_feature(name, raw.get(name), bounds) for name in FEATURE_NAMES}
    if all(v is None for v in feats.values()):
        return None
    hold = _safe_float(row.get("hold_days"))
    return {
        "ticker": str(row.get("ticker") or "").upper() or None,
        "entry_date": str(row.get("entry_date") or row.get("asof_date") or "")[:10] or None,
        "era": str(row.get("era") or "unknown"),
        "regime": resolve_regime_key(row),
        "features": feats,
        "is_winner": bool(row.get("is_winner")),
        "is_loser": bool(row.get("is_loser")),
        "early_stopout": bool(row.get("early_stopout")),
        "mid_winner": bool(row.get("mid_winner")),
        "net_return": _safe_float(row.get("net_return") if row.get("net_return") is not None else row.get("ret_40d_fwd")),
        "hold_days": int(hold) if hold is not None else None,
        "exit_reason": row.get("exit_reason"),
    }
