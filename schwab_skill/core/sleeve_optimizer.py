"""Map sleeve desired-weights → net ticker book (cvxpy optional).

Does not invent entries. LIVE sizes what execution already would trade.
Every constraint is tagged S0, S1, S3, or allocator-only.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

import numpy as np

from core.multi_sleeve_constitution import CAP_CLUSTER, CAP_NAME, DD_HARD, SLEEVE_S0, SLEEVE_S1, SLEEVE_S3
from core.portfolio_allocator import apply_name_and_cluster_caps
from core.sleeve_weights import apply_hysteresis, net_exposure_book

LOG = logging.getLogger(__name__)

PolicySleeve = Literal["S0", "S1", "S3", "allocator-only"]


def _tag(policy: str, sleeve: PolicySleeve) -> dict[str, str]:
    return {"policy": policy, "sleeve": sleeve}


def optimize_net_weights(
    sleeve_targets: dict[str, dict[str, float]],
    *,
    corr: dict[tuple[str, str], float] | np.ndarray | None = None,
    tickers: list[str] | None = None,
    drawdown_from_peak: float | None = None,
    current_weights: dict[str, float] | None = None,
    sector_by_ticker: dict[str, str] | None = None,
    name_cap: float = CAP_NAME,
    cluster_cap: float = CAP_CLUSTER,
    corr_cap: float = 0.70,
    max_dd: float = DD_HARD,
    mode: str = "off",
    hysteresis_band: float = 0.01,
) -> dict[str, Any]:
    """
    Long-only net book with name/cluster/corr/DD/cash constraints.

    ``mode``: off returns the uncapped sleeve sum (still hysteresis if current
    given). shadow logs desired vs current and returns the unoptimized book for
    live trading. live returns optimized weights for sizing only.
    """
    mode_n = str(mode or "off").strip().lower()
    policies = [
        _tag("sleeve_targets", SLEEVE_S0),
        _tag("sleeve_targets", SLEEVE_S1),
        _tag("cash_residual", SLEEVE_S3),
        _tag("name_cap", "allocator-only"),
        _tag("cluster_cap", "allocator-only"),
        _tag("corr_cap", "allocator-only"),
        _tag("max_dd", "allocator-only"),
        _tag("long_only", "allocator-only"),
        _tag("hysteresis", "allocator-only"),
    ]
    book = net_exposure_book(sleeve_targets, sector_by_ticker=sector_by_ticker)
    desired = {str(k).upper(): max(0.0, float(v)) for k, v in (book.get("weights") or {}).items()}
    names = tickers or sorted(desired)
    names = [str(t).upper() for t in names]
    w = np.array([desired.get(t, 0.0) for t in names], dtype=np.float64)
    w = np.maximum(w, 0.0)
    notes: list[str] = []

    # Name cap (allocator-only; constitution CAP_NAME)
    for i, tkr in enumerate(names):
        if w[i] > name_cap:
            w[i] = name_cap
            notes.append(f"name_cap:{tkr}")

    # Sector / cluster cap
    capped_map = apply_name_and_cluster_caps(
        {n: float(x) for n, x in zip(names, w)},
        sector_by_ticker=sector_by_ticker,
        name_cap=name_cap,
        cluster_cap=cluster_cap,
    )
    w = np.array([capped_map.get(t, 0.0) for t in names], dtype=np.float64)

    # Pairwise corr cap: if corr(i,j) > cap, clip the smaller weight
    corr_mat = _as_corr_matrix(names, corr)
    if corr_mat is not None and corr_cap < 1.0:
        n = len(names)
        for i in range(n):
            for j in range(i + 1, n):
                if corr_mat[i, j] > corr_cap and (w[i] + w[j]) > cluster_cap:
                    scale = cluster_cap / max(w[i] + w[j], 1e-12)
                    w[i] *= scale
                    w[j] *= scale
                    notes.append(f"corr_cap:{names[i]}-{names[j]}")

    # Max DD: shrink gross into S3 cash
    gross = float(w.sum())
    if drawdown_from_peak is not None and abs(float(drawdown_from_peak)) >= max_dd:
        w[:] = 0.0
        notes.append("max_dd_flatten_to_s3")
    elif drawdown_from_peak is not None and abs(float(drawdown_from_peak)) >= max_dd * 0.66:
        w *= 0.5
        notes.append("max_dd_halve")

    # Optional cvxpy refinement (same constraints)
    w = _try_cvxpy(w, names, corr_mat, name_cap, cluster_cap, corr_cap, sector_by_ticker)

    w = np.maximum(w, 0.0)
    gross = float(w.sum())
    if gross > 1.0:
        w = w / gross
        gross = 1.0
    cash = max(0.0, 1.0 - gross)
    optimized = {n: float(x) for n, x in zip(names, w) if x > 1e-12}
    traded = apply_hysteresis(optimized, current_weights or {}, band=hysteresis_band)

    result = {
        "mode": mode_n,
        "desired": desired,
        "current": {str(k).upper(): float(v) for k, v in (current_weights or {}).items()},
        "weights": optimized if mode_n == "live" else desired,
        "optimized_weights": optimized,
        "trade_targets": traded if mode_n == "live" else {},
        "cash_weight": cash if mode_n == "live" else float(book.get("cash_weight") or 0.0),
        "s3_cash": cash,
        "gross": gross,
        "notes": notes,
        "policies": policies,
        "places_orders": False,
        "invents_entries": False,
    }
    if mode_n == "shadow":
        LOG.info(
            "allocator shadow desired vs current: desired_n=%s current_n=%s cash=%s",
            len(desired),
            len(result["current"]),
            cash,
        )
        result["weights"] = desired  # do not change live sizing in shadow
    if mode_n == "off":
        result["weights"] = desired
        result["optimized_weights"] = desired
    return result


def _as_corr_matrix(
    names: list[str],
    corr: dict[tuple[str, str], float] | np.ndarray | None,
) -> np.ndarray | None:
    n = len(names)
    if corr is None or n == 0:
        return None
    if isinstance(corr, np.ndarray):
        if corr.shape != (n, n):
            return None
        return corr
    mat = np.eye(n, dtype=np.float64)
    idx = {t: i for i, t in enumerate(names)}
    for (a, b), val in corr.items():
        i = idx.get(str(a).upper())
        j = idx.get(str(b).upper())
        if i is None or j is None:
            continue
        mat[i, j] = mat[j, i] = float(val)
    return mat


def _try_cvxpy(
    w: np.ndarray,
    names: list[str],
    corr_mat: np.ndarray | None,
    name_cap: float,
    cluster_cap: float,
    corr_cap: float,
    sector_by_ticker: dict[str, str] | None,
) -> np.ndarray:
    try:
        import cvxpy as cp
    except Exception:
        return w
    n = len(w)
    if n == 0:
        return w
    x = cp.Variable(n, nonneg=True)
    target = w.copy()
    obj = cp.Minimize(cp.sum_squares(x - target))
    cons = [x <= name_cap, cp.sum(x) <= 1.0]
    if sector_by_ticker:
        sectors: dict[str, list[int]] = {}
        for i, tkr in enumerate(names):
            sec = str(sector_by_ticker.get(tkr) or "UNKNOWN").upper()
            sectors.setdefault(sec, []).append(i)
        for idxs in sectors.values():
            cons.append(cp.sum(x[idxs]) <= cluster_cap)
    if corr_mat is not None:
        for i in range(n):
            for j in range(i + 1, n):
                if corr_mat[i, j] > corr_cap:
                    cons.append(x[i] + x[j] <= cluster_cap)
    try:
        prob = cp.Problem(obj, cons)
        prob.solve(solver=cp.SCS, verbose=False)
        if x.value is None:
            return w
        return np.maximum(np.asarray(x.value, dtype=np.float64).reshape(-1), 0.0)
    except Exception as exc:
        LOG.warning("cvxpy optimizer failed, using heuristic: %s", exc)
        return w
