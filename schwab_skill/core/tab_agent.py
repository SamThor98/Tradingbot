"""Generic per-tab prompt agent: versioned prompts, scoring, and manual promotion.

Other dashboard tabs plug in by subclassing :class:`TabAgent` and registering
in :func:`get_tab_agent`. Nothing in this module places orders or marks a
prompt live except :meth:`TabAgent.approve` and :meth:`TabAgent.rollback`.
"""

from __future__ import annotations

import json
import logging
import math
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

LOG = logging.getLogger(__name__)

REGISTRY_FILE = ".tab_agent_registry.json"
ATTRIBUTION_FILE = ".tab_agent_attributions.jsonl"

STATUS_LIVE = "live"
STATUS_PENDING = "pending"
STATUS_SHADOW = "shadow"
STATUS_REJECTED = "rejected"
STATUS_RETIRED = "retired"

# Realized-return deadband (%), aligned with outcome backfill scratches.
SCRATCH_BAND_PCT = 0.5
WINNER_RULE = (
    "A version is better than live only when both samples meet the minimum, "
    "the 95% Wilson intervals on win rate do not overlap, and its average "
    "realized return is higher. That flag is informational. Promotion is manual."
)


class TabAgentError(ValueError):
    """Operator-facing registry error (unknown id, illegal transition)."""


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


def wilson_ci(successes: int, n: int, *, z: float = 1.96) -> tuple[float, float] | None:
    """95% Wilson interval for a binomial rate. None when n is 0."""
    if n <= 0:
        return None
    successes = max(0, min(int(successes), int(n)))
    n = int(n)
    p = successes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denom
    margin = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n) / denom
    return (round(max(0.0, center - margin), 4), round(min(1.0, center + margin), 4))


def _mean_ci(values: list[float], *, z: float = 1.96) -> tuple[float, float] | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    se = math.sqrt(var / len(values))
    return (round(mean - z * se, 4), round(mean + z * se, 4))


