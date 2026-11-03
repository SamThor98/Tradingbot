"""Pre-registered backtest hypothesis cards.

A card names one change, the command or queue spec that tests it, and the
profit-factor floors it must meet. Scoring copies numbers out of an artifact.
It does not recompute them, edit ``.env``, or append the promotion ledger.

``settled`` is a finished queue card. The weekend supervisor writes it when a
matching dashboard run has status ``success`` or ``failed``. Scoring and the
campaign planner leave it alone. ``network_status.json`` counts the pile.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.weekend_backtest_campaign import spec_fingerprint

LOG = logging.getLogger(__name__)

SCHEMA_VERSION = 1
CARD_STATUSES = frozenset({"proposed", "ready", "settled", "discarded"})
FILED_STATUS_ORDER = ("proposed", "ready", "settled", "discarded")
FINISHED_RUN_STATUSES = frozenset({"success", "failed"})
NETWORK_STATUS_NAME = "network_status.json"
CARD_KINDS = frozenset({"queue_spec", "artifact"})
CARD_DECISIONS = frozenset({"clears_floors", "quote_only"})
QUEUE_BASE_OVERRIDES = {"skip_mirofish": True, "forensic_enabled": False}
DEFAULT_PF_MEAN = 1.2
DEFAULT_WORST_ERA_PF = 1.0
CARDS_DIRNAME = Path("research") / "hypothesis_cards"
RESULTS_DIRNAME = Path("artifacts") / "hypothesis_cards"

ALLOWED_SCRIPTS = frozenset(
    {
        "analyze_signal_stack_counterfactual.py",
        "compare_cap37_unsorted_vs_live.py",
        "phase2_edge_audit.py",
        "run_multi_era_backtest_schwab_only.py",
        "run_param_ablation.py",
        "score_ablation_report.py",
        "validate_pf_robustness.py",
        "liquid_funding_shadow_study.py",
    }
)

_CARD_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PATH_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*){0,5}$")
_ARG_RE = re.compile(r"^[A-Za-z0-9_.=:@+\-,]+$")
_QUOTE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_CARD_KEYS = frozenset(
    {
        "schema_version",
        "card_id",
        "status",
        "theory_name",
        "question",
        "single_change",
        "floors",
        "decision",
        "kind",
        "command",
        "artifact",
        "metrics",
        "also_quote",
        "spec",
    }
)
_FLOOR_KEYS = frozenset({"pf_mean", "worst_era_pf", "min_trades", "min_retention_pct"})
_METRIC_KEYS = frozenset({"pf_mean", "worst_era_pf", "n_trades", "verdict", "retention_pct"})
_REQUIRED_METRICS = ("pf_mean", "worst_era_pf", "n_trades", "verdict")
_CHANGE_KEYS = frozenset({"field", "baseline", "candidate"})


@dataclass(frozen=True)
class HypothesisCard:
    raw: dict[str, Any]
    fingerprint: str
    queue_fingerprint: str | None

    @property
    def card_id(self) -> str:
        return str(self.raw["card_id"])

    @property
    def status(self) -> str:
        return str(self.raw["status"])

    @property
    def kind(self) -> str:
        return str(self.raw["kind"])

    @property
    def decision(self) -> str:
        return str(self.raw["decision"])

    @property
    def theory_name(self) -> str:
        return str(self.raw["theory_name"])


def cards_dir(skill_dir: Path) -> Path:
    return skill_dir / CARDS_DIRNAME


def results_dir(skill_dir: Path) -> Path:
    return skill_dir / RESULTS_DIRNAME


def promotion_floors(skill_dir: Path) -> tuple[float, float]:
    path = skill_dir / "signal_edge_ledger.json"
    if not path.is_file():
        return DEFAULT_PF_MEAN, DEFAULT_WORST_ERA_PF
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return DEFAULT_PF_MEAN, DEFAULT_WORST_ERA_PF
    floors = payload.get("promotion_floors") if isinstance(payload, dict) else None
    if not isinstance(floors, dict):
        return DEFAULT_PF_MEAN, DEFAULT_WORST_ERA_PF
    try:
        return float(floors["pf_mean"]), float(floors["worst_era_pf"])
    except (KeyError, TypeError, ValueError):
        return DEFAULT_PF_MEAN, DEFAULT_WORST_ERA_PF


def load_cards(skill_dir: Path) -> tuple[list[HypothesisCard], list[str]]:
    """Load every card. Issues are returned; valid cards are still returned."""
    directory = cards_dir(skill_dir)
    if not directory.is_dir():
        return [], []
    issues: list[str] = []
    cards: list[HypothesisCard] = []
    seen_ids: dict[str, str] = {}
    seen_fingerprints: dict[str, str] = {}
    pf_floor, worst_floor = promotion_floors(skill_dir)
    paths = sorted(p for p in directory.glob("*.json") if p.is_file())
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            issues.append(f"{path.name}: {exc}")
            continue
        card_issues = validate_card_payload(
            payload,
            filename=path.name,
            pf_floor=pf_floor,
            worst_floor=worst_floor,
        )
        if card_issues:
            issues.extend(card_issues)
            continue
        card = _freeze_card(payload)
        prior = seen_ids.get(card.card_id)
        if prior is not None:
            issues.append(f"{path.name}: duplicate card_id {card.card_id} (also {prior})")
            continue
        prior_fp = seen_fingerprints.get(card.fingerprint)
        if prior_fp is not None:
            issues.append(f"{path.name}: duplicate fingerprint with {prior_fp}")
            continue
        seen_ids[card.card_id] = path.name
        seen_fingerprints[card.fingerprint] = path.name
        cards.append(card)
    return cards, issues


def validate_card_payload(
    payload: Any,
    *,
    filename: str,
    pf_floor: float,
    worst_floor: float,
) -> list[str]:
    if not isinstance(payload, dict):
        return [f"{filename}: card must be a JSON object"]
    issues: list[str] = []
    extra = set(payload) - _CARD_KEYS
    if extra:
        issues.append(f"{filename}: unknown keys {sorted(extra)}")
    card_id = payload.get("card_id")
    if not isinstance(card_id, str) or not _CARD_ID_RE.match(card_id) or len(card_id) > 64:
        issues.append(f"{filename}: card_id must be a lowercase slug")
        card_id = filename
    if filename != f"{card_id}.json":
        issues.append(f"{filename}: file name must be {card_id}.json")
    if payload.get("schema_version") != SCHEMA_VERSION:
        issues.append(f"{filename}: schema_version must be {SCHEMA_VERSION}")
    if payload.get("status") not in CARD_STATUSES:
        issues.append(f"{filename}: status must be one of {sorted(CARD_STATUSES)}")
    if payload.get("kind") not in CARD_KINDS:
        issues.append(f"{filename}: kind must be one of {sorted(CARD_KINDS)}")
    if payload.get("decision") not in CARD_DECISIONS:
        issues.append(f"{filename}: decision must be one of {sorted(CARD_DECISIONS)}")
    theory = payload.get("theory_name")
    if not isinstance(theory, str) or not theory.strip() or len(theory) > 200:
        issues.append(f"{filename}: theory_name must be 1-200 characters")
    question = payload.get("question")
    if not isinstance(question, str) or not question.strip() or len(question) > 400 or "\n" in question:
        issues.append(f"{filename}: question must be a single line of 1-400 characters")
    issues.extend(_validate_change(payload.get("single_change"), filename))
    issues.extend(_validate_floors(payload.get("floors"), filename, pf_floor, worst_floor))
    kind = payload.get("kind")
    if kind == "artifact":
        issues.extend(_validate_artifact_card(payload, filename))
    elif kind == "queue_spec":
        issues.extend(_validate_queue_card(payload, filename))
    return issues


def settle_finished_queue_cards(skill_dir: Path, runs: list[dict[str, Any]]) -> list[str]:
    """Mark ready queue cards settled when a matching dashboard run has finished.

    Queued and running rows are left ready. A settled card is not a promotion:
    the result file keeps ``promote`` false, and a catalog window does not
    satisfy the five-era floors.
    """
    cards, issues = load_cards(skill_dir)
    for item in issues:
        LOG.warning("Hypothesis card rejected during settle: %s", item)
    settled: list[str] = []
    for card in cards:
        if card.kind != "queue_spec" or card.status != "ready" or not card.queue_fingerprint:
            continue
        match = _finished_run(runs, card.queue_fingerprint)
        if match is None:
            continue
        updated = dict(card.raw)
        updated["status"] = "settled"
        _dump_json(cards_dir(skill_dir) / f"{card.card_id}.json", updated)
        _dump_json(results_dir(skill_dir) / f"{card.card_id}.json", _settled_result(card, match))
        LOG.info("Settled hypothesis card %s from run %s", card.card_id, match.get("id"))
        settled.append(card.card_id)
    return settled


def augment_campaign_plan(
    plan: list[dict[str, Any]],
    skill_dir: Path,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Prepend ready queue cards. Settled and proposed cards stay off the queue."""
    cards, issues = load_cards(skill_dir)
    notes = [f"Hypothesis card rejected: {item}" for item in issues]
    known = {str(row.get("fingerprint") or "") for row in plan}
    front: list[dict[str, Any]] = []
    for card in cards:
        if card.kind != "queue_spec" or card.status != "ready":
            continue
        spec = dict(card.raw["spec"])
        fingerprint = spec_fingerprint(spec)
        if fingerprint in known:
            notes.append(f"Hypothesis card {card.card_id} skipped; fingerprint already in the plan")
            continue
        known.add(fingerprint)
        front.append(_queue_plan_row(card, spec, fingerprint))
        notes.append(f"Hypothesis card {card.card_id} queued ahead of the catalog")
    if not notes:
        notes.append("Hypothesis cards: none ready for the queue")
    return front + list(plan), notes


