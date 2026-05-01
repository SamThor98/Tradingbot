#!/usr/bin/env python3
"""Validate SLO mapping coverage against implemented metric names."""

from __future__ import annotations

from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parent.parent
DOC = SKILL_DIR / "docs" / "SLO_METRIC_MAPPING.md"
TASKS = SKILL_DIR / "webapp" / "tasks.py"
MAIN_SAAS = SKILL_DIR / "webapp" / "main_saas.py"

REQUIRED_DOC_TOKENS = [
    "http_requests_total",
    "http_5xx_total",
    "scan_tasks_total",
    "scan_tasks_failed_total",
    "order_tasks_total",
    "order_tasks_failed_total",
    "scan_task_duration_seconds",
    "order_task_duration_seconds",
]

REQUIRED_CODE_TOKENS = [
    "http_requests_total",
    "http_5xx_total",
    "scan_tasks_total",
    "scan_tasks_failed_total",
    "order_tasks_total",
    "order_tasks_failed_total",
    "scan_task_duration",
    "order_task_duration",
]


def main() -> int:
    failures: list[str] = []
    if not DOC.exists():
        failures.append("missing_docs/SLO_METRIC_MAPPING.md")
        doc_text = ""
    else:
        doc_text = DOC.read_text(encoding="utf-8")
    code_text = ""
    for p in (TASKS, MAIN_SAAS):
        if p.exists():
            code_text += p.read_text(encoding="utf-8")
        else:
            failures.append(f"missing_code_file:{p}")

    for token in REQUIRED_DOC_TOKENS:
        if token not in doc_text:
            failures.append(f"missing_doc_metric_token:{token}")
    for token in REQUIRED_CODE_TOKENS:
        if token not in code_text:
            failures.append(f"missing_code_metric_token:{token}")

    if failures:
        print("FAIL: SLO mapping validation failed")
        for item in failures:
            print(f"- {item}")
        return 1
    print("PASS: SLO mapping covers required metrics and code emitters")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