def _f(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


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
    """File-backed prompt versions for every tab."""

    def __init__(self, skill_dir: Path | str) -> None:
        self.skill_dir = Path(skill_dir)
        self.path = _store_path(self.skill_dir, REGISTRY_FILE)

    def _read(self) -> dict[str, Any]:
        data = _load_json(self.path)
        data.setdefault("tabs", {})
        return data

    def _write(self, data: dict[str, Any]) -> None:
        _save_json(self.path, data)

    def _key_bucket(self, data: dict[str, Any], tab_id: str, prompt_key: str) -> dict[str, Any]:
        tab = data.setdefault("tabs", {}).setdefault(tab_id, {}).setdefault("keys", {})
        bucket = tab.setdefault(prompt_key, {"versions": []})
        bucket.setdefault("versions", [])
        return bucket

    def versions(self, tab_id: str, prompt_key: str | None = None) -> list[dict[str, Any]]:
        data = self._read()
        keys = data.get("tabs", {}).get(tab_id, {}).get("keys", {})
        if prompt_key is not None:
            rows = keys.get(prompt_key, {}).get("versions", [])
            return [dict(v) for v in rows if isinstance(v, dict)]
        out: list[dict[str, Any]] = []
        for bucket in keys.values():
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
            bucket = self._key_bucket(data, tab_id, prompt_key)
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
            raise TabAgentError("New versions are pending until explicitly approved.")
        data = self._read()
        bucket = self._key_bucket(data, tab_id, prompt_key)
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
        bucket["versions"].append(row)
        self._write(data)
        return dict(row)

    def _mutate(
        self, tab_id: str, version_id: str, fn: Callable[[dict[str, Any], list[dict[str, Any]]], None]
    ) -> dict[str, Any]:
        data = self._read()
        keys = data.get("tabs", {}).get(tab_id, {}).get("keys", {})
        for bucket in keys.values():
            rows = bucket.get("versions", [])
            for row in rows:
                if isinstance(row, dict) and row.get("id") == version_id:
                    fn(row, rows)
                    self._write(data)
                    return dict(row)
        raise TabAgentError(f"Unknown prompt version: {version_id}")

    def live_version(self, tab_id: str, prompt_key: str) -> dict[str, Any] | None:
        live = [v for v in self.versions(tab_id, prompt_key) if v.get("status") == STATUS_LIVE]
        return live[-1] if live else None

    def approve(self, tab_id: str, version_id: str) -> dict[str, Any]:
        target = self.get(tab_id, version_id)
        if target is None:
            raise TabAgentError(f"Unknown prompt version: {version_id}")
        if target.get("status") == STATUS_LIVE:
            return target
        if target.get("status") not in (STATUS_PENDING, STATUS_SHADOW):
            raise TabAgentError(
                f"Version {version_id} is {target.get('status')}; only pending or shadow versions can be approved."
            )
        prompt_key = str(target.get("prompt_key") or "")

        def _apply(row: dict[str, Any], rows: list[dict[str, Any]]) -> None:
            for other in rows:
                if other.get("id") != version_id and other.get("status") == STATUS_LIVE:
                    other["status"] = STATUS_RETIRED
            row["status"] = STATUS_LIVE

        return self._mutate(tab_id, version_id, _apply) if prompt_key else target

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
        prompt_key = str(target.get("prompt_key") or "")

        def _apply(row: dict[str, Any], rows: list[dict[str, Any]]) -> None:
            for other in rows:
                if (
                    other.get("prompt_key") == prompt_key
                    and other.get("status") == STATUS_SHADOW
                    and other.get("id") != version_id
                ):
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
            raise TabAgentError(
                "Rollback only restores a retired live version. Pending proposals stay pending until you approve them."
            )

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
    """Append which prompt versions were active for one scanned ticker."""
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
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        if tab_id and row.get("tab_id") != tab_id:
            continue
        out.append(row)
    return out


def _packet_return(packet: dict[str, Any], *, horizon_days: int) -> tuple[str, float] | None:
    outcome = packet.get("outcome") if isinstance(packet.get("outcome"), dict) else {}
    label = str(outcome.get("label") or "pending").lower()
    if label in ("pending", "unknown", ""):
        return None
    horizon = outcome.get("horizon_days")
    if horizon is None or int(horizon) != int(horizon_days):
        return None
    ret = _f(outcome.get("realized_return_pct"))
    if ret is None:
        return None
    return label, ret


def _event_return(event: dict[str, Any]) -> float | None:
    for key in ("forward_return_pct", "realized_return_pct"):
        ret = _f(event.get(key))
        if ret is not None:
            return ret
    return None


def _is_suppress_reason(reason: str) -> bool:
    text = reason.strip().lower()
    if not text:
        return False
    if "suppress" in text or text.startswith("filtered") or "blocked" in text:
        return True
    return text.startswith(("meta_policy", "uncertainty", "quality_"))


def score_prompt_versions(
    *,
    versions: list[dict[str, Any]],
    packets: list[dict[str, Any]],
    events: list[dict[str, Any]] | None = None,
    attributions: list[dict[str, Any]] | None = None,
    min_samples: int = 30,
    horizon_days: int = 10,
) -> dict[str, Any]:
    """Score each prompt version. Small samples stay non-decisive."""
    events = events or []
    attributions = attributions or []
    min_samples = max(1, int(min_samples))
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

    version_stats: list[dict[str, Any]] = []
    for version in versions:
        vid = str(version.get("id") or "")
        uses = by_version.get(vid, [])
        seen: set[tuple[str, str]] = set()
        labels: list[str] = []
        returns: list[float] = []
        for use in uses:
            ticker = str(use.get("ticker") or "").upper()
            day = str(use.get("day") or "")
            if not ticker or not day or (ticker, day) in seen:
                continue
            seen.add((ticker, day))
            packet = packet_index.get((ticker, day))
            if packet is None:
                continue
            parsed = _packet_return(packet, horizon_days=horizon_days)
            if parsed is None:
                continue
            labels.append(parsed[0])
            returns.append(parsed[1])
        wins = sum(1 for label in labels if label == "win")
        n = len(labels)
        win_rate = round(wins / n, 4) if n else None
        avg = round(sum(returns) / len(returns), 4) if returns else None
        suppress_flags: list[bool] = []
        suppress_seen: set[tuple[str, str, str]] = set()
        use_keys = {(str(u.get("ticker") or "").upper(), str(u.get("day") or "")) for u in uses}
        for event in events:
            if not isinstance(event, dict) or not _is_suppress_reason(str(event.get("reason") or "")):
                continue
            ticker = str(event.get("ticker") or "").upper()
            day = utc_day(event.get("ts") or event.get("created_at"))
            if (ticker, day) not in use_keys:
                continue
            ret = _event_return(event)
            if ret is None:
                continue
            dedupe = (ticker, day or "", str(event.get("reason") or ""))
            if dedupe in suppress_seen:
                continue
            suppress_seen.add(dedupe)
            suppress_flags.append(ret < -SCRATCH_BAND_PCT)
        sn = len(suppress_flags)
        s_wins = sum(1 for flag in suppress_flags if flag)
        version_stats.append(
            {
                "version_id": vid,
                "prompt_key": version.get("prompt_key"),
                "status": version.get("status"),
                "parent": version.get("parent"),
                "n": n,
                "wins": wins,
                "sufficient": n >= min_samples,
                "win_rate": win_rate,
                "win_rate_ci95": list(wilson_ci(wins, n) or []) or None,
                "avg_realized_return_pct": avg,
                "avg_return_ci95": list(_mean_ci(returns) or []) or None,
                "horizon_days": int(horizon_days),
                "suppress_n": sn,
                "suppress_precision": round(s_wins / sn, 4) if sn else None,
                "suppress_precision_ci95": list(wilson_ci(s_wins, sn) or []) or None,
                "suppress_sufficient": sn >= min_samples,
            }
        )

    by_id = {row["version_id"]: row for row in version_stats}
    comparisons: list[dict[str, Any]] = []
    for stat in version_stats:
        if stat.get("status") == STATUS_LIVE:
            continue
        parent_id = stat.get("parent")
        live_candidates = [
            row
            for row in version_stats
            if row.get("prompt_key") == stat.get("prompt_key") and row.get("status") == STATUS_LIVE
        ]
        baseline = by_id.get(str(parent_id or "")) or (live_candidates[-1] if live_candidates else None)
        comparisons.append(_compare(stat, baseline, min_samples=min_samples))

    return {
        "min_samples": min_samples,
        "horizon_days": int(horizon_days),
        "winner_rule": WINNER_RULE,
        "auto_promote": False,
        "versions": version_stats,
        "comparisons": comparisons,
    }


def _ci_separated(left: list[float] | None, right: list[float] | None) -> str | None:
    if not left or not right or len(left) != 2 or len(right) != 2:
        return None
    if left[0] > right[1]:
        return "higher"
    if left[1] < right[0]:
        return "lower"
    return "overlap"


def _compare(candidate: dict[str, Any], baseline: dict[str, Any] | None, *, min_samples: int) -> dict[str, Any]:
    out: dict[str, Any] = {
        "version_id": candidate.get("version_id"),
        "prompt_key": candidate.get("prompt_key"),
        "baseline_version_id": None if baseline is None else baseline.get("version_id"),
        "edge_vs_live": "insufficient_sample",
        "auto_promote": False,
    }
    if baseline is None or candidate.get("n", 0) < min_samples or baseline.get("n", 0) < min_samples:
        return out
    direction = _ci_separated(candidate.get("win_rate_ci95"), baseline.get("win_rate_ci95"))
    cand_avg = candidate.get("avg_realized_return_pct")
    base_avg = baseline.get("avg_realized_return_pct")
    if direction == "higher" and cand_avg is not None and base_avg is not None and cand_avg > base_avg:
        out["edge_vs_live"] = "better_than_live"
    elif direction == "lower" and cand_avg is not None and base_avg is not None and cand_avg < base_avg:
        out["edge_vs_live"] = "worse_than_live"
    else:
        out["edge_vs_live"] = "no_separation"
    return out


class TabAgent(ABC):
    """One dashboard tab's prompts, score, and manual lifecycle.

    Subclasses supply the prompt catalog, the score inputs, and the improver.
    approve / reject / rollback / shadow are shared and never call an LLM.
    """

    tab_id: str
    metric_name: str

    @abstractmethod
    def prompt_catalog(self) -> dict[str, str]:
        """Built-in prompt text keyed by prompt id. Seeds the registry once."""

    @abstractmethod
    def score(self, skill_dir: Path, *, registry: PromptRegistry | None = None) -> dict[str, Any]:
        """Grade prompt versions for this tab. Must not promote anything."""

    @abstractmethod
    def propose(
        self,
        skill_dir: Path,
        *,
        registry: PromptRegistry | None = None,
        llm: Callable[[str, str], str] | None = None,
        prompt_key: str | None = None,
    ) -> dict[str, Any]:
        """Store PENDING revisions. Must not mark them live."""

    def registry(self, skill_dir: Path) -> PromptRegistry:
        reg = PromptRegistry(skill_dir)
        reg.ensure_seeded(
            self.tab_id,
            self.prompt_catalog(),
            rationale="Seeded from the tab's current built-in prompt.",
        )
        return reg

    def approve(self, skill_dir: Path, version_id: str) -> dict[str, Any]:
        return self.registry(skill_dir).approve(self.tab_id, version_id)

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
                    "live": next((r for r in rows if r.get("status") == STATUS_LIVE), None),
                    "pending": [r for r in rows if r.get("status") == STATUS_PENDING],
                    "shadow": next((r for r in rows if r.get("status") == STATUS_SHADOW), None),
                    "retired": [r for r in rows if r.get("status") == STATUS_RETIRED],
                }
            )
        return {"tab_id": self.tab_id, "metric": self.metric_name, "prompts": prompts}


def get_tab_agent(tab_id: str) -> TabAgent:
    key = str(tab_id or "").strip().lower()
    if key == "today":
        from core.today_tab_agent import TodayTabAgent

        return TodayTabAgent()
    if key == "debate":
        from core.debate_tab_agent import DebateTabAgent

        return DebateTabAgent()
    known = "today, debate"
    raise TabAgentError(
        f"Unknown tab agent {tab_id!r}. Registered tabs: {known}. "
        "Other tabs plug in by subclassing TabAgent and adding a branch in get_tab_agent."
    )
