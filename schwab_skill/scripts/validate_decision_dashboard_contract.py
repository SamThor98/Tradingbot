#!/usr/bin/env python3
"""Validate decision dashboard API/UI contract artifacts."""

from __future__ import annotations

from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
DOC = SKILL_DIR / "docs" / "DECISION_DASHBOARD_KPI_SPEC.md"
MAIN = SKILL_DIR / "webapp" / "main.py"
APP_JS = SKILL_DIR / "webapp" / "static" / "app.js"
INDEX_HTML = SKILL_DIR / "webapp" / "static" / "index.html"


def _read(path: Path, errors: list[str]) -> str:
    if not path.exists():
        errors.append(f"missing file: {path}")
        return ""
    return path.read_text(encoding="utf-8")


def main() -> int:
    errors: list[str] = []
    doc = _read(DOC, errors)
    main_py = _read(MAIN, errors)
    app_js = _read(APP_JS, errors)
    html = _read(INDEX_HTML, errors)

    if doc:
        for token in ("Decision Dashboard KPI Spec", "Data Contract", "KPIs, Owners, Definitions", "Refresh Cadence"):
            if token not in doc:
                errors.append(f"DECISION_DASHBOARD_KPI_SPEC.md missing token: {token}")
    if main_py and "/api/decision-dashboard" not in main_py:
        errors.append("main.py missing /api/decision-dashboard route")
    if app_js:
        for token in ("refreshDecisionDashboard", "/api/decision-dashboard"):
            if token not in app_js:
                errors.append(f"app.js missing token: {token}")
    if html:
        for token in ("decisionDashboardCard", "decisionReliabilityState", "decisionPromotionState", "decisionLatestPromotion"):
            if token not in html:
                errors.append(f"index.html missing token: {token}")

    if errors:
        print("decision dashboard contract validation failed:")
        for err in errors:
            print(f"- {err}")
        return 1
    print("decision dashboard contract validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
