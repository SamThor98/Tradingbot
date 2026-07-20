"""Append-only ledger of live-scan prob-rank shadow evidence vs rank-v2."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)

LEDGER_DIRNAME = "prob_rank_shadow_evidence"
LEDGER_FILENAME = "shadow_scans.jsonl"


def shadow_evidence_dir(skill_dir: Path) -> Path:
    return Path(skill_dir) / "validation_artifacts" / LEDGER_DIRNAME


def ledger_path(skill_dir: Path) -> Path:
    return shadow_evidence_dir(skill_dir) / LEDGER_FILENAME


def _ticker(signal: dict[str, Any]) -> str:
    return str(signal.get("ticker") or signal.get("symbol") or "").upper().strip()


def _rank_v2_score(signal: dict[str, Any]) -> float | None:
    for key in ("rank_score_v2", "rank_score", "composite_score", "signal_score"):
        val = signal.get(key)
        if val is None:
            continue
        try:
            return float(val)
        except (TypeError, ValueError):
            continue
    return None


def _prob_would_keep(signal: dict[str, Any]) -> bool:
    sel = signal.get("prob_rank_selection")
    if isinstance(sel, dict) and "would_keep" in sel:
        return bool(sel.get("would_keep"))
    block = signal.get("prob_rank") if isinstance(signal.get("prob_rank"), dict) else {}
    return bool(block.get("would_keep"))


def build_shadow_evidence_record(
    signals: list[dict[str, Any]],
    diagnostics: dict[str, Any],
    *,
    skill_dir: Path | None = None,
    scan_label: str | None = None,
) -> dict[str, Any] | None:
    """
    Build one ledger row comparing prob-rank would_keep vs rank-v2 top-N.

    Returns None when mode is off or there is nothing useful to record.
    """
    mode = str(diagnostics.get("prob_rank_mode") or "off").lower()
    if mode == "off":
        return None

    top_n = int(diagnostics.get("prob_rank_top_n") or 5)
    top_n = max(1, top_n)

    scored = [s for s in signals if s.get("expected_return_40d") is not None]
    if not scored and not signals:
        return None

    prob_keep = {_ticker(s) for s in signals if _ticker(s) and _prob_would_keep(s)}
    # Rank-v2 control arm: top-N by rank_score_v2 among current cohort
    ranked_v2 = sorted(
        (s for s in signals if _ticker(s) and _rank_v2_score(s) is not None),
        key=lambda s: float(_rank_v2_score(s) or -1e9),
        reverse=True,
    )
    v2_keep = {_ticker(s) for s in ranked_v2[:top_n]}

    inter = prob_keep & v2_keep
    union = prob_keep | v2_keep
    jaccard = (len(inter) / len(union)) if union else None
    only_prob = sorted(prob_keep - v2_keep)
    only_v2 = sorted(v2_keep - prob_keep)

    model_ids: list[str] = []
    for s in scored:
        mid = None
        block = s.get("prob_rank") if isinstance(s.get("prob_rank"), dict) else {}
        mid = block.get("model_id") or s.get("prob_rank_model_id")
        if mid:
            model_ids.append(str(mid))
    model_id = sorted(set(model_ids))[0] if model_ids else None

    keep_rows: list[dict[str, Any]] = []
    for s in signals:
        t = _ticker(s)
        if not t or t not in union:
            continue
        er = s.get("expected_return_40d")
        try:
            er_f = float(er) if er is not None else None
        except (TypeError, ValueError):
            er_f = None
        keep_rows.append(
            {
                "ticker": t,
                "expected_return_40d": er_f,
                "rank_score_v2": _rank_v2_score(s),
                "prob_would_keep": t in prob_keep,
                "rank_v2_top_n": t in v2_keep,
                "prob_rank": (
                    s.get("prob_rank_cross_section_rank")
                    or (s.get("prob_rank") or {}).get("cross_section_rank")
                ),
            }
        )
    keep_rows.sort(key=lambda r: float(r.get("expected_return_40d") or -1e9), reverse=True)

    return {
        "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scan_label": scan_label,
        "prob_rank_mode": mode,
        "model_id": model_id,
        "top_n": top_n,
        "n_signals": len(signals),
        "n_scored": int(diagnostics.get("prob_rank_scored") or len(scored)),
        "n_unscored": int(diagnostics.get("prob_rank_unscored") or max(0, len(signals) - len(scored))),
        "prob_would_keep_n": len(prob_keep),
        "rank_v2_top_n_count": len(v2_keep),
        "overlap_n": len(inter),
        "jaccard": round(float(jaccard), 4) if jaccard is not None else None,
        "only_prob_rank": only_prob,
        "only_rank_v2": only_v2,
        "diagnostics": {
            k: diagnostics.get(k)
            for k in (
                "prob_rank_would_keep",
                "prob_rank_would_drop",
                "prob_rank_dropped",
                "prob_rank_score_failed",
                "rank_filter_v2_mode",
                "rank_filter_v2_would_drop",
                "signals_emitted",
                "stage_a_candidates",
            )
            if k in diagnostics
        },
        "keep_rows": keep_rows[: max(top_n * 3, 15)],
        "skill_dir": str(skill_dir) if skill_dir is not None else None,
    }


def append_shadow_evidence(
    skill_dir: Path,
    record: dict[str, Any],
) -> Path:
    """Append one JSONL record; creates the ledger directory if needed."""
    path = ledger_path(skill_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")
    return path


def record_shadow_evidence(
    signals: list[dict[str, Any]],
    diagnostics: dict[str, Any],
    skill_dir: Path,
    *,
    scan_label: str | None = None,
) -> dict[str, Any] | None:
    """Build + append; never raises. Sets diagnostics['prob_rank_shadow_evidence']."""
    try:
        record = build_shadow_evidence_record(
            signals,
            diagnostics,
            skill_dir=skill_dir,
            scan_label=scan_label,
        )
        if record is None:
            return None
        path = append_shadow_evidence(skill_dir, record)
        ledger_summary = summarize_shadow_evidence(load_shadow_evidence_records(skill_dir))
        diagnostics["prob_rank_shadow_evidence"] = {
            "written": True,
            "path": str(path),
            "jaccard": record.get("jaccard"),
            "overlap_n": record.get("overlap_n"),
            "prob_would_keep_n": record.get("prob_would_keep_n"),
            "rank_v2_top_n_count": record.get("rank_v2_top_n_count"),
            "only_prob_rank": record.get("only_prob_rank"),
            "only_rank_v2": record.get("only_rank_v2"),
            "ledger_n_scans": ledger_summary.get("n_scans"),
            "ledger_mean_jaccard": ledger_summary.get("mean_jaccard"),
        }
        LOG.info(
            "Prob-rank shadow evidence: overlap=%s/%s jaccard=%s -> %s",
            record.get("overlap_n"),
            record.get("top_n"),
            record.get("jaccard"),
            path.name,
        )
        return record
    except Exception as exc:
        LOG.warning("Prob-rank shadow evidence write skipped: %s", exc)
        diagnostics["prob_rank_shadow_evidence"] = {"written": False, "error": str(exc)}
        return None


def load_shadow_evidence_records(skill_dir: Path) -> list[dict[str, Any]]:
    path = ledger_path(skill_dir)
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def seed_records_from_scored_trades(
    merged: Any,
    *,
    top_n: int = 5,
    model_id: str | None = None,
    min_cohort: int = 8,
    max_days: int = 40,
    score_col: str = "expected_return_40d",
    rank_v2_col: str = "rank_score_v2",
    scan_label_prefix: str = "cf_day",
) -> list[dict[str, Any]]:
    """
    Build ledger-shaped records from scored trade rows grouped by entry date.

    ``merged`` must include ticker, entry_date (or entry_iso), ``score_col``,
    and ``rank_v2_col``. Used to bootstrap multi-name Jaccard evidence from
    offline dual-run joins without waiting for live Stage-B breadth.
    """
    import pandas as pd

    if merged is None or getattr(merged, "empty", True):
        return []
    df = merged.copy()
    df["ticker"] = df["ticker"].astype(str).str.upper()
    if "entry_iso" not in df.columns:
        df["entry_iso"] = pd.to_datetime(df["entry_date"]).dt.strftime("%Y-%m-%d")
    if score_col not in df.columns or rank_v2_col not in df.columns:
        return []
    df = df[df[score_col].notna() & df[rank_v2_col].notna()]
    if df.empty:
        return []

    # Prefer larger cohorts; cap how many days we seed
    sizes = df.groupby("entry_iso").size().sort_values(ascending=False)
    dates = [d for d, n in sizes.items() if int(n) >= int(min_cohort)][: int(max_days)]
    records: list[dict[str, Any]] = []
    top_n = max(1, int(top_n))
    for day in dates:
        grp = df[df["entry_iso"] == day]
        ordered = grp.sort_values(score_col, ascending=False)
        keep_tickers = set(ordered.head(top_n)["ticker"].tolist())
        signals: list[dict[str, Any]] = []
        for _, row in grp.iterrows():
            t = str(row["ticker"])
            signals.append(
                {
                    "ticker": t,
                    "expected_return_40d": float(row[score_col]),
                    "rank_score_v2": float(row[rank_v2_col]),
                    "prob_rank_selection": {"would_keep": t in keep_tickers, "top_n": top_n},
                    "prob_rank": {
                        "model_id": model_id,
                        "cross_section_rank": None,
                    },
                }
            )
        diagnostics = {
            "prob_rank_mode": "shadow",
            "prob_rank_top_n": top_n,
            "prob_rank_scored": len(signals),
            "prob_rank_unscored": 0,
        }
        era = None
        if "era" in grp.columns and len(grp):
            era = str(grp["era"].iloc[0])
        label = f"{scan_label_prefix}:{day}" + (f":{era}" if era else "")
        rec = build_shadow_evidence_record(signals, diagnostics, scan_label=label)
        if rec is not None:
            rec["source"] = "cf_day_cohort"
            if era:
                rec["era"] = era
            records.append(rec)
    return records


def summarize_shadow_evidence(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate Jaccard / overlap / disagreement across recorded scans."""
    if not records:
        return {
            "n_scans": 0,
            "note": "No shadow evidence rows yet — run scans with PROB_RANK_MODE=shadow",
        }
    jaccards = [float(r["jaccard"]) for r in records if r.get("jaccard") is not None]
    overlaps = [int(r["overlap_n"]) for r in records if r.get("overlap_n") is not None]
    only_prob_counts = [len(r.get("only_prob_rank") or []) for r in records]
    only_v2_counts = [len(r.get("only_rank_v2") or []) for r in records]
    model_ids = sorted({str(r.get("model_id")) for r in records if r.get("model_id")})
    return {
        "n_scans": len(records),
        "model_ids": model_ids,
        "mean_jaccard": round(sum(jaccards) / len(jaccards), 4) if jaccards else None,
        "min_jaccard": round(min(jaccards), 4) if jaccards else None,
        "max_jaccard": round(max(jaccards), 4) if jaccards else None,
        "mean_overlap_n": round(sum(overlaps) / len(overlaps), 3) if overlaps else None,
        "mean_only_prob_rank": round(sum(only_prob_counts) / len(only_prob_counts), 3),
        "mean_only_rank_v2": round(sum(only_v2_counts) / len(only_v2_counts), 3),
        "first_ts": records[0].get("ts_utc"),
        "last_ts": records[-1].get("ts_utc"),
        "latest": {
            "ts_utc": records[-1].get("ts_utc"),
            "jaccard": records[-1].get("jaccard"),
            "overlap_n": records[-1].get("overlap_n"),
            "only_prob_rank": records[-1].get("only_prob_rank"),
            "only_rank_v2": records[-1].get("only_rank_v2"),
            "model_id": records[-1].get("model_id"),
        },
        "note": (
            "Shadow evidence only — does not change selection. "
            "Do not enable PROB_RANK_MODE=live from this ledger alone."
        ),
    }
