"""As-of universe membership, PIT fundamental join, and coverage gating."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

from research.pit_protocols import PIT_FUNDAMENTAL_FIELDS, PITFundamentalProvider, PITUniverseProvider
from research.registry import feature_entries, load_feature_registry

LOG = logging.getLogger(__name__)


def universe_asof(
    asof: str,
    provider: PITUniverseProvider,
) -> list[str]:
    """Return PIT membership for ``asof``. Empty list is fail-closed, not 'use Wikipedia'."""
    names = [str(t).upper() for t in provider.universe_asof(asof) if str(t).strip()]
    dq = provider.data_quality()
    status = str(dq.get("status") or "")
    if status not in {"ok", ""}:
        LOG.warning("PIT universe data_quality: %s", dq)
    return names


def join_pit_fundamentals(
    candidates: pd.DataFrame,
    provider: PITFundamentalProvider,
    *,
    asof_col: str = "asof_date",
    ticker_col: str = "ticker",
) -> pd.DataFrame:
    """Left-join as-of fundamentals. Missing values stay NA — never filled with 0."""
    if candidates is None or candidates.empty:
        return pd.DataFrame()
    work = candidates.copy()
    work[ticker_col] = work[ticker_col].astype(str).str.upper()
    work["_asof"] = pd.to_datetime(work[asof_col]).dt.strftime("%Y-%m-%d")
    rows: list[dict[str, float | None]] = []
    for tkr, asof in zip(work[ticker_col], work["_asof"]):
        raw = provider.fundamentals_asof(str(tkr), str(asof))
        clean: dict[str, float | None] = {}
        for key in PIT_FUNDAMENTAL_FIELDS:
            val = raw.get(key)
            if val is None:
                clean[key] = None
                continue
            try:
                fval = float(val)
            except (TypeError, ValueError):
                clean[key] = None
                continue
            if fval != fval:
                clean[key] = None
                continue
            clean[key] = fval
        rows.append(clean)
    fund = pd.DataFrame(rows)
    for col in PIT_FUNDAMENTAL_FIELDS:
        if col not in fund.columns:
            fund[col] = pd.NA
        work[col] = fund[col].values
    work = work.drop(columns=["_asof"])
    dq = provider.data_quality()
    if str(dq.get("status") or "") not in {"ok", ""}:
        LOG.warning("PIT fundamentals data_quality: %s", dq)
    return work


def field_coverage(df: pd.DataFrame, fields: list[str] | tuple[str, ...] = PIT_FUNDAMENTAL_FIELDS) -> dict[str, float]:
    """Non-null fraction per field. Zeros that were invented would inflate this — we never invent."""
    out: dict[str, float] = {}
    n = max(len(df), 1)
    for name in fields:
        if name not in df.columns:
            out[name] = 0.0
            continue
        series = pd.to_numeric(df[name], errors="coerce")
        out[name] = float(series.notna().mean()) if n else 0.0
    return out


def coverage_unlocks(
    coverage: dict[str, float],
    *,
    skill_dir: Path | None = None,
    registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Report which deferred PIT fields may be enabled.

    The committed registry stays ``enabled: false`` until coverage clears the
    documented threshold. This function never writes zeros and never flips the
    on-disk registry.
    """
    from config import get_pit_fundamental_coverage_min

    floor = get_pit_fundamental_coverage_min(skill_dir)
    reg = registry if registry is not None else load_feature_registry()
    unlocked: list[str] = []
    blocked: list[dict[str, Any]] = []
    for feat in feature_entries(reg):
        name = str(feat.get("name") or "")
        if str(feat.get("status") or "") != "deferred":
            continue
        if str(feat.get("data_source") or "") not in {"fundamentals", "macro"}:
            continue
        threshold = float(feat.get("coverage_threshold") or floor)
        cov = float(coverage.get(name) or 0.0)
        if bool(feat.get("enabled")):
            # Already enabled in the file — still report coverage.
            if cov >= threshold:
                unlocked.append(name)
            else:
                blocked.append({"name": name, "coverage": cov, "threshold": threshold, "reason": "enabled_but_below_threshold"})
            continue
        if cov >= threshold:
            unlocked.append(name)
        else:
            blocked.append({"name": name, "coverage": round(cov, 4), "threshold": threshold, "reason": "below_coverage_threshold"})
    return {
        "coverage_floor": floor,
        "unlocked": unlocked,
        "blocked": blocked,
        "registry_enabled_deferred": [
            str(f.get("name"))
            for f in feature_entries(reg)
            if str(f.get("status")) == "deferred" and bool(f.get("enabled"))
        ],
    }


