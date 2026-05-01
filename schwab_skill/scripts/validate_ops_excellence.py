#!/usr/bin/env python3
"""Validate operational excellence governance artifacts."""

from __future__ import annotations

from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SKILL_DIR.parent
WIKI_DIR = REPO_ROOT / "wiki"
DOCS_DIR = SKILL_DIR / "docs"


def _read_required(path: Path, errors: list[str]) -> str:
    if not path.exists():
        errors.append(f"missing file: {path}")
        return ""
    return path.read_text(encoding="utf-8")


def main() -> int:
    errors: list[str] = []

    ops_loop = _read_required(WIKI_DIR / "ops-excellence-loop.md", errors)
    postmortem_sla = _read_required(DOCS_DIR / "POSTMORTEM_SLA.md", errors)
    postmortem_template = _read_required(DOCS_DIR / "POSTMORTEM_TEMPLATE.md", errors)

    if ops_loop:
        for token in (
            "Cadence Calendar",
            "Incident Drill Checklist",
            "Restore Drill Checklist",
            "Pass Criteria",
            "Fail Criteria",
            "Evidence Artifacts",
        ):
            if token not in ops_loop:
                errors.append(f"ops-excellence-loop.md missing section/token: {token}")

    if postmortem_sla:
        for token in ("P0", "P1", "P2", "Closure Targets", "Escalation"):
            if token not in postmortem_sla:
                errors.append(f"POSTMORTEM_SLA.md missing section/token: {token}")

    if postmortem_template:
        for token in ("Timeline", "Root Cause", "Action Items", "Follow-through Check"):
            if token not in postmortem_template:
                errors.append(f"POSTMORTEM_TEMPLATE.md missing section/token: {token}")

    if errors:
        print("ops excellence validation failed:")
        for err in errors:
            print(f"- {err}")
        return 1

    print("ops excellence validation passed")
    return 0


if __name__ == "__main__":
  raise SystemExit(main())
