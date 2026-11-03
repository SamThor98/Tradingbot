"""Generic per-tab prompt agent. Scoring uses :mod:`core.tab_agent_objective`.

Nothing in this module places orders. New versions stay pending until
:meth:`TabAgent.approve` or :meth:`TabAgent.rollback`.
"""

from __future__ import annotations

import json
import logging
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from core.tab_agent_objective import OBJECTIVE_ID, compare_books, evaluate_book, load_objective

LOG = logging.getLogger(__name__)

REGISTRY_FILE = ".tab_agent_registry.json"
ATTRIBUTION_FILE = ".tab_agent_attributions.jsonl"

STATUS_LIVE = "live"
STATUS_PENDING = "pending"
STATUS_SHADOW = "shadow"
STATUS_REJECTED = "rejected"
STATUS_RETIRED = "retired"


class TabAgentError(ValueError):
    """Operator-facing registry error."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_dt(raw: Any) -> datetime | None:
    if isinstance(raw, datetime):
        dt = raw
    elif isinstance(raw, str) and raw.strip():
        text = raw.strip().replace("Z", "+00:00")
        if " " in text and "T" not in text:
            text = text.replace(" ", "T", 1)
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def utc_day(raw: Any) -> str | None:
    dt = parse_dt(raw)
    return dt.date().isoformat() if dt else None


def _store_path(skill_dir: Path, name: str) -> Path:
    return Path(skill_dir) / name


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        LOG.warning("Ignoring unreadable tab-agent store %s: %s", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _save_json(path: Path, data: dict[str, Any]) -> None:
    from _io_utils import atomic_write_json

    atomic_write_json(path, data, indent=2)


class PromptRegistry:
    def __init__(self, skill_dir: Path | str) -> None:
        self.skill_dir = Path(skill_dir)
        self.path = _store_path(self.skill_dir, REGISTRY_FILE)

    def _read(self) -> dict[str, Any]:
        data = _load_json(self.path)
        data.setdefault("tabs", {})
        return data

    def _write(self, data: dict[str, Any]) -> None:
        _save_json(self.path, data)

    def _bucket(self, data: dict[str, Any], tab_id: str, prompt_key: str) -> dict[str, Any]:
        tab = data.setdefault("tabs", {}).setdefault(tab_id, {}).setdefault("keys", {})
        bucket = tab.setdefault(prompt_key, {"versions": []})
        bucket.setdefault("versions", [])
        return bucket

    def versions(self, tab_id: str, prompt_key: str | None = None) -> list[dict[str, Any]]:
        keys = self._read().get("tabs", {}).get(tab_id, {}).get("keys", {})
        chosen = {prompt_key: keys.get(prompt_key, {})} if prompt_key is not None else keys
        out: list[dict[str, Any]] = []
        for bucket in chosen.values():
            for row in bucket.get("versions", []) or []:
                if isinstance(row, dict):
                    out.append(dict(row))
        return out

    def get(self, tab_id: str, version_id: str) -> dict[str, Any] | None:
        for row in self.versions(tab_id):
            if row.get("id") == version_id:
                return row
        return None

    def ensure_seeded(self, tab_id: str, catalog: dict[str, str], *, rationale: str) -> None:
        data = self._read()
        changed = False
        for prompt_key, text in catalog.items():
            bucket = self._bucket(data, tab_id, prompt_key)
            if bucket["versions"]:
                continue
            bucket["versions"].append(
                {
                    "id": uuid.uuid4().hex[:12],
                    "prompt_key": prompt_key,
                    "text": str(text),
                    "parent": None,
                    "rationale": rationale,
                    "created_at": utc_now_iso(),
                    "status": STATUS_LIVE,
                }
            )
            changed = True
        if changed:
            self._write(data)

    def add_version(
        self,
        tab_id: str,
        *,
        prompt_key: str,
        text: str,
        parent: str | None,
        rationale: str,
        status: str = STATUS_PENDING,
    ) -> dict[str, Any]:
        if status == STATUS_LIVE:
            raise TabAgentError(
                "New versions start in shadow and are promoted only by the objective or a manual override."
            )
        data = self._read()
        row = {
            "id": uuid.uuid4().hex[:12],
            "prompt_key": prompt_key,
            "text": str(text).strip(),
            "parent": parent,
            "rationale": str(rationale).strip(),
            "created_at": utc_now_iso(),
            "status": status,
        }
        if not row["text"]:
            raise TabAgentError("Prompt text is empty.")
        self._bucket(data, tab_id, prompt_key)["versions"].append(row)
        self._write(data)
        return dict(row)

    def live_version(self, tab_id: str, prompt_key: str) -> dict[str, Any] | None:
        live = [row for row in self.versions(tab_id, prompt_key) if row.get("status") == STATUS_LIVE]
        return live[-1] if live else None

    def _mutate(
        self, tab_id: str, version_id: str, fn: Callable[[dict[str, Any], list[dict[str, Any]]], None]
    ) -> dict[str, Any]:
        data = self._read()
        for bucket in data.get("tabs", {}).get(tab_id, {}).get("keys", {}).values():
            rows = bucket.get("versions", [])
            for row in rows:
                if isinstance(row, dict) and row.get("id") == version_id:
                    fn(row, rows)
                    self._write(data)
                    return dict(row)
        raise TabAgentError(f"Unknown prompt version: {version_id}")

    def approve(self, tab_id: str, version_id: str) -> dict[str, Any]:
        target = self.get(tab_id, version_id)
        if target is None:
            raise TabAgentError(f"Unknown prompt version: {version_id}")
        if target.get("status") == STATUS_LIVE:
            return target
        if target.get("status") not in (STATUS_PENDING, STATUS_SHADOW):
            raise TabAgentError(
                f"Version {version_id} is {target.get('status')}; only pending or shadow can be approved."
            )

        def _apply(row: dict[str, Any], rows: list[dict[str, Any]]) -> None:
            for other in rows:
                if other.get("id") != version_id and other.get("status") == STATUS_LIVE:
                    other["status"] = STATUS_RETIRED
            row["status"] = STATUS_LIVE

        return self._mutate(tab_id, version_id, _apply)

    def promote(
        self, tab_id: str, version_id: str, *, predecessor_stats: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Make a shadow or pending version live and remember the predecessor book."""
        target = self.get(tab_id, version_id)
        if target is None:
            raise TabAgentError(f"Unknown prompt version: {version_id}")
        live = self.live_version(tab_id, str(target.get("prompt_key") or ""))
        predecessor_id = None if live is None or live.get("id") == version_id else live.get("id")

        def _apply(row: dict[str, Any], rows: list[dict[str, Any]]) -> None:
            for other in rows:
                if other.get("id") != version_id and other.get("status") == STATUS_LIVE:
                    other["status"] = STATUS_RETIRED
            row["status"] = STATUS_LIVE
            if predecessor_id:
                row["predecessor_id"] = predecessor_id
                row["promoted_at"] = utc_now_iso()
                row["predecessor_stats"] = dict(predecessor_stats or {})

        if target.get("status") == STATUS_LIVE:
            return target
        if target.get("status") not in (STATUS_PENDING, STATUS_SHADOW):
            raise TabAgentError(f"Version {version_id} is {target.get('status')} and cannot be promoted.")
        return self._mutate(tab_id, version_id, _apply)

    def reject(self, tab_id: str, version_id: str) -> dict[str, Any]:
        target = self.get(tab_id, version_id)
        if target is None:
            raise TabAgentError(f"Unknown prompt version: {version_id}")
        if target.get("status") == STATUS_LIVE:
            raise TabAgentError("The live prompt cannot be rejected. Roll back to a retired version instead.")
        if target.get("status") not in (STATUS_PENDING, STATUS_SHADOW):
            raise TabAgentError(f"Version {version_id} is {target.get('status')} and cannot be rejected.")

        def _apply(row: dict[str, Any], _rows: list[dict[str, Any]]) -> None:
            row["status"] = STATUS_REJECTED

        return self._mutate(tab_id, version_id, _apply)

    def mark_shadow(self, tab_id: str, version_id: str) -> dict[str, Any]:
        target = self.get(tab_id, version_id)
        if target is None:
            raise TabAgentError(f"Unknown prompt version: {version_id}")
        if target.get("status") != STATUS_PENDING:
            raise TabAgentError("Only a pending proposal can enter shadow mode.")

        def _apply(row: dict[str, Any], rows: list[dict[str, Any]]) -> None:
            for other in rows:
                if other.get("status") == STATUS_SHADOW and other.get("id") != version_id:
                    other["status"] = STATUS_PENDING
            row["status"] = STATUS_SHADOW

        return self._mutate(tab_id, version_id, _apply)

    def rollback(self, tab_id: str, version_id: str) -> dict[str, Any]:
        target = self.get(tab_id, version_id)
        if target is None:
            raise TabAgentError(f"Unknown prompt version: {version_id}")
        if target.get("status") == STATUS_LIVE:
            return target
        if target.get("status") != STATUS_RETIRED:
            raise TabAgentError("Rollback only restores a retired live version.")

        def _apply(row: dict[str, Any], rows: list[dict[str, Any]]) -> None:
            for other in rows:
                if other.get("status") == STATUS_LIVE and other.get("id") != version_id:
                    other["status"] = STATUS_RETIRED
            row["status"] = STATUS_LIVE

        return self._mutate(tab_id, version_id, _apply)


