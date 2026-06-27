#!/usr/bin/env python3
"""Compare exit-grace smoke sweep PF vs both control baselines.

Reads ``validation_artifacts/multi_era_backtest_schwab_only_<config_id>.json``
and writes dual-baseline comparison artifacts:

  validation_artifacts/exit_grace_smoke_compare.json
  validation_artifacts/exit_grace_smoke_compare.md
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = SKILL_DIR / "validation_artifacts"

PER_ERA_MIN_TRADES = 50
MAX_PER_ERA_PF_REGRESSION = 0.10

DEFAULT_CONFIGS = [
    "control_legacy",
    "control_legacy_exits",
    "exit_grace_t15_h40",
    "exit_grace_t10_h40",
    "exit_grace_t15_h30",
]

CONTROL_LEGACY = "control_legacy"
CONTROL_LEGACY_EXITS = "control_legacy_exits"

REPLAY_PROFILE_TO_CONFIG: dict[str, str] = {
    "baseline_legacy": CONTROL_LEGACY_EXITS,
    "control_legacy_defaults": CONTROL_LEGACY,
    "exit_grace_t15_h40": "exit_grace_t15_h40",
    "exit_grace_t10_h40": "exit_grace_t10_h40",
    "exit_grace_t15_h30": "exit_grace_t15_h30",
}


def _artifact_path(config_id: str) -> Path:
    return ARTIFACT_DIR / f"multi_era_backtest_schwab_only_{config_id}.json"


def _load_era_pf(config_id: str) -> dict[str, dict[str, Any]] | None:
    path = _artifact_path(config_id)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if payload.get("failed_eras"):
        return None
    eras: dict[str, dict[str, Any]] = {}
    for row in payload.get("results", []):
        era = str(row.get("era") or "")
        if not era:
            continue
        eras[era] = row
    return eras or None


def _pf_float(value: Any) -> float | None:
    if value in (None, "inf"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _summarise(
    eras: dict[str, dict[str, Any]],
    control: dict[str, dict[str, Any]] | None,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    pf_deltas: list[float] = []
    pf_values: list[float] = []
    thin_eras: list[str] = []
    regressed_eras: list[dict[str, Any]] = []
    for era, row in sorted(eras.items()):
        t_pf = _pf_float(row.get("profit_factor_net"))
        c_pf = _pf_float((control or {}).get(era, {}).get("profit_factor_net")) if control else None
        pf_delta = (t_pf - c_pf) if (t_pf is not None and c_pf is not None) else None
        if pf_delta is not None:
            pf_deltas.append(pf_delta)
        if t_pf is not None:
            pf_values.append(t_pf)
        trades = int(row.get("total_trades", 0) or 0)
        if trades < PER_ERA_MIN_TRADES:
            thin_eras.append(era)
        if pf_delta is not None and pf_delta < -MAX_PER_ERA_PF_REGRESSION:
            regressed_eras.append({"era": era, "pf_delta": round(pf_delta, 4)})
        rows.append(
            {
                "era": era,
                "trades": trades,
                "pf": round(t_pf, 4) if t_pf is not None else None,
                "pf_control": round(c_pf, 4) if c_pf is not None else None,
                "pf_delta": round(pf_delta, 4) if pf_delta is not None else None,
            }
        )
    pf_mean = sum(pf_values) / len(pf_values) if pf_values else 0.0
    pf_mean_delta = sum(pf_deltas) / len(pf_deltas) if pf_deltas else 0.0
    return {
        "rows": rows,
        "pf_mean": round(pf_mean, 4),
        "pf_mean_delta": round(pf_mean_delta, 4),
        "worst_era_pf": round(min(pf_values), 4) if pf_values else None,
        "total_trades": sum(int(r.get("trades", 0) or 0) for r in rows),
        "thin_eras": thin_eras,
        "regressed_eras": regressed_eras,
        "passes_guardrails": (not thin_eras) and (not regressed_eras),
    }


def _load_from_replay(run_id: str) -> dict[str, dict[str, dict[str, Any]]]:
    """Load per-config era rows from replay_exit_overlay artifact."""
    path = ARTIFACT_DIR / f"replay_exit_overlay_{run_id}.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for profile_name, summary in (payload.get("profiles") or {}).items():
        config_id = REPLAY_PROFILE_TO_CONFIG.get(profile_name, profile_name)
        eras: dict[str, dict[str, Any]] = {}
        for row in summary.get("eras") or []:
            era = str(row.get("era") or "")
            if not era:
                continue
            pf = _pf_float(row.get("pf"))
            eras[era] = {
                "era": era,
                "profit_factor_net": pf,
                "total_trades": int(row.get("n", 0) or 0),
            }
        if eras:
            out[config_id] = eras
    return out


def _build_summaries_from_eras(
    configs: list[str],
    era_by_config: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    control_legacy = era_by_config.get(CONTROL_LEGACY)
    control_legacy_exits = era_by_config.get(CONTROL_LEGACY_EXITS)
    summaries: dict[str, dict[str, Any]] = {}
    for config_id in configs:
        eras = era_by_config.get(config_id)
        if not eras:
            continue
        base = _summarise(eras, None)
        vs_legacy = _summarise(eras, control_legacy)
        vs_legacy_exits = _summarise(eras, control_legacy_exits)
        summaries[config_id] = {
            **base,
            "delta_vs_control_legacy": vs_legacy["pf_mean_delta"],
            "delta_vs_control_legacy_exits": vs_legacy_exits["pf_mean_delta"],
            "regressed_vs_control_legacy": vs_legacy["regressed_eras"],
            "regressed_vs_control_legacy_exits": vs_legacy_exits["regressed_eras"],
        }
    return summaries


def _plumbing_pass(configs: list[str], summaries: dict[str, dict[str, Any]]) -> dict[str, Any]:
    missing = [c for c in configs if c not in summaries]
    zero_trade_configs = [
        c for c, s in summaries.items() if int(s.get("total_trades", 0) or 0) == 0
    ]

    def _recent_trades(config_id: str) -> int:
        if config_id not in summaries:
            return 0
        for row in summaries[config_id].get("rows", []):
            if row.get("era") == "recent_current":
                return int(row.get("trades", 0) or 0)
        return 0

    recent_has_trades = (
        _recent_trades(CONTROL_LEGACY_EXITS) > 0 or _recent_trades("exit_grace_t15_h40") > 0
    )
    passed = not missing and not zero_trade_configs and recent_has_trades
    return {
        "passed": passed,
        "missing_artifacts": missing,
        "zero_trade_configs": zero_trade_configs,
        "recent_current_has_trades": recent_has_trades,
        "recommend_full_sweep": passed,
    }


def _render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Exit-Grace Smoke Comparison",
        "",
        f"Generated: {payload.get('generated_at')}",
        f"Source: {payload.get('source', 'multi_era')}",
        "",
        f"**Plumbing pass:** {'yes' if payload.get('plumbing', {}).get('passed') else 'no'}",
        "",
    ]
    plumbing = payload.get("plumbing") or {}
    if plumbing.get("missing_artifacts"):
        lines.append(f"- Missing artifacts: {', '.join(plumbing['missing_artifacts'])}")
    if plumbing.get("zero_trade_configs"):
        lines.append(f"- Zero-trade configs: {', '.join(plumbing['zero_trade_configs'])}")
    lines.extend(["", "## PF summary (vs both controls)", ""])
    lines.append(
        "| config | pf_mean | worst_era_pf | Δ vs control_legacy | "
        "Δ vs control_legacy_exits | trades | thin_eras |"
    )
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | --- |")
    for row in payload.get("ranking", []):
        thin = ",".join(row.get("thin_eras") or []) or "—"
        lines.append(
            f"| {row['config_id']} | {row['pf_mean']:.4f} | "
            f"{row.get('worst_era_pf')} | "
            f"{row.get('delta_vs_control_legacy', '—')} | "
            f"{row.get('delta_vs_control_legacy_exits', '—')} | "
            f"{row.get('total_trades', 0)} | {thin} |"
        )
    lines.extend(["", "## Interpretation", ""])
    if plumbing.get("passed"):
        lines.append(
            "- Smoke plumbing OK. Full 5-era universe sweep is warranted if exit-grace "
            "configs beat `control_legacy_exits` on PF mean."
        )
    else:
        lines.append(
            "- Smoke plumbing failed. Fix Schwab auth/data before full sweep."
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare exit-grace smoke PF vs both controls.")
    parser.add_argument(
        "--configs",
        nargs="*",
        default=DEFAULT_CONFIGS,
        help="Config IDs to compare (default: exit-grace smoke set).",
    )
    parser.add_argument(
        "--from-replay",
        default="",
        help="Use replay_exit_overlay_<run_id>.json instead of multi-era artifacts.",
    )
    args = parser.parse_args()
    configs = list(args.configs)

    source = "multi_era"
    if args.from_replay:
        era_by_config = _load_from_replay(args.from_replay)
        summaries = _build_summaries_from_eras(configs, era_by_config)
        source = f"replay:{args.from_replay}"
    else:
        era_by_config: dict[str, dict[str, dict[str, Any]]] = {}
        for config_id in configs:
            eras = _load_era_pf(config_id)
            if eras:
                era_by_config[config_id] = eras
        summaries = _build_summaries_from_eras(configs, era_by_config)

    ranking = []
    for config_id in configs:
        if config_id not in summaries:
            continue
        s = summaries[config_id]
        ranking.append(
            {
                "config_id": config_id,
                "pf_mean": s["pf_mean"],
                "worst_era_pf": s["worst_era_pf"],
                "delta_vs_control_legacy": s["delta_vs_control_legacy"],
                "delta_vs_control_legacy_exits": s["delta_vs_control_legacy_exits"],
                "total_trades": s["total_trades"],
                "thin_eras": s["thin_eras"],
                "passes_guardrails_vs_legacy": s["regressed_vs_control_legacy"] == [] and not s["thin_eras"],
            }
        )
    ranking.sort(
        key=lambda r: (
            -float(r.get("delta_vs_control_legacy_exits") or 0),
            -float(r.get("pf_mean") or 0),
        )
    )

    plumbing = _plumbing_pass(configs, summaries)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "configs": configs,
        "plumbing": plumbing,
        "summaries": summaries,
        "ranking": ranking,
    }

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = ARTIFACT_DIR / "exit_grace_smoke_compare.json"
    md_path = ARTIFACT_DIR / "exit_grace_smoke_compare.md"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(payload), encoding="utf-8")
    print(json.dumps({"plumbing": plumbing, "ranking": ranking}, indent=2))
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    return 0 if plumbing["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
