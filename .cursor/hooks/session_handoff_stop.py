"""Stop hook: ask for one handoff note after schwab_skill Python changes."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

FOLLOWUP = (
    "Uncommitted Python under schwab_skill changed and this change set has no "
    "handoff yet. Use the session-handoff skill to write one note in "
    ".cursor/handoffs/. Record only validators this session actually ran. If "
    "the diff touches the scanner, backtest, scoring, exits, or quality gates, "
    "also run the signal-edge-review skill as a read-only pass. Do not place "
    "orders or change plugin modes."
)

STAMP = Path(".cursor/handoffs/.last-prompted")
ROOT_MARKERS = ("schwab_skill/",)


def relevant_paths(porcelain: str) -> list[str]:
    """Return repo-relative Python paths the handoff should cover."""
    found: list[str] = []
    for line in porcelain.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip().replace("\\", "/")
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        if path.startswith(ROOT_MARKERS) and path.endswith(".py"):
            found.append(path)
    return sorted(set(found))


def decide(payload: dict, porcelain: str, stamp_path: Path) -> dict:
    """Return a stop-hook response. Empty means let the agent stop."""
    if payload.get("status") != "completed":
        return {}
    try:
        loop_count = int(payload.get("loop_count") or 0)
    except (TypeError, ValueError):
        loop_count = 0
    if loop_count > 0:
        return {}
    paths = relevant_paths(porcelain)
    if not paths:
        return {}
    digest = hashlib.sha256("\n".join(paths).encode("utf-8")).hexdigest()
    if stamp_path.is_file() and stamp_path.read_text(encoding="utf-8").strip() == digest:
        return {}
    stamp_path.parent.mkdir(parents=True, exist_ok=True)
    stamp_path.write_text(digest + "\n", encoding="utf-8")
    return {"followup_message": FOLLOWUP}


def git_porcelain() -> str:
    try:
        completed = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        print("{}")
        return
    if not isinstance(payload, dict):
        print("{}")
        return
    print(json.dumps(decide(payload, git_porcelain(), STAMP)))


def _self_test() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        stamp = Path(tmp) / ".last-prompted"
        dirty = " M schwab_skill/signal_scanner.py\n?? wiki/index.md\n"
        assert relevant_paths(dirty) == ["schwab_skill/signal_scanner.py"]
        assert relevant_paths(" M schwab_skill/README.md\n") == []
        assert decide({"status": "aborted", "loop_count": 0}, dirty, stamp) == {}
        assert decide({"status": "completed", "loop_count": 1}, dirty, stamp) == {}
        first = decide({"status": "completed", "loop_count": 0}, dirty, stamp)
        assert first.get("followup_message") == FOLLOWUP
        second = decide({"status": "completed", "loop_count": 0}, dirty, stamp)
        assert second == {}
        renamed = "R  schwab_skill/old.py -> schwab_skill/new.py\n"
        assert relevant_paths(renamed) == ["schwab_skill/new.py"]
    print("session_handoff_stop: ok")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        _self_test()
    else:
        main()