def record_scan_prompt_use(
    skill_dir: Path | str,
    *,
    tab_id: str,
    ticker: str,
    bindings: list[dict[str, str]],
    created_at: datetime | None = None,
) -> None:
    when = created_at or datetime.now(timezone.utc)
    day = utc_day(when)
    symbol = str(ticker or "").upper().strip()
    if not symbol or not day or not bindings:
        return
    path = _store_path(Path(skill_dir), ATTRIBUTION_FILE)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for binding in bindings:
                version_id = str(binding.get("version_id") or "").strip()
                prompt_key = str(binding.get("prompt_key") or "").strip()
                if not version_id or not prompt_key:
                    continue
                handle.write(
                    json.dumps(
                        {
                            "tab_id": tab_id,
                            "ticker": symbol,
                            "day": day,
                            "prompt_key": prompt_key,
                            "version_id": version_id,
                            "role": str(binding.get("role") or "live"),
                            "created_at": when.astimezone(timezone.utc).isoformat(),
                        }
                    )
                    + "\n"
                )
    except Exception as exc:
        LOG.warning("Prompt attribution write failed: %s", exc)


def load_attributions(skill_dir: Path | str, *, tab_id: str | None = None) -> list[dict[str, Any]]:
    path = _store_path(Path(skill_dir), ATTRIBUTION_FILE)
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception as exc:
        LOG.warning("Prompt attribution read failed: %s", exc)
        return []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and (tab_id is None or row.get("tab_id") == tab_id):
            out.append(row)
    return out


