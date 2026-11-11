"""Weekday digest of tab-agent versions, promotions, and errors.

Writes ``schwab_skill/.tab_agent_digest/YYYY-MM-DD.md`` and a JSON twin.
A scheduled reader can send the file to the owner. This module does not
place orders or send mail.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)

DIGEST_DIR = ".tab_agent_digest"


def _paths(skill_dir: Path | str, day: str) -> tuple[Path, Path]:
    root = Path(skill_dir) / DIGEST_DIR
    return root / f"{day}.md", root / f"{day}.json"


def _tab_ids() -> list[str]:
    tabs = ["today"]
    try:
        from core.debate_tab_agent import DebateTabAgent  # noqa: F401

        tabs.append("debate")
    except Exception:
        pass
    for name, module in (
        ("research", "core.research_tab_agent"),
        ("backtest", "core.backtest_tab_agent"),
        ("system", "core.system_tab_agent"),
    ):
        try:
            import importlib

            importlib.import_module(module)
        except Exception:
            continue
        tabs.append(name)
    return tabs


def _day_changes(skill_dir: Path, day: str) -> list[dict[str, Any]]:
    from core.tab_agent_governor import load_change_reports

    rows = []
    for row in load_change_reports(skill_dir, limit=500):
        stamp = str(row.get("ts") or "")
        if stamp.startswith(day):
            rows.append(row)
    return rows


def build_digest(skill_dir: Path | str, day: date | None = None) -> dict[str, Any]:
    root = Path(skill_dir)
    stamp = day or datetime.now(timezone.utc).date()
    day_text = stamp.isoformat()
    tabs: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for tab_id in _tab_ids():
        try:
            from core.tab_agent import get_tab_agent

            agent = get_tab_agent(tab_id)
            status = agent.status(root)
            score = agent.score(root)
        except Exception as exc:
            errors.append({"tab_id": tab_id, "error": str(exc)})
            tabs.append({"tab_id": tab_id, "error": str(exc)})
            continue
        versions = score.get("versions") if isinstance(score.get("versions"), list) else []
        live = [
            {
                "prompt_key": row.get("prompt_key"),
                "version_id": row.get("version_id"),
                "n": row.get("n"),
                "n_live": row.get("n_live"),
                "n_replay": row.get("n_replay"),
                "primary_metric": row.get("primary_metric"),
                "primary_score": row.get("primary_score"),
                "max_drawdown": row.get("max_drawdown"),
                "win_rate": row.get("win_rate"),
                "n_longs": row.get("n_longs"),
                "n_skips": row.get("n_skips"),
                "edge_vs_baseline": row.get("edge_vs_baseline"),
                "degenerate": row.get("degenerate"),
                "decision_prompt": row.get("decision_prompt", True),
                "scored": row.get("scored", True),
                "not_scored_reason": row.get("not_scored_reason"),
                "prompt_versions": row.get("prompt_versions"),
                "quality_threshold": row.get("quality_threshold"),
            }
            for row in versions
            if row.get("status") == "live"
        ]
        events = [row for row in _day_changes(root, day_text) if row.get("tab_id") == tab_id]
        tabs.append(
            {
                "tab_id": tab_id,
                "objective": score.get("objective") or status.get("objective"),
                "live": live,
                "comparisons": score.get("comparisons") or [],
                "events": [
                    {
                        "action": row.get("action"),
                        "prompt_key": row.get("prompt_key"),
                        "version_id": row.get("version_id"),
                        "why": row.get("why"),
                        "before": row.get("before"),
                        "after": row.get("after"),
                        "sample_size": row.get("sample_size"),
                    }
                    for row in events
                ],
                "errors": [],
                "baseline": score.get("baseline"),
                "exit_fallback": score.get("exit_fallback"),
                "calibration": score.get("calibration") if isinstance(score.get("calibration"), dict) else None,
                "quality_threshold": score.get("quality_threshold"),
                "quality_sweep": score.get("quality_sweep"),
            }
        )
    return {
        "day": day_text,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tabs": tabs,
        "errors": errors,
        "note": "Analysis prompts only. No order, size, or execution setting is changed by this digest.",
        "lab": _lab_report(root, stamp),
    }


def _lab_report(skill_dir: Path, day: date) -> dict[str, Any] | None:
    try:
        from core.tab_agent_lab import report_for_digest

        return report_for_digest(skill_dir, day)
    except Exception as exc:
        LOG.warning("Tab-agent lab report skipped: %s", exc)
        return {"error": str(exc)}


def _markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# Tab agent digest {payload.get('day')}",
        "",
        str(payload.get("note") or ""),
        "",
    ]
    for tab in payload.get("tabs") or []:
        lines.append(f"## {tab.get('tab_id')}")
        if tab.get("error"):
            lines.append(f"Error: {tab.get('error')}")
            lines.append("")
            continue
        objective = tab.get("objective") if isinstance(tab.get("objective"), dict) else {}
        if objective.get("statement"):
            lines.append(str(objective.get("statement")))
            lines.append("")
        baseline = tab.get("baseline") if isinstance(tab.get("baseline"), dict) else {}
        if baseline.get("name"):
            lines.append(
                f"Buy-every-scan baseline: n={baseline.get('n')} "
                f"{baseline.get('primary_metric')}={baseline.get('primary_score')}"
            )
        fallback = tab.get("exit_fallback") if isinstance(tab.get("exit_fallback"), dict) else {}
        if fallback.get("n"):
            lines.append(
                f"Exit fallback rate={fallback.get('exit_fallback_rate')} "
                f"derived_stop_rate={fallback.get('derived_stop_rate')}"
            )
        if tab.get("quality_threshold") is not None:
            lines.append(f"Quality threshold: {tab.get('quality_threshold')}")
            sweep = tab.get("quality_sweep") if isinstance(tab.get("quality_sweep"), dict) else {}
            chosen = sweep.get("chosen_threshold")
            if chosen is not None:
                lines.append(
                    f"Sweep chose {chosen} ({sweep.get('reason')}), "
                    f"tune before {sweep.get('split_date')}, holdout cases={sweep.get('holdout_cases')}"
                )
            for row in (sweep.get("rows") or [])[:9]:
                lines.append(
                    f"- bar {row.get('threshold')} sortino={row.get('sortino')} "
                    f"longs={row.get('n_longs')} maxDD={row.get('max_drawdown')} "
                    f"holdout={row.get('holdout_sortino')} eligible={row.get('eligible')}"
                )
        lines.append("Live versions:")
        for row in tab.get("live") or []:
            marker = ""
            if row.get("decision_prompt") is False:
                marker = " non-decision"
            elif row.get("scored") is False:
                marker = " not-scored"
            elif row.get("degenerate"):
                marker = " no-variance"
            skips = f" skips={row.get('n_skips')}" if row.get("n_skips") is not None else ""
            lines.append(
                f"- {row.get('prompt_key')} {row.get('version_id')} "
                f"n={row.get('n')} longs={row.get('n_longs')}{skips} "
                f"win_rate={row.get('win_rate')} "
                f"live={row.get('n_live')} replay={row.get('n_replay')} "
                f"{row.get('primary_metric')}={row.get('primary_score')} "
                f"edge_vs_baseline={row.get('edge_vs_baseline')} "
                f"maxDD={row.get('max_drawdown')}{marker}"
            )
        if not tab.get("live"):
            lines.append("- none")
        lines.append("")
        lines.append("Events:")
        events = tab.get("events") or []
        if not events:
            lines.append("- none")
        for event in events:
            before = event.get("before") if isinstance(event.get("before"), dict) else {}
            after = event.get("after") if isinstance(event.get("after"), dict) else {}
            lines.append(
                f"- {event.get('action')} {event.get('prompt_key')} {event.get('version_id')} "
                f"n={event.get('sample_size')} "
                f"before {before.get('primary_metric')}={before.get('primary_score')} n={before.get('n')} "
                f"after {after.get('primary_metric')}={after.get('primary_score')} n={after.get('n')}"
            )
            if event.get("why"):
                lines.append(f"  {event.get('why')}")
        calibration = tab.get("calibration") if isinstance(tab.get("calibration"), dict) else None
        if calibration:
            lines.append("")
            lines.append("Calibration:")
            for name, row in (calibration.get("by_tab") or {}).items():
                if not isinstance(row, dict):
                    continue
                lines.append(
                    f"- {name} n={row.get('n')} brier={row.get('brier')} miscalibrated={row.get('miscalibrated')}"
                )
                for gap in row.get("gaps") or []:
                    lines.append(
                        f"  {gap.get('bin')} stated={gap.get('mean_confidence')} "
                        f"realized={gap.get('frequency')} gap={gap.get('gap')}"
                    )
        lines.append("")
    lines.extend(_lab_markdown(payload.get("lab")))
    if payload.get("errors"):
        lines.append("## Errors")
        for row in payload["errors"]:
            lines.append(f"- {row.get('tab_id')}: {row.get('error')}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _lab_markdown(lab: Any) -> list[str]:
    lines = ["## Overnight lab", ""]
    if not isinstance(lab, dict):
        lines.append("No finished night to report.")
        lines.append("")
        return lines
    if lab.get("error"):
        lines.append(f"Error: {lab.get('error')}")
        lines.append("")
        return lines
    lines.append(
        f"Night {lab.get('night_id')}: candidates={lab.get('candidates_tried')} "
        f"tokens={lab.get('tokens')} spend_usd={lab.get('spend_usd')} "
        f"orders_touched={lab.get('orders_touched')}"
    )
    trust = lab.get("trust_gate") if isinstance(lab.get("trust_gate"), dict) else {}
    reason = lab.get("trust_reason") or trust.get("reason")
    if reason:
        lines.append(str(reason))
    sources = lab.get("source_sortino")
    if isinstance(sources, dict):
        bits = []
        for name in ("rebuild_pre_cutoff", "rebuild_post_cutoff", "live_train", "live_holdout"):
            row = sources.get(name)
            if isinstance(row, dict) and row.get("sortino_adj") is not None:
                bits.append(f"{name}={row.get('sortino_adj')}")
        if bits:
            lines.append("Source Sortino: " + ", ".join(bits))
    for tab, row in (lab.get("tabs") or {}).items():
        if not isinstance(row, dict):
            continue
        shadow = row.get("shadow") if isinstance(row.get("shadow"), dict) else {}
        best = row.get("best_train") if isinstance(row.get("best_train"), dict) else {}
        live = row.get("live_train") if isinstance(row.get("live_train"), dict) else {}
        holdout = row.get("best_holdout") if isinstance(row.get("best_holdout"), dict) else {}
        lines.append(
            f"- {tab} generations={row.get('generations')} tried={row.get('candidates_tried')} "
            f"live_train_sortino={live.get('sortino_adj', live.get('sortino'))} "
            f"best_train_sortino={best.get('sortino_adj')} "
            f"best_holdout_sortino={holdout.get('sortino_adj')} "
            f"shadow={shadow.get('installed')} versions={shadow.get('version_ids')}"
        )
    lines.append("")
    return lines


def write_daily_digest(skill_dir: Path | str, day: date | None = None) -> dict[str, Any]:
    payload = build_digest(skill_dir, day)
    md_path, json_path = _paths(skill_dir, str(payload["day"]))
    try:
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(_markdown(payload), encoding="utf-8")
        json_path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    except OSError as exc:
        LOG.warning("Tab-agent digest write failed: %s", exc)
        payload.setdefault("errors", []).append({"tab_id": "*", "error": str(exc)})
    payload["markdown_path"] = str(md_path)
    payload["json_path"] = str(json_path)
    return payload


def read_digest(skill_dir: Path | str, day: str | None = None) -> dict[str, Any] | None:
    stamp = day or datetime.now(timezone.utc).date().isoformat()
    _md_path, json_path = _paths(skill_dir, stamp[:10])
    if not json_path.exists():
        return None
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None