def score_all_cards(skill_dir: Path) -> dict[str, Any]:
    """Write one result file per open card, then the reject-rate rollup.

    Does not run commands. Discarded cards are counted and not scored.
    """
    cards, issues = load_cards(skill_dir)
    summary: dict[str, Any] = {
        "scored": 0,
        "artifact_missing": 0,
        "not_enqueued": 0,
        "errors": list(issues),
    }
    out_dir = results_dir(skill_dir)
    results: dict[str, dict[str, Any]] = {}
    for card in cards:
        if card.status == "discarded":
            continue
        try:
            result = score_card(card, skill_dir)
        except Exception as exc:
            LOG.exception("Hypothesis card score failed: %s", card.card_id)
            summary["errors"].append(f"{card.card_id}: {type(exc).__name__}")
            continue
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"{card.card_id}.json"
        preserved = card.status == "settled" and str(result.get("gate") or "").startswith("settled_")
        if not preserved or not target.is_file():
            _dump_json(target, result)
        results[card.card_id] = result
        summary["scored"] += 1
        gate = result.get("gate")
        if gate == "artifact_missing":
            summary["artifact_missing"] += 1
        elif gate == "not_enqueued":
            summary["not_enqueued"] += 1
    status_doc = write_network_status(skill_dir, cards, results)
    summary["network_status"] = {
        key: status_doc[key]
        for key in (
            "filed",
            "scored_with_artifact",
            "floors_cleared",
            "floors_missed",
            "queue_waiting",
            "promote_not_false",
        )
    }
    return summary


