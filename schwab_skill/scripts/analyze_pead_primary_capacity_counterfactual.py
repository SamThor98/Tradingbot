#!/usr/bin/env python3
"""PEAD-primary capacity-aware counterfactual (Track B, research only).

Runs PEAD-native selection + portfolio capacity notes on the frozen
``pead_primary_aug_fixed_full_c40`` book / entry-timing cache.

Does NOT promote PEAD live or graft Stage2 breakout buffer as default policy.

Usage (from schwab_skill/):
  python scripts/analyze_pead_primary_capacity_counterfactual.py
  python scripts/analyze_pead_primary_capacity_counterfactual.py --run-id pead_primary_aug_fixed_full_c40
  python scripts/analyze_pead_primary_capacity_counterfactual.py --skip-exit-replay
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from config import (  # noqa: E402
    get_backtest_portfolio_max_positions,
    get_backtest_portfolio_starting_equity,
    get_backtest_position_size_pct,
    get_backtest_risk_per_trade_pct,
)
from core.pead_primary_capacity_cf import (  # noqa: E402
    PEAD_NATIVE_SCORE_COLS,
    PF_MEAN_TARGET,
    WORST_ERA_FLOOR,
    portfolio_note,
    recommend_operating_stack,
    select_percentile_arm,
    select_top_n_arm,
    select_transfer_breakout_buffer,
    select_transfer_pts_cap,
    select_transfer_rank_v2,
    summarize_arm,
    trade_key,
)
from logger_setup import get_logger, setup_logging  # noqa: E402
from scripts.analyze_entry_timing_shadow_counterfactual import (  # noqa: E402
    _load_replay_cache,
)
from scripts.analyze_signal_stack_counterfactual import (  # noqa: E402
    DEFAULT_EXIT_PROFILE,
    _replay_exit_grace_rows,
)
from scripts.phase2_common import load_trades  # noqa: E402
from scripts.validate_scoring_metrics import _load_trade_frame  # noqa: E402

LOG = get_logger("analyze_pead_primary_capacity_counterfactual")
ART = SKILL_DIR / "validation_artifacts"
DEFAULT_RUN_ID = "pead_primary_aug_fixed_full_c40"
TOP_N_GRID = (3, 5, 8, 10)
PERCENTILE_GRID = (50.0, 60.0, 70.0)
MAX_POS_GRID = (5, 8, 10)
EXIT_PROFILES = ("exit_grace_t15_h40", "exit_grace_t10_h40", "exit_grace_t15_h30")


def _merge_book(run_id: str) -> pd.DataFrame:
    """Join chunk trades (dates/stops/returns) with entry-timing cache scores."""
    chunk_trades = load_trades(run_id)
    if not chunk_trades:
        raise RuntimeError(f"No trades for run_id={run_id}")
    rows: list[dict[str, Any]] = []
    for t in chunk_trades:
        rows.append(
            {
                "era": t.era,
                "ticker": str(t.ticker or "").upper(),
                "entry_date": t.entry_date,
                "exit_date": t.exit_date,
                "net_return": float(t.net_ret),
                "stop_pct": float(t.stop_pct),
                "hold_days": int(t.hold_days),
                "signal_score": t.signal_score,
            }
        )
    trades = pd.DataFrame(rows)
    trades["entry_iso"] = pd.to_datetime(trades["entry_date"]).dt.strftime("%Y-%m-%d")

    # Optional scoring enrichment from validate_scoring path (rank_score_v2 etc.).
    try:
        scored = _load_trade_frame(run_id)
        scored = scored.copy()
        scored["ticker"] = scored["ticker"].astype(str).str.upper()
        scored["entry_iso"] = pd.to_datetime(scored["entry_date"]).dt.strftime("%Y-%m-%d")
        score_from_chunks = [
            c
            for c in (
                "signal_score",
                "composite_score",
                "edge_score",
                "reliability_score",
                "rank_score_v2",
                "pts_52w",
            )
            if c in scored.columns
        ]
        if score_from_chunks:
            scored_small = scored[["era", "ticker", "entry_iso", *score_from_chunks]].drop_duplicates(
                subset=["era", "ticker", "entry_iso"], keep="last"
            )
            trades = trades.drop(columns=[c for c in score_from_chunks if c in trades.columns], errors="ignore")
            trades = trades.merge(scored_small, on=["era", "ticker", "entry_iso"], how="left")
    except Exception as exc:
        LOG.warning("Chunk score enrichment skipped: %s", exc)

    cache = _load_replay_cache(run_id)
    if cache is None or cache.empty:
        LOG.warning("Entry-timing cache missing for %s; using chunk scores only", run_id)
        return trades

    cache = cache.copy()
    cache["ticker"] = cache["ticker"].astype(str).str.upper()
    cache["entry_iso"] = pd.to_datetime(cache["entry_date"]).dt.strftime("%Y-%m-%d")
    score_cols = [
        c
        for c in (
            "signal_score",
            "composite_score",
            "edge_score",
            "reliability_score",
            "rank_score_v2",
            "pts_52w",
            "breakout_buffer_pct",
            "pct_above_sma50",
        )
        if c in cache.columns
    ]
    keep = ["era", "ticker", "entry_iso", *score_cols]
    cache_small = cache[keep].drop_duplicates(subset=["era", "ticker", "entry_iso"], keep="last")
    # Prefer cache scores when present (includes breakout_buffer_pct).
    drop_overlap = [c for c in score_cols if c in trades.columns]
    base = trades.drop(columns=drop_overlap, errors="ignore")
    merged = base.merge(cache_small, on=["era", "ticker", "entry_iso"], how="left", suffixes=("", "_cache"))
    return merged


def _apply_exit_returns(df: pd.DataFrame, grace_rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not grace_rows:
        return df.copy()
    gmap: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in grace_rows:
        key = trade_key(row.get("era"), row.get("ticker"), row.get("entry_date"))
        gmap[key] = row
    out = df.copy()
    nets: list[float] = []
    holds: list[int] = []
    exits: list[Any] = []
    for r in out.itertuples(index=False):
        key = trade_key(getattr(r, "era", ""), getattr(r, "ticker", ""), getattr(r, "entry_date", None))
        g = gmap.get(key)
        if g is None:
            nets.append(float(getattr(r, "net_return", 0.0) or 0.0))
            holds.append(int(getattr(r, "hold_days", 0) or 0))
            exits.append(getattr(r, "exit_date", None))
            continue
        net = g.get("net_return", g.get("net_ret"))
        nets.append(float(net if net is not None else getattr(r, "net_return", 0.0) or 0.0))
        hd = g.get("hold_days")
        holds.append(int(hd if hd is not None else getattr(r, "hold_days", 0) or 0))
        exits.append(g.get("exit_date") or getattr(r, "exit_date", None))
    out["net_return"] = nets
    out["hold_days"] = holds
    out["exit_date"] = exits
    return out


def _load_or_replay_exit(
    run_id: str,
    profile: str,
    *,
    skip: bool,
) -> list[dict[str, Any]]:
    cache_path = ART / f"pead_primary_exit_grace_replay_{run_id}_{profile}.json"
    if cache_path.exists():
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            rows = payload.get("rows") if isinstance(payload, dict) else payload
            if isinstance(rows, list) and rows:
                LOG.info("Loaded exit-grace cache %s (%s rows)", cache_path.name, len(rows))
                return rows
        except Exception as exc:
            LOG.warning("Failed reading exit-grace cache %s: %s", cache_path, exc)
    if skip:
        LOG.info("Skipping exit replay for %s (--skip-exit-replay)", profile)
        return []
    LOG.info("Replaying exit profile %s for %s …", profile, run_id)
    rows = _replay_exit_grace_rows(run_id, profile_name=profile, data_provider="chunk")
    cache_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "run_id": run_id,
                "profile": profile,
                "n_rows": len(rows),
                "rows": rows,
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    LOG.info("Wrote %s (%s rows)", cache_path.name, len(rows))
    return rows


def _attach_portfolio(
    summary: dict[str, Any],
    df: pd.DataFrame,
    *,
    max_positions: int,
    risk_per_trade_pct: float,
    position_size_pct: float,
    starting_equity: float,
    score_col: str | None,
) -> dict[str, Any]:
    note = portfolio_note(
        df,
        max_positions=max_positions,
        risk_per_trade_pct=risk_per_trade_pct,
        position_size_pct=position_size_pct,
        starting_equity=starting_equity,
        score_col=score_col,
    )
    out = dict(summary)
    out["portfolio"] = note
    return out


def build_arms(
    book: pd.DataFrame,
    *,
    exit_books: dict[str, pd.DataFrame],
    max_positions: int,
    risk_per_trade_pct: float,
    position_size_pct: float,
    starting_equity: float,
    max_pos_grid: tuple[int, ...],
) -> list[dict[str, Any]]:
    baseline_n = len(book)
    arms: list[dict[str, Any]] = []

    bare = summarize_arm(book, label="bare_full_book", baseline_n=baseline_n, family="baseline")
    arms.append(
        _attach_portfolio(
            bare,
            book,
            max_positions=max_positions,
            risk_per_trade_pct=risk_per_trade_pct,
            position_size_pct=position_size_pct,
            starting_equity=starting_equity,
            score_col="signal_score",
        )
    )

    # Capacity grid on bare book (notes only; same PF as bare).
    for mp in max_pos_grid:
        cap = summarize_arm(
            book,
            label=f"capacity_bare_max{mp}_risk{risk_per_trade_pct:.4f}",
            baseline_n=baseline_n,
            family="capacity",
        )
        arms.append(
            _attach_portfolio(
                cap,
                book,
                max_positions=mp,
                risk_per_trade_pct=risk_per_trade_pct,
                position_size_pct=position_size_pct,
                starting_equity=starting_equity,
                score_col="signal_score",
            )
        )

    for profile, edf in exit_books.items():
        s = summarize_arm(
            edf,
            label=f"{profile}_full_book",
            baseline_n=baseline_n,
            family="exit_grace",
        )
        arms.append(
            _attach_portfolio(
                s,
                edf,
                max_positions=max_positions,
                risk_per_trade_pct=risk_per_trade_pct,
                position_size_pct=position_size_pct,
                starting_equity=starting_equity,
                score_col="signal_score",
            )
        )

    for score_col in PEAD_NATIVE_SCORE_COLS:
        if score_col not in book.columns or book[score_col].notna().sum() < 50:
            continue
        for top_n in TOP_N_GRID:
            selected = select_top_n_arm(book, score_col=score_col, top_n=top_n)
            s = summarize_arm(
                selected,
                label=f"top{top_n}_by_{score_col}",
                baseline_n=baseline_n,
                family="top_n",
            )
            arms.append(
                _attach_portfolio(
                    s,
                    selected,
                    max_positions=max_positions,
                    risk_per_trade_pct=risk_per_trade_pct,
                    position_size_pct=position_size_pct,
                    starting_equity=starting_equity,
                    score_col=score_col,
                )
            )
        for pct in PERCENTILE_GRID:
            selected = select_percentile_arm(book, score_col=score_col, min_percentile=pct)
            s = summarize_arm(
                selected,
                label=f"{score_col}_p{int(pct)}",
                baseline_n=baseline_n,
                family="percentile",
            )
            arms.append(
                _attach_portfolio(
                    s,
                    selected,
                    max_positions=max_positions,
                    risk_per_trade_pct=risk_per_trade_pct,
                    position_size_pct=position_size_pct,
                    starting_equity=starting_equity,
                    score_col=score_col,
                )
            )

    # Transfer arms: reject unless they independently clear strict gates.
    transfer_specs: list[tuple[str, pd.DataFrame]] = [
        ("transfer_breakout_buffer_ge_0.010", select_transfer_breakout_buffer(book, min_buf=0.01)),
        ("transfer_pts_52w_cap_37", select_transfer_pts_cap(book, cap=37.0)),
        ("transfer_rank_v2_p76", select_transfer_rank_v2(book, percentile=76.0)),
    ]
    for label, selected in transfer_specs:
        s = summarize_arm(
            selected,
            label=label,
            baseline_n=baseline_n,
            family="transfer",
            is_transfer_arm=True,
        )
        arms.append(
            _attach_portfolio(
                s,
                selected,
                max_positions=max_positions,
                risk_per_trade_pct=risk_per_trade_pct,
                position_size_pct=position_size_pct,
                starting_equity=starting_equity,
                score_col="signal_score" if "signal_score" in selected.columns else None,
            )
        )
    return arms


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--skip-exit-replay", action="store_true")
    parser.add_argument(
        "--exit-profiles",
        nargs="*",
        default=list(EXIT_PROFILES),
        help="Exit-grace profiles to replay/evaluate (default: t15/h40, t10/h40, t15/h30).",
    )
    parser.add_argument("--out", type=str, default=None)
    args = parser.parse_args(argv)
    setup_logging()

    run_id = str(args.run_id)
    book = _merge_book(run_id)
    LOG.info("Loaded PEAD book %s trades=%s", run_id, len(book))

    # Sanity: chunks exist for all eras.
    eras_present = sorted({str(e) for e in book["era"].dropna().unique()})
    if len(eras_present) < 5:
        LOG.error("Expected 5 eras, found %s: %s", len(eras_present), eras_present)
        return 2

    max_positions = int(get_backtest_portfolio_max_positions(SKILL_DIR))
    risk = float(get_backtest_risk_per_trade_pct(SKILL_DIR))
    pos_pct = float(get_backtest_position_size_pct(SKILL_DIR))
    starting = float(get_backtest_portfolio_starting_equity(SKILL_DIR))

    exit_books: dict[str, pd.DataFrame] = {}
    for profile in args.exit_profiles or []:
        if profile not in EXIT_PROFILES and profile != DEFAULT_EXIT_PROFILE:
            LOG.warning("Unknown exit profile %s — skipping", profile)
            continue
        rows = _load_or_replay_exit(run_id, profile, skip=bool(args.skip_exit_replay))
        if not rows:
            # Fall back to citing bare book when replay skipped/unavailable.
            continue
        exit_books[profile] = _apply_exit_returns(book, rows)

    arms = build_arms(
        book,
        exit_books=exit_books,
        max_positions=max_positions,
        risk_per_trade_pct=risk,
        position_size_pct=pos_pct,
        starting_equity=starting,
        max_pos_grid=MAX_POS_GRID,
    )
    recommendation = recommend_operating_stack(arms)

    passing = [a for a in arms if a.get("passes_pf_150") and not a.get("is_transfer_arm")]
    failing_transfer = [
        a for a in arms if a.get("is_transfer_arm") and not a.get("passes_pf_150")
    ]
    clearing_transfer = [
        a for a in arms if a.get("is_transfer_arm") and a.get("passes_pf_150")
    ]

    # Dual-admit invariant: PEAD-only never executable; ALLOW_LIVE must stay false.
    from config import (
        get_strategy_pead_primary_allow_live,
        get_strategy_pead_primary_effective_mode,
        get_strategy_pead_primary_mode,
    )
    from signal_scanner import _pead_primary_is_executable

    allow_live = bool(get_strategy_pead_primary_allow_live(SKILL_DIR))
    pead_mode = get_strategy_pead_primary_mode(SKILL_DIR)
    pead_eff = get_strategy_pead_primary_effective_mode(SKILL_DIR)
    pead_only_executable = bool(_pead_primary_is_executable("pead_primary"))
    dual_admit = {
        "strategy_pead_primary_mode": pead_mode,
        "strategy_pead_primary_effective_mode": pead_eff,
        "strategy_pead_primary_allow_live": allow_live,
        "pead_only_is_executable": pead_only_executable,
        "executable_stage2_only_expected": True,
        "leak_check_pass": (pead_only_executable is False) and (allow_live is False),
        "note": "STRATEGY_PEAD_PRIMARY_ALLOW_LIVE must remain false; PEAD stays shadow dual-admit",
    }

    artifact = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "gates": {
            "pf_mean_target": PF_MEAN_TARGET,
            "worst_era_floor": WORST_ERA_FLOOR,
            "required_eras": 5,
            "min_era_trades": 50,
        },
        "portfolio_defaults": {
            "max_positions": max_positions,
            "risk_per_trade_pct": risk,
            "position_size_pct": pos_pct,
            "starting_equity": starting,
        },
        "baseline_n_trades": int(len(book)),
        "eras_present": eras_present,
        "dual_admit": dual_admit,
        "arms": arms,
        "passing_native_arms": [a["label"] for a in passing],
        "rejected_transfer_arms": [a["label"] for a in failing_transfer],
        "cleared_transfer_arms": [a["label"] for a in clearing_transfer],
        "recommendation": recommendation,
        "lookback_deferred": {
            "status": "deferred",
            "reason": (
                "Soft PEAD_LOOKBACK_DAYS / PEAD_PRIMARY_LOOKBACK_DAYS sweeps need new "
                "full-universe entry books; fixed book uses lookback=20 from "
                "research/env_overrides/pead_primary_aug.json"
            ),
        },
        "go_no_go": {
            "pead_live_promotion": "no_go",
            "pead_only_executable": "no_go",
            "continue_shadow_dual_admit": "go" if dual_admit["leak_check_pass"] else "no_go",
            "capacity_aware_selection_research": "go" if passing else "iterate",
            "next_checklist": [
                "Keep STRATEGY_PEAD_PRIMARY_MODE=shadow and ALLOW_LIVE=false",
                "Collect ≥2 more distinct RTH dual-admit sessions (eval>0, 0 PEAD-only leak)",
                "Operator-compare shadow admits vs Stage2 executable each session",
                "If adopting a top-N / percentile arm in shadow ranking only, pin label from recommendation",
                "Do not canary a PEAD sleeve until capacity-aware arm + multi-session dual-admit stay green",
                "Optional later: new full-universe books for PEAD_LOOKBACK_DAYS ∈ {10,15,20,30}",
            ],
        },
    }

    out_path = Path(args.out) if args.out else ART / f"pead_primary_capacity_cf_{run_id}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(artifact, indent=2, default=str), encoding="utf-8")
    LOG.info("Wrote %s (%s arms)", out_path, len(arms))

    md_path = out_path.with_suffix(".md")
    md_path.write_text(_to_markdown(artifact), encoding="utf-8")
    LOG.info("Wrote %s", md_path)

    # Non-zero only on hard failures (missing book / PEAD executable leak).
    if not dual_admit["leak_check_pass"]:
        LOG.error(
            "PEAD dual-admit leak check failed (allow_live=%s pead_only_exec=%s)",
            allow_live,
            pead_only_executable,
        )
        return 3
    return 0


def _to_markdown(artifact: dict[str, Any]) -> str:
    lines = [
        f"# PEAD capacity CF — `{artifact.get('run_id')}`",
        "",
        f"Generated: `{artifact.get('generated_at')}`",
        "",
        "## Gates",
        "",
        f"- PF mean ≥ {PF_MEAN_TARGET}, worst-era ≥ {WORST_ERA_FLOOR}, 5 eras, no thin eras (<50)",
        f"- Dual-admit leak check: `{artifact.get('dual_admit', {}).get('leak_check_pass')}`",
        "",
        "## Arms",
        "",
        "| Arm | Family | N | Ret% | PF mean | Worst | Cap.filt | Port.ret% | Verdict |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for a in artifact.get("arms") or []:
        port = a.get("portfolio") or {}
        lines.append(
            "| {label} | {family} | {n} | {ret} | {pf} | {worst} | {cf} | {pret} | {verdict} |".format(
                label=a.get("label"),
                family=a.get("family"),
                n=a.get("n_trades"),
                ret=a.get("retention_pct"),
                pf=a.get("pf_mean"),
                worst=a.get("worst_era_pf"),
                cf=port.get("capacity_filtered"),
                pret=port.get("total_return_net_pct"),
                verdict=a.get("verdict"),
            )
        )
    rec = artifact.get("recommendation") or {}
    lines.extend(
        [
            "",
            "## Recommended shadow stack",
            "",
            f"- Arm: `{rec.get('recommended_arm')}`",
            f"- Entry: {rec.get('entry')}",
            f"- Exit: {rec.get('exit')}",
            f"- Rank: {rec.get('rank')}",
            f"- Sizing: {rec.get('sizing')}",
            "- Live: **forbidden** (`ALLOW_LIVE=false`)",
            "",
            "## Go / no-go",
            "",
        ]
    )
    gng = artifact.get("go_no_go") or {}
    for k, v in gng.items():
        if k == "next_checklist":
            continue
        lines.append(f"- `{k}`: **{v}**")
    lines.append("")
    lines.append("### Next checklist")
    lines.append("")
    for item in gng.get("next_checklist") or []:
        lines.append(f"- [ ] {item}")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
