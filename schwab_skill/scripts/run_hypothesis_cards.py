#!/usr/bin/env python3
"""Score hypothesis cards, enqueue one ready queue card, or run one artifact command.

Default mode copies metrics from artifacts that already exist. ``--enqueue-ready``
posts ready queue specs to the local dashboard and does not start the catalog
sweep. ``--run`` executes a ready artifact command whose script is on the allowlist.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parents[1]
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from config import bootstrap_dotenv_into_environ  # noqa: E402
from core.hypothesis_card import (  # noqa: E402
    augment_campaign_plan,
    executable_argv,
    load_cards,
    score_all_cards,
    score_card,
)


def _print_card_lines() -> int:
    cards, issues = load_cards(SKILL_DIR)
    for issue in issues:
        print(f"FAIL: {issue}")
    for card in cards:
        print(f"{card.status:9} {card.kind:10} {card.card_id} {card.fingerprint}")
    return 1 if issues else 0


def _run_ready(card_id: str, timeout_seconds: int) -> int:
    cards, issues = load_cards(SKILL_DIR)
    if issues:
        for issue in issues:
            print(f"FAIL: {issue}")
        return 1
    selected = [card for card in cards if card.card_id == card_id or not card_id]
    if card_id and not any(card.card_id == card_id for card in cards):
        print(f"FAIL: unknown card_id {card_id}")
        return 1
    ran = 0
    failed = 0
    for card in selected:
        if card.kind != "artifact" or card.status != "ready":
            print(f"SKIP {card.card_id}: --run only executes ready artifact cards")
            continue
        command = executable_argv(list(card.raw["command"]))
        print(" ".join(command))
        try:
            proc = subprocess.run(
                command,
                cwd=str(SKILL_DIR),
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            print(f"FAIL: {card.card_id} timed out after {timeout_seconds}s")
            failed += 1
            continue
        ran += 1
        if proc.returncode != 0:
            print(f"FAIL: {card.card_id} exited {proc.returncode}")
            failed += 1
        result = score_card(card, SKILL_DIR)
        out = SKILL_DIR / "artifacts" / "hypothesis_cards" / f"{card.card_id}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(f"SCORED {card.card_id} gate={result.get('gate')} promote={result.get('promote')}")
    if ran == 0 and card_id:
        return 1
    return 1 if failed else 0


def _weekend_module() -> Any:
    path = Path(__file__).resolve().parent / "run_weekend_backtest_campaign.py"
    spec = importlib.util.spec_from_file_location("weekend_campaign_supervisor", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load weekend supervisor from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _enqueue_ready(base_url: str) -> int:
    """Post ready queue cards only. Does not fill the weekend catalog."""
    bootstrap_dotenv_into_environ(SKILL_DIR)
    weekend = _weekend_module()
    client = weekend.DashboardClient(base_url, (os.getenv("WEB_API_KEY") or "").strip())
    try:
        runs = client.list_runs(100)
    except Exception as exc:
        print(f"FAIL: dashboard unreachable: {type(exc).__name__}: {exc}")
        return 1
    rows, notes = augment_campaign_plan([], SKILL_DIR)
    for note in notes:
        print(note)
    if not rows:
        print("No ready queue cards")
        return 0
    plan_fingerprints = {str(row.get("fingerprint") or "") for row in rows}
    state = weekend.load_state()
    known = weekend._known_fingerprints(
        runs,
        list(state.get("submitted") or []),
        plan_fingerprints=plan_fingerprints,
    )
    for row in rows:
        fingerprint = str(row["fingerprint"])
        card_id = str(row.get("card_id") or "")
        if fingerprint in known:
            print(f"SKIP {card_id} already on the dashboard")
            continue
        status, payload = client.queue_spec(dict(row["spec"]))
        if status not in (200, 201):
            print(f"FAIL {card_id} HTTP {status}")
            return 1
        data = payload.get("data") if isinstance(payload, dict) else {}
        run_id = str(data.get("run_id") or "") if isinstance(data, dict) else ""
        state.setdefault("submitted", []).append(
            {
                "fingerprint": fingerprint,
                "theory_name": row.get("theory_name"),
                "run_id": run_id,
                "card_id": card_id,
                "at": datetime.now(weekend.CHICAGO).isoformat(),
            }
        )
        weekend.save_state(state)
        known.add(fingerprint)
        print(f"QUEUED {card_id} run_id={run_id}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Score or run pre-registered hypothesis cards.")
    parser.add_argument("--list", action="store_true", help="Print cards and fingerprints.")
    parser.add_argument(
        "--run",
        action="store_true",
        help="Execute ready artifact commands, then score them.",
    )
    parser.add_argument("--card-id", default="", help="Limit --run to one card.")
    parser.add_argument(
        "--enqueue-ready",
        action="store_true",
        help="POST ready queue cards to the local dashboard. Does not start the catalog sweep.",
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("LOCAL_DASHBOARD_URL", "https://127.0.0.1:8182"),
    )
    parser.add_argument("--timeout-seconds", type=int, default=14400)
    args = parser.parse_args()
    if args.run and args.enqueue_ready:
        print("FAIL: --run and --enqueue-ready are separate actions")
        return 1
    if args.list:
        return _print_card_lines()
    if args.enqueue_ready:
        return _enqueue_ready(str(args.base_url))
    if args.run:
        if args.timeout_seconds < 1:
            print("FAIL: --timeout-seconds must be >= 1")
            return 1
        return _run_ready(args.card_id.strip(), int(args.timeout_seconds))
    cards, issues = load_cards(SKILL_DIR)
    for issue in issues:
        print(f"FAIL: {issue}")
    if issues:
        return 1
    summary = score_all_cards(SKILL_DIR)
    network = summary.get("network_status") if isinstance(summary.get("network_status"), dict) else {}
    print(
        "hypothesis cards scored={scored} artifact_missing={artifact_missing} "
        "not_enqueued={not_enqueued} floors_cleared={floors_cleared} "
        "floors_missed={floors_missed} queue_waiting={queue_waiting} "
        "promote_not_false={promote_not_false} errors={errors}".format(
            scored=summary.get("scored"),
            artifact_missing=summary.get("artifact_missing"),
            not_enqueued=summary.get("not_enqueued"),
            floors_cleared=network.get("floors_cleared"),
            floors_missed=network.get("floors_missed"),
            queue_waiting=network.get("queue_waiting"),
            promote_not_false=network.get("promote_not_false"),
            errors=len(summary.get("errors") or []),
        )
    )
    for issue in summary.get("errors") or []:
        print(f"FAIL: {issue}")
    return 1 if summary.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