def write_network_status(
    skill_dir: Path,
    cards: list[HypothesisCard],
    results: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Count filed, cleared, missed, and waiting cards. Does not recompute profit factors."""
    filed = {status: 0 for status in FILED_STATUS_ORDER}
    scored_with_artifact = 0
    floors_cleared = 0
    floors_missed = 0
    queue_waiting = 0
    promote_not_false = 0
    rows: list[dict[str, Any]] = []
    for card in cards:
        filed[card.status] = filed.get(card.status, 0) + 1
        if card.kind == "queue_spec" and card.status == "ready":
            queue_waiting += 1
        result = results.get(card.card_id)
        if result is None:
            rows.append(
                {
                    "card_id": card.card_id,
                    "status": card.status,
                    "kind": card.kind,
                    "gate": None,
                    "clears_numeric_floors": None,
                    "promote": None,
                }
            )
            continue
        if result.get("promote") is not False:
            promote_not_false += 1
        clears = result.get("clears_numeric_floors")
        if clears is True:
            floors_cleared += 1
        elif clears is False:
            floors_missed += 1
        source = result.get("source_artifact")
        metrics = result.get("metrics")
        if (
            isinstance(source, str)
            and source
            and isinstance(metrics, dict)
            and result.get("gate") not in {"artifact_missing", "metric_missing"}
        ):
            scored_with_artifact += 1
        rows.append(
            {
                "card_id": card.card_id,
                "status": card.status,
                "kind": card.kind,
                "gate": result.get("gate"),
                "clears_numeric_floors": clears if isinstance(clears, bool) else None,
                "promote": result.get("promote"),
            }
        )
    document = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "filed": filed,
        "scored_with_artifact": scored_with_artifact,
        "floors_cleared": floors_cleared,
        "floors_missed": floors_missed,
        "queue_waiting": queue_waiting,
        "promote_not_false": promote_not_false,
        "rows": rows,
    }
    _dump_json(results_dir(skill_dir) / NETWORK_STATUS_NAME, document)
    return document


def _result_note(kind: str) -> str:
    if kind == "artifact":
        return "Metrics were copied from the artifact. This file does not promote a plugin or edit .env."
    return "Queue spec has no artifact yet. This file does not promote a plugin or edit .env."


def score_card(card: HypothesisCard, skill_dir: Path) -> dict[str, Any]:
    scored_at = datetime.now(timezone.utc).isoformat()
    base = {
        "schema_version": SCHEMA_VERSION,
        "card_id": card.card_id,
        "fingerprint": card.fingerprint,
        "queue_fingerprint": card.queue_fingerprint,
        "status": card.status,
        "decision": card.decision,
        "kind": card.kind,
        "theory_name": card.theory_name,
        "question": card.raw.get("question"),
        "single_change": card.raw.get("single_change"),
        "command": card.raw.get("command"),
        "floors": card.raw.get("floors"),
        "scored_at": scored_at,
        "promote": False,
        "note": _result_note(card.kind),
    }
    if card.kind == "queue_spec":
        if card.status == "settled":
            existing = _load_result(skill_dir, card.card_id)
            if (
                isinstance(existing, dict)
                and existing.get("promote") is False
                and str(existing.get("gate") or "").startswith("settled_")
            ):
                return existing
            gate = "settled"
        elif card.status == "ready":
            gate = "queued_when_supervisor_runs"
        else:
            gate = "not_enqueued"
        return {
            **base,
            "source_artifact": None,
            "source_generated_at": None,
            "metrics": None,
            "quoted": {},
            "gate": gate,
            "clears_numeric_floors": None,
        }
    artifact_rel = str(card.raw["artifact"])
    artifact_path = _resolve_artifact(skill_dir, artifact_rel)
    if artifact_path is None or not artifact_path.is_file():
        return {
            **base,
            "source_artifact": artifact_rel,
            "source_generated_at": None,
            "metrics": None,
            "quoted": {},
            "gate": "artifact_missing",
            "clears_numeric_floors": None,
        }
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{card.card_id}: artifact root must be an object")
    metrics, metric_ok = _copy_metrics(payload, dict(card.raw["metrics"]))
    quoted = _copy_quotes(payload, dict(card.raw.get("also_quote") or {}))
    clears = _clears_floors(metrics, dict(card.raw["floors"])) if metric_ok else None
    if not metric_ok:
        gate = "metric_missing"
    elif card.decision == "quote_only":
        gate = "quoted"
    elif clears:
        gate = "clears_floors"
    else:
        gate = "misses_floors"
    generated = payload.get("generated_at")
    return {
        **base,
        "source_artifact": artifact_rel.replace("\\", "/"),
        "source_generated_at": generated if isinstance(generated, str) else None,
        "metrics": metrics,
        "quoted": quoted,
        "gate": gate,
        "clears_numeric_floors": clears,
    }


def executable_argv(command: list[str]) -> list[str]:
    """Run the card with this interpreter. The stored command stays ``python``."""
    if len(command) < 2:
        raise ValueError("command is too short")
    return [sys.executable, *command[1:]]


def _freeze_card(payload: dict[str, Any]) -> HypothesisCard:
    queue_fp: str | None = None
    if payload["kind"] == "queue_spec":
        queue_fp = spec_fingerprint(dict(payload["spec"]))
        fingerprint = "queue|" + queue_fp
    else:
        blob = {
            "artifact": payload["artifact"],
            "also_quote": payload.get("also_quote") or {},
            "command": payload["command"],
            "decision": payload["decision"],
            "metrics": payload["metrics"],
            "single_change": payload["single_change"],
        }
        digest = hashlib.sha256(json.dumps(blob, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[
            :16
        ]
        fingerprint = "artifact|" + digest
    return HypothesisCard(raw=payload, fingerprint=fingerprint, queue_fingerprint=queue_fp)


def _dump_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _load_result(skill_dir: Path, card_id: str) -> dict[str, Any] | None:
    path = results_dir(skill_dir) / f"{card_id}.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _finished_run(runs: list[dict[str, Any]], fingerprint: str) -> dict[str, Any] | None:
    matches: list[dict[str, Any]] = []
    for row in runs:
        if not isinstance(row, dict) or str(row.get("status") or "") not in FINISHED_RUN_STATUSES:
            continue
        spec = row.get("spec")
        if not isinstance(spec, dict) or not (spec.get("theory_name") or spec.get("start_date")):
            continue
        if spec_fingerprint(spec) != fingerprint:
            continue
        matches.append(row)
    if not matches:
        return None

    def _rank(row: dict[str, Any]) -> tuple[int, str]:
        status_rank = 1 if row.get("status") == "success" else 0
        created = row.get("created_at")
        return status_rank, created if isinstance(created, str) else ""

    return max(matches, key=_rank)


def _settled_result(card: HypothesisCard, run: dict[str, Any]) -> dict[str, Any]:
    status = str(run.get("status") or "")
    created = run.get("created_at")
    summary = run.get("result_summary") if isinstance(run.get("result_summary"), dict) else {}
    quoted: dict[str, Any] = {"run_status": status}
    run_id = run.get("id")
    if isinstance(run_id, str) and run_id.strip():
        quoted["run_id"] = run_id
    trades = _whole_number(summary.get("total_trades"))
    if trades is not None:
        quoted["total_trades"] = trades
    for key in ("profit_factor_net", "total_return_net_pct", "max_drawdown_net_pct"):
        number = _number(summary.get(key))
        if number is not None:
            quoted[key] = number
    return {
        "schema_version": SCHEMA_VERSION,
        "card_id": card.card_id,
        "fingerprint": card.fingerprint,
        "queue_fingerprint": card.queue_fingerprint,
        "status": "settled",
        "decision": card.decision,
        "kind": card.kind,
        "theory_name": card.theory_name,
        "question": card.raw.get("question"),
        "single_change": card.raw.get("single_change"),
        "floors": card.raw.get("floors"),
        "scored_at": datetime.now(timezone.utc).isoformat(),
        "promote": False,
        "note": (
            "Dashboard run finished. This file does not promote a plugin or edit .env. "
            "A catalog window is not the five-era floor."
        ),
        "source_artifact": None,
        "source_generated_at": created if isinstance(created, str) else None,
        "metrics": None,
        "quoted": quoted,
        "gate": "settled_success" if status == "success" else "settled_failed",
        "clears_numeric_floors": None,
    }


def _queue_plan_row(card: HypothesisCard, spec: dict[str, Any], fingerprint: str) -> dict[str, Any]:
    start = datetime.strptime(str(spec["start_date"]), "%Y-%m-%d")
    end = datetime.strptime(str(spec["end_date"]), "%Y-%m-%d")
    years = max(1, round((end - start).days / 365.25))
    return {
        "wave": 0,
        "theory_name": card.theory_name[:200],
        "fingerprint": fingerprint,
        "window_years": years,
        "spec": spec,
        "source": "hypothesis_card",
        "card_id": card.card_id,
    }


def _validate_change(change: Any, filename: str) -> list[str]:
    if not isinstance(change, dict) or set(change) != _CHANGE_KEYS:
        return [f"{filename}: single_change needs field, baseline, and candidate"]
    field = change.get("field")
    if not isinstance(field, str) or not _FIELD_RE.match(field):
        return [f"{filename}: single_change.field must be an identifier"]
    issues: list[str] = []
    for key in ("baseline", "candidate"):
        if not _scalar(change.get(key)):
            issues.append(f"{filename}: single_change.{key} must be a string, number, or bool")
    return issues


def _validate_floors(floors: Any, filename: str, pf_floor: float, worst_floor: float) -> list[str]:
    if not isinstance(floors, dict) or not set(floors) <= _FLOOR_KEYS:
        return [f"{filename}: floors has unknown or missing keys"]
    issues: list[str] = []
    pf = _number(floors.get("pf_mean"))
    worst = _number(floors.get("worst_era_pf"))
    if pf is None or worst is None:
        return [f"{filename}: floors.pf_mean and floors.worst_era_pf must be finite numbers"]
    if pf < pf_floor or worst < worst_floor:
        issues.append(f"{filename}: floors cannot be weaker than the ledger ({pf_floor}, {worst_floor})")
    if "min_trades" in floors:
        trades = floors.get("min_trades")
        if isinstance(trades, bool) or not isinstance(trades, int) or trades < 1:
            issues.append(f"{filename}: floors.min_trades must be an integer >= 1")
    if "min_retention_pct" in floors and _number(floors.get("min_retention_pct")) is None:
        issues.append(f"{filename}: floors.min_retention_pct must be a finite number")
    return issues


def _validate_artifact_card(payload: dict[str, Any], filename: str) -> list[str]:
    issues: list[str] = []
    if "spec" in payload:
        issues.append(f"{filename}: artifact cards do not take a spec")
    command = payload.get("command")
    if not isinstance(command, list) or not (2 <= len(command) <= 16):
        issues.append(f"{filename}: command must be a 2-16 element list")
    else:
        issues.extend(_validate_command(command, filename))
    artifact = payload.get("artifact")
    if not isinstance(artifact, str) or not _safe_relative(artifact):
        issues.append(f"{filename}: artifact must be a relative path inside the skill directory")
    metrics = payload.get("metrics")
    if not isinstance(metrics, dict) or not set(_REQUIRED_METRICS) <= set(metrics) or not set(metrics) <= _METRIC_KEYS:
        issues.append(f"{filename}: metrics must map pf_mean, worst_era_pf, n_trades, and verdict")
    elif isinstance(metrics, dict):
        for key, path in metrics.items():
            if not isinstance(path, str) or not _PATH_RE.match(path):
                issues.append(f"{filename}: metrics.{key} path is not allowed")
    also = payload.get("also_quote", {})
    if also is None:
        also = {}
    if "also_quote" in payload and not isinstance(payload.get("also_quote"), dict):
        issues.append(f"{filename}: also_quote must be an object")
    elif isinstance(also, dict):
        if len(also) > 12:
            issues.append(f"{filename}: also_quote is limited to 12 fields")
        for key, path in also.items():
            if not isinstance(key, str) or not _QUOTE_NAME_RE.match(key):
                issues.append(f"{filename}: also_quote key {key!r} is not allowed")
            if not isinstance(path, str) or not _PATH_RE.match(path):
                issues.append(f"{filename}: also_quote.{key} path is not allowed")
    return issues


def _validate_queue_card(payload: dict[str, Any], filename: str) -> list[str]:
    issues: list[str] = []
    for key in ("command", "artifact", "metrics", "also_quote"):
        if key in payload:
            issues.append(f"{filename}: queue cards do not take {key}")
    spec = payload.get("spec")
    if not isinstance(spec, dict):
        return [*issues, f"{filename}: queue cards need a spec object"]
    try:
        from webapp.backtest_spec import parse_strategy_spec

        parse_strategy_spec(spec)
    except Exception as exc:
        issues.append(f"{filename}: spec rejected: {exc}")
        return issues
    overrides = spec.get("overrides")
    if not isinstance(overrides, dict):
        return [*issues, f"{filename}: spec.overrides must include the unsupervised base flags"]
    for key, expected in QUEUE_BASE_OVERRIDES.items():
        if overrides.get(key) is not expected:
            issues.append(f"{filename}: spec.overrides.{key} must be {expected}")
    extra = {key: value for key, value in overrides.items() if key not in QUEUE_BASE_OVERRIDES}
    change = payload.get("single_change") if isinstance(payload.get("single_change"), dict) else {}
    field = change.get("field")
    if len(extra) != 1 or field not in extra:
        issues.append(f"{filename}: spec must change only single_change.field")
    elif not _same_scalar(extra.get(field), change.get("candidate")):
        issues.append(f"{filename}: spec override must equal single_change.candidate")
    return issues


def _validate_command(command: list[Any], filename: str) -> list[str]:
    if command[0] not in {"python", "python3"}:
        return [f"{filename}: command must start with python"]
    script = command[1]
    if not isinstance(script, str) or not script.startswith("scripts/") or "/" in script[8:]:
        return [f"{filename}: command must call scripts/<allowed>.py"]
    name = script.removeprefix("scripts/")
    if name not in ALLOWED_SCRIPTS:
        return [f"{filename}: {name} is not an allowed backtest command"]
    issues: list[str] = []
    for arg in command[2:]:
        if not isinstance(arg, str) or not _ARG_RE.match(arg) or ".." in arg:
            issues.append(f"{filename}: command arg {arg!r} is not allowed")
    return issues


def _safe_relative(value: str) -> bool:
    if not value or value.startswith(("/", "\\")) or ".." in Path(value).parts:
        return False
    if ":" in value:
        return False
    return True


def _resolve_artifact(skill_dir: Path, relative: str) -> Path | None:
    if not _safe_relative(relative):
        return None
    root = skill_dir.resolve()
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path


def _copy_metrics(payload: dict[str, Any], paths: dict[str, str]) -> tuple[dict[str, Any], bool]:
    copied: dict[str, Any] = {}
    ok = True
    for key in _REQUIRED_METRICS:
        value = _lookup(payload, paths[key])
        if key == "verdict":
            if not isinstance(value, str) or not value.strip():
                ok = False
                copied[key] = None
            else:
                copied[key] = value
            continue
        if key == "n_trades":
            number = _whole_number(value)
        else:
            number = _number(value)
        if number is None:
            ok = False
        copied[key] = number
    if "retention_pct" in paths:
        copied["retention_pct"] = _number(_lookup(payload, paths["retention_pct"]))
        if copied["retention_pct"] is None:
            ok = False
    return copied, ok


def _copy_quotes(payload: dict[str, Any], paths: dict[str, str]) -> dict[str, Any]:
    quoted: dict[str, Any] = {}
    for key, path in paths.items():
        value = _lookup(payload, path)
        if isinstance(value, (str, int, float, bool)) or value is None:
            if isinstance(value, float) and not math.isfinite(value):
                quoted[key] = None
            else:
                quoted[key] = value
        else:
            quoted[key] = None
    return quoted


def _clears_floors(metrics: dict[str, Any], floors: dict[str, Any]) -> bool:
    pf = _number(metrics.get("pf_mean"))
    worst = _number(metrics.get("worst_era_pf"))
    trades = _whole_number(metrics.get("n_trades"))
    if pf is None or worst is None or trades is None:
        return False
    min_trades = floors.get("min_trades", 1)
    if pf < float(floors["pf_mean"]) or worst < float(floors["worst_era_pf"]) or trades < int(min_trades):
        return False
    if "min_retention_pct" in floors:
        retention = _number(metrics.get("retention_pct"))
        if retention is None or retention < float(floors["min_retention_pct"]):
            return False
    return True


def _lookup(payload: dict[str, Any], path: str) -> Any:
    if not _PATH_RE.match(path):
        return None
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _scalar(value: Any) -> bool:
    if isinstance(value, bool):
        return True
    if isinstance(value, str):
        return bool(value.strip()) and "\n" not in value and len(value) <= 120
    return _number(value) is not None


def _same_scalar(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return left is right
    left_num = _number(left)
    right_num = _number(right)
    if left_num is not None and right_num is not None:
        return left_num == right_num
    return left == right


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def _whole_number(value: Any) -> int | None:
    number = _number(value)
    if number is None or not number.is_integer() or number < 0:
        return None
    return int(number)