def replay_membership_delta(
    asof: str,
    provider: PITUniverseProvider,
    *,
    trades: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Compare PIT membership vs Wikipedia-now. Does not replace the Schwab-only runner."""
    pit = set(universe_asof(asof, provider))
    wiki = {str(t).upper() for t in (provider.wikipedia_now() or [])}
    only_wiki = sorted(wiki - pit)
    only_pit = sorted(pit - wiki)
    dq = provider.data_quality()
    report: dict[str, Any] = {
        "asof": str(asof)[:10],
        "provider": getattr(provider, "provider_id", "unknown"),
        "n_pit": len(pit),
        "n_wikipedia_now": len(wiki),
        "n_only_wikipedia_now": len(only_wiki),
        "n_only_pit": len(only_pit),
        "only_wikipedia_now_sample": only_wiki[:20],
        "canonical_runner_replaced": False,
        "data_quality": dq,
        "banner": (
            "PIT replay is diagnostic only. Do not silently replace "
            "run_multi_era_backtest_schwab_only.py until the delta is reviewed."
        ),
    }
    if str(dq.get("universe_kind") or "") == "sp500":
        report["universe_kind"] = "sp500"
        report["trade_book_caveat"] = (
            "PIT and Wikipedia-now are S&P 500 membership. Trade chunks may "
            "include names outside SP500. Trade-count/PF delta is survivorship "
            "on the overlapping names, not a full SP1500 PIT replay."
        )
    if str(dq.get("status") or "") != "ok":
        report["trade_count_pit"] = None
        report["trade_count_wikipedia_now"] = None
        report["trade_count_delta"] = None
        report["pf_pit"] = None
        report["pf_wikipedia_now"] = None
        report["pf_delta"] = None
        return report
    if str(dq.get("provider") or "") == "sharadar" and len(wiki) < 50:
        report["comparison_reason"] = "wikipedia_now_too_small"
        report["trade_count_pit"] = None
        report["trade_count_wikipedia_now"] = None
        report["trade_count_delta"] = None
        report["pf_pit"] = None
        report["pf_wikipedia_now"] = None
        report["pf_delta"] = None
        return report
    if trades is None or trades.empty or "ticker" not in trades.columns:
        return report
    work = trades.copy()
    work["ticker"] = work["ticker"].astype(str).str.upper()
    ret_col = "net_return" if "net_return" in work.columns else None
    pit_mask = work["ticker"].isin(pit)
    wiki_mask = work["ticker"].isin(wiki)
    report["trade_count_pit"] = int(pit_mask.sum())
    report["trade_count_wikipedia_now"] = int(wiki_mask.sum())
    report["trade_count_delta"] = int(wiki_mask.sum()) - int(pit_mask.sum())
    if ret_col:
        report["pf_pit"] = _pf(work.loc[pit_mask, ret_col])
        report["pf_wikipedia_now"] = _pf(work.loc[wiki_mask, ret_col])
        pit_pf = report["pf_pit"]
        wiki_pf = report["pf_wikipedia_now"]
        if pit_pf is not None and wiki_pf is not None:
            report["pf_delta"] = round(float(pit_pf) - float(wiki_pf), 4)
        else:
            report["pf_delta"] = None
    return report


def _pf(series: pd.Series) -> float | None:
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return None
    wins = float(s[s > 0].sum())
    losses = float(-s[s <= 0].sum())
    if losses <= 0:
        return None if wins <= 0 else 99.0
    return round(wins / losses, 4)