def _packet_return(packet: dict[str, Any], *, horizon_days: int) -> tuple[str, float] | None:
    outcome = packet.get("outcome") if isinstance(packet.get("outcome"), dict) else {}
    chosen = outcome
    by_horizon = outcome.get("by_horizon") if isinstance(outcome.get("by_horizon"), dict) else {}
    try:
        top_horizon = int(outcome.get("horizon_days"))
    except (TypeError, ValueError):
        top_horizon = None
    if top_horizon != int(horizon_days) and str(horizon_days) in by_horizon:
        nested = by_horizon.get(str(horizon_days))
        if isinstance(nested, dict):
            chosen = nested
    label = str(chosen.get("label") or "pending").lower()
    if label in ("pending", "unknown", ""):
        return None
    side = str(packet.get("side") or packet.get("direction") or "long").lower()
    if side in {"short", "option", "put", "call"}:
        return None
    try:
        horizon = int(chosen.get("horizon_days"))
    except (TypeError, ValueError):
        return None
    if horizon != int(horizon_days):
        return None
    try:
        ret = float(chosen.get("realized_return_pct"))
    except (TypeError, ValueError):
        return None
    return label, ret


def score_prompt_versions(
    *,
    versions: list[dict[str, Any]],
    packets: list[dict[str, Any]],
    attributions: list[dict[str, Any]] | None = None,
    skill_dir: Path | None = None,
    since_ts: str | None = None,
    tab_id: str = "today",
) -> dict[str, Any]:
    """Score each version on the shared risk-adjusted objective."""
    config = load_objective(skill_dir)
    attributions = attributions or []
    by_version: dict[str, list[dict[str, Any]]] = {}
    for row in attributions:
        vid = str(row.get("version_id") or "")
        if vid:
            by_version.setdefault(vid, []).append(row)
    packet_index: dict[tuple[str, str], dict[str, Any]] = {}
    for packet in packets:
        if not isinstance(packet, dict):
            continue
        ticker = str(packet.get("ticker") or "").upper()
        day = utc_day(packet.get("created_at"))
        if ticker and day and (ticker, day) not in packet_index:
            packet_index[(ticker, day)] = packet

    from core.tab_agent_replay import is_decision_prompt, non_decision_book

    stats: list[dict[str, Any]] = []
    for version in versions:
        vid = str(version.get("id") or "")
        if not is_decision_prompt(tab_id, str(version.get("prompt_key") or "")):
            stats.append(non_decision_book(version))
            continue
        returns: list[float] = []
        dates: list[str] = []
        decisions: list[str] = []
        seen: set[tuple[str, str]] = set()
        for use in by_version.get(vid, []):
            ticker = str(use.get("ticker") or "").upper()
            day = str(use.get("day") or "")
            if since_ts and str(use.get("created_at") or "") < since_ts:
                continue
            if not ticker or not day or (ticker, day) in seen:
                continue
            seen.add((ticker, day))
            decision = str(use.get("decision") or use.get("role") or "").lower()
            if decision in {"short", "option", "put", "call", "inverse"}:
                continue
            if decision in {"cash", "no_trade", "pass", "abstain", "stay_in_cash"}:
                dates.append(day)
                returns.append(0.0)
                decisions.append("cash")
                continue
            parsed = _packet_return(packet_index.get((ticker, day)) or {}, horizon_days=config.horizon_days)
            if parsed is None:
                continue
            dates.append(day)
            returns.append(parsed[1])
            decisions.append("long")
        from core.tab_agent_replay import attach_replay, decision_flags

        merged = attach_replay(
            returns,
            dates,
            skill_dir=skill_dir,
            tab_id=tab_id,
            version_id=vid,
            weight=config.replay_weight,
            since_ts=since_ts,
            live_decisions=decisions,
        )
        book = evaluate_book(
            merged.returns,
            merged.dates,
            config=config,
            weights=merged.weights,
            sources=merged.sources,
        )
        book.update(
            {
                "version_id": vid,
                "prompt_key": version.get("prompt_key"),
                "status": version.get("status"),
                "parent": version.get("parent"),
            }
        )
        book.update(decision_flags(merged.decisions))
        stats.append(book)

    by_id = {row["version_id"]: row for row in stats}
    comparisons = []
    for row in stats:
        if row.get("status") == STATUS_LIVE:
            continue
        baseline = by_id.get(str(row.get("parent") or ""))
        if baseline is None:
            baseline = next(
                (
                    item
                    for item in stats
                    if item.get("prompt_key") == row.get("prompt_key") and item.get("status") == STATUS_LIVE
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
    report = {
        "objective": config.as_dict(),
        "objective_id": OBJECTIVE_ID,
        "auto_promote": False,
        "versions": stats,
        "comparisons": comparisons,
        "winner_rule": config.as_dict()["statement"],
    }
    from core.tab_agent_replay import annotate_tab_score

    return annotate_tab_score(report, skill_dir, tab_id, config)


class TabAgent(ABC):
    tab_id: str
    metric_name: str = OBJECTIVE_ID

    @abstractmethod
    def prompt_catalog(self) -> dict[str, str]:
        """Built-in prompt text keyed by prompt id."""

    @abstractmethod
    def score(
        self,
        skill_dir: Path,
        *,
        registry: PromptRegistry | None = None,
        since_ts: str | None = None,
    ) -> dict[str, Any]:
        """Grade versions. Must not promote anything."""

    @abstractmethod
    def propose(
        self,
        skill_dir: Path,
        *,
        registry: PromptRegistry | None = None,
        llm: Callable[[str, str], str] | None = None,
        prompt_key: str | None = None,
    ) -> dict[str, Any]:
        """Store PENDING revisions."""

    def registry(self, skill_dir: Path) -> PromptRegistry:
        reg = PromptRegistry(skill_dir)
        reg.ensure_seeded(
            self.tab_id, self.prompt_catalog(), rationale="Seeded from the tab's current built-in prompt."
        )
        return reg

    def approve(self, skill_dir: Path, version_id: str) -> dict[str, Any]:
        """Manual override. Promotes even when the sample gate has not passed."""
        from core.tab_agent_governor import write_change_report

        reg = self.registry(skill_dir)
        target = reg.get(self.tab_id, version_id)
        if target is None:
            raise TabAgentError(f"Unknown prompt version: {version_id}")
        row = reg.promote(self.tab_id, version_id, predecessor_stats={})
        write_change_report(
            skill_dir,
            {
                "tab_id": self.tab_id,
                "action": "manual_approve",
                "prompt_key": target.get("prompt_key"),
                "version_id": version_id,
                "why": "Operator override. The sample gate was not required.",
                "before": {},
                "after": {},
                "what_changed": str(target.get("rationale") or "Analysis prompt replaced by hand."),
                "sample_size": None,
            },
        )
        return row

    def govern(self, skill_dir: Path) -> dict[str, Any]:
        from core.tab_agent_governor import govern_agent

        return govern_agent(self, skill_dir)

    def reject(self, skill_dir: Path, version_id: str) -> dict[str, Any]:
        return self.registry(skill_dir).reject(self.tab_id, version_id)

    def rollback(self, skill_dir: Path, version_id: str) -> dict[str, Any]:
        return self.registry(skill_dir).rollback(self.tab_id, version_id)

    def shadow(self, skill_dir: Path, version_id: str) -> dict[str, Any]:
        return self.registry(skill_dir).mark_shadow(self.tab_id, version_id)

    def status(self, skill_dir: Path) -> dict[str, Any]:
        reg = self.registry(skill_dir)
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in reg.versions(self.tab_id):
            grouped.setdefault(str(row.get("prompt_key") or ""), []).append(row)
        prompts = []
        for key, rows in grouped.items():
            prompts.append(
                {
                    "prompt_key": key,
                    "live": next((row for row in rows if row.get("status") == STATUS_LIVE), None),
                    "pending": [row for row in rows if row.get("status") == STATUS_PENDING],
                    "shadow": next((row for row in rows if row.get("status") == STATUS_SHADOW), None),
                    "retired": [row for row in rows if row.get("status") == STATUS_RETIRED],
                }
            )
        return {
            "tab_id": self.tab_id,
            "metric": self.metric_name,
            "objective": load_objective(skill_dir).as_dict(),
            "prompts": prompts,
        }


def get_tab_agent(tab_id: str) -> TabAgent:
    key = str(tab_id or "").strip().lower()
    if key == "today":
        from core.today_tab_agent import TodayTabAgent

        return TodayTabAgent()
    if key == "debate":
        from core.debate_tab_agent import DebateTabAgent

        return DebateTabAgent()
    if key == "research":
        from core.research_tab_agent import ResearchTabAgent

        return ResearchTabAgent()
    if key == "backtest":
        from core.backtest_tab_agent import BacktestTabAgent

        return BacktestTabAgent()
    if key == "system":
        from core.system_tab_agent import SystemTabAgent

        return SystemTabAgent()
    raise TabAgentError(
        f"Unknown tab agent {tab_id!r}. Registered tabs: today, debate, research, backtest, system. "
        "Other tabs plug in by subclassing TabAgent and adding a branch in get_tab_agent."
    )
