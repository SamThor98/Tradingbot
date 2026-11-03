"""Backtest tab agent over the strategy-chat prompts.

A version is scored on completed ``backtest_runs`` that it actually queued.
Only trades whose entry date is on or after the version was created count,
so the book is walk-forward and does not look ahead. The shared Sortino
objective compounds those net returns. This module does not place orders.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from core.tab_agent import STATUS_PENDING, TabAgent
from core.tab_agent_objective import OBJECTIVE_ID, compare_books, evaluate_book, load_objective

LOG = logging.getLogger(__name__)

ATTRIBUTION_FILE = ".tab_agent_backtest_runs.jsonl"
PROMPT_KEY = "strategy_chat"
DONE = {"completed", "complete", "success", "succeeded"}

IMPROVER_SYSTEM = (
    "You revise the strategy-chat system prompt used to design historical backtests. "
    "The objective is a higher Sortino or Sharpe of out-of-sample net returns from completed backtest runs. "
    "Walk-forward only: do not describe a result the run has not finished. "
    "Long-only equities. Never suggest a short, an inverse ETF, or an option. "
    "Do not penalize market cap. Do not cap the number of names. "
    "You do not place live orders or change size. "
    "Return JSON with keys text and rationale. rationale must say when the sample is too small."
)


def prompt_catalog() -> dict[str, str]:
    from webapp.strategy_chat import STRATEGY_CHAT_SYSTEM

    return {PROMPT_KEY: STRATEGY_CHAT_SYSTEM}


def live_prompt_text(skill_dir: Path | str, key: str = PROMPT_KEY) -> str | None:
    """Live prompt when the backtest agent is on. None keeps the built-in text."""
    from config import get_tab_agent_backtest_enabled

    if not get_tab_agent_backtest_enabled(Path(skill_dir)):
        return None
    row = BacktestTabAgent().registry(Path(skill_dir)).live_version("backtest", key)
    if row and str(row.get("text") or "").strip():
        return str(row["text"])
    return None


def note_queued_run(skill_dir: Path | str, run_id: str) -> None:
    """Remember which live prompt queued a backtest. No-op while the agent is off."""
    text = live_prompt_text(skill_dir)
    if text is None or not str(run_id or "").strip():
        return
    row = BacktestTabAgent().registry(Path(skill_dir)).live_version("backtest", PROMPT_KEY)
    if row is None:
        return
    path = Path(skill_dir) / ATTRIBUTION_FILE
    body = {
        "run_id": str(run_id),
        "prompt_key": PROMPT_KEY,
        "prompt_version": row.get("id"),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(body) + "\n")
    except OSError as exc:
        LOG.warning("Backtest prompt attribution skipped: %s", exc)


def load_attributions(skill_dir: Path | str) -> dict[str, str]:
    path = Path(skill_dir) / ATTRIBUTION_FILE
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        LOG.warning("Backtest attribution read failed: %s", exc)
        return {}
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("run_id") and row.get("prompt_version"):
            out[str(row["run_id"])] = str(row["prompt_version"])
    return out


def _loads(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def load_backtest_runs(db_path: Path | str) -> list[dict[str, Any]]:
    """Read completed-or-not rows from the local ``backtest_runs`` table."""
    path = Path(db_path)
    if not path.exists():
        return []
    try:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            fetched = conn.execute(
                "SELECT id, status, spec_json, result_json, created_at FROM backtest_runs"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        LOG.warning("backtest_runs read failed: %s", exc)
        return []
    rows = []
    for item in fetched:
        rows.append(
            {
                "id": item["id"],
                "status": item["status"],
                "spec": _loads(item["spec_json"]),
                "result": _loads(item["result_json"]),
                "created_at": item["created_at"],
            }
        )
    return rows


def oos_returns(run: dict[str, Any], version_created: str) -> list[tuple[str, float]]:
    """Out-of-sample percent returns. Entries before the version date are dropped."""
    created_day = str(version_created or "")[:10]
    result = run.get("result") if isinstance(run.get("result"), dict) else {}
    spec = run.get("spec") if isinstance(run.get("spec"), dict) else {}
    trades = result.get("trades") if isinstance(result.get("trades"), list) else []
    points: list[tuple[str, float]] = []
    for trade in trades:
        if not isinstance(trade, dict):
            continue
        entry = str(trade.get("entry_date") or "")[:10]
        if len(entry) < 10 or (created_day and entry < created_day):
            continue
        try:
            ret_pct = float(trade.get("net_return")) * 100.0
        except (TypeError, ValueError):
            continue
        exit_day = str(trade.get("exit_date") or entry)[:10]
        points.append((exit_day, ret_pct))
    if points:
        return points
    start = str(spec.get("start_date") or "")[:10]
    if created_day and start and start < created_day:
        return []
    try:
        total = float(result.get("total_return_net_pct"))
    except (TypeError, ValueError):
        return []
    end = str(spec.get("end_date") or run.get("created_at") or created_day)[:10]
    return [(end or created_day, total)]


def _finished(run: dict[str, Any]) -> bool:
    status = str(run.get("status") or "").lower()
    if status in DONE:
        return True
    result = run.get("result") if isinstance(run.get("result"), dict) else {}
    return "total_trades" in result or "total_return_net_pct" in result or bool(result.get("trades"))


class BacktestTabAgent(TabAgent):
    tab_id = "backtest"
    metric_name = OBJECTIVE_ID

    def prompt_catalog(self) -> dict[str, str]:
        return prompt_catalog()

    def score(
        self,
        skill_dir: Path,
        *,
        registry: Any = None,
        since_ts: str | None = None,
        runs: list[dict[str, Any]] | None = None,
        attributions: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        reg = registry or self.registry(skill_dir)
        config = load_objective(skill_dir)
        if runs is None:
            runs = load_backtest_runs(Path(skill_dir) / "webapp" / "webapp.db")
        if attributions is None:
            attributions = load_attributions(skill_dir)
        versions = reg.versions(self.tab_id)
        stats = [
            _version_book(version, runs, attributions, config, since_ts=since_ts) for version in versions
        ]
        by_id = {row["version_id"]: row for row in stats}
        comparisons = []
        for row in stats:
            if row.get("status") == "live":
                continue
            baseline = by_id.get(str(row.get("parent") or ""))
            if baseline is None:
                baseline = next(
                    (
                        item
                        for item in stats
                        if item.get("prompt_key") == row.get("prompt_key") and item.get("status") == "live"
                    ),
                    None,
                )
            comparisons.append(
                {
                    "version_id": row["version_id"],
                    "prompt_key": row.get("prompt_key"),
                    "baseline_version_id": None if baseline is None else baseline.get("version_id"),
                    "edge_vs_live": compare_books(row, baseline, config=config),
                    "auto_promote": False,
                }
            )
        objective = config.as_dict()
        return {
            "tab_id": self.tab_id,
            "objective": objective,
            "objective_id": OBJECTIVE_ID,
            "auto_promote": False,
            "winner_rule": objective["statement"],
            "versions": stats,
            "comparisons": comparisons,
            "join_note": (
                "Completed backtest_runs join on the prompt that queued them. "
                "Trades entered before that version existed are excluded."
            ),
        }

    def propose(
        self,
        skill_dir: Path,
        *,
        registry: Any = None,
        llm: Callable[[str, str], str] | None = None,
        prompt_key: str | None = None,
    ) -> dict[str, Any]:
        from core.today_tab_agent import parse_prompt_proposal

        reg = registry or self.registry(skill_dir)
        caller = llm or _default_llm
        score = self.score(skill_dir, registry=reg)
        created: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        catalog = self.prompt_catalog()
        keys = [prompt_key] if prompt_key else list(catalog)
        for key in keys:
            if key not in catalog:
                skipped.append({"prompt_key": str(key), "reason": "unknown_prompt"})
                continue
            live = reg.live_version(self.tab_id, key)
            if live is None:
                skipped.append({"prompt_key": key, "reason": "no_live_prompt"})
                continue
            if any(row.get("status") in {STATUS_PENDING, "shadow"} for row in reg.versions(self.tab_id, key)):
                skipped.append({"prompt_key": key, "reason": "candidate_exists"})
                continue
            stat = next((row for row in score.get("versions") or [] if row.get("version_id") == live.get("id")), {})
            user = (
                f"Prompt key: {key}\nObjective: {score.get('winner_rule')}\n"
                f"n={stat.get('n')} {stat.get('primary_metric')}={stat.get('primary_score')} "
                f"max_drawdown={stat.get('max_drawdown')}\n\nCurrent prompt:\n{live.get('text')}"
            )
            try:
                raw = caller(IMPROVER_SYSTEM, user)
            except Exception as exc:
                LOG.warning("Backtest prompt proposal failed for %s: %s", key, exc)
                skipped.append({"prompt_key": key, "reason": "llm_error"})
                continue
            parsed = parse_prompt_proposal(raw or "")
            if parsed is None or parsed["text"].strip() == str(live.get("text") or "").strip():
                skipped.append({"prompt_key": key, "reason": "no_proposal"})
                continue
            row = reg.add_version(
                self.tab_id,
                prompt_key=key,
                text=parsed["text"],
                parent=str(live.get("id") or "") or None,
                rationale=parsed["rationale"],
                status="shadow",
            )
            from core.tab_agent_review import write_review_request

            write_review_request(
                skill_dir,
                tab_id=self.tab_id,
                version=row,
                parent_text=str(live.get("text") or ""),
                score_row=stat,
                objective=score.get("objective") if isinstance(score.get("objective"), dict) else None,
            )
            created.append(row)
        return {"tab_id": self.tab_id, "created": created, "skipped": skipped, "auto_promote": False, "score": score}


def _default_llm(system: str, user: str) -> str:
    from engine_analysis import _call_llm, _load_env

    return _call_llm(user, system, _load_env())


def _version_book(
    version: dict[str, Any],
    runs: list[dict[str, Any]],
    attributions: dict[str, str],
    config: Any,
    *,
    since_ts: str | None,
) -> dict[str, Any]:
    vid = str(version.get("id") or "")
    created = str(since_ts or version.get("created_at") or "")
    returns: list[float] = []
    dates: list[str] = []
    for run in runs:
        if not _finished(run):
            continue
        if attributions.get(str(run.get("id") or "")) != vid:
            continue
        for day, ret in oos_returns(run, created):
            dates.append(day)
            returns.append(ret)
    book = evaluate_book(returns, dates, config=config)
    book.update(
        {
            "version_id": vid,
            "prompt_key": version.get("prompt_key"),
            "status": version.get("status"),
            "parent": version.get("parent"),
        }
    )
    return book
