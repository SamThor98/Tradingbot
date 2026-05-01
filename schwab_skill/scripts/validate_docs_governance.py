#!/usr/bin/env python3
"""Validate documentation/skill governance invariants.

Checks:
1. Wiki frontmatter completeness (source, created, updated, tags).
2. Wiki broken links for [[wikilinks]].
3. Wiki orphan pages (not linked from wiki/index.md).
4. Rule path consistency for project-conventions webapp paths.
5. Active skills catalog references existing files.
6. Canonical/extension Schwab skill identity split (no duplicate names).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = ROOT / "schwab_skill"
WIKI_DIR = ROOT / "wiki"
RULES_DIR = ROOT / ".cursor" / "rules"
SKILLS_DIR = ROOT / ".cursor" / "skills"
ACTIVE_SKILLS = SKILLS_DIR / "ACTIVE_SKILLS.md"
CANONICAL_SCHWAB_SKILL = SKILLS_DIR / "schwab-api" / "SKILL.md"
OPENCLAW_SCHWAB_SKILL = SKILL_DIR / "SKILL.md"

WIKILINK_RE = re.compile(r"\[\[([a-z0-9\-_/]+)\]\]")
CODE_LINK_RE = re.compile(r"`([^`]+\.md)`")
NAME_RE = re.compile(r"^name:\s*(.+)\s*$", re.MULTILINE)


@dataclass
class Finding:
    level: str
    message: str


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _frontmatter(md: str) -> dict[str, str]:
    if not md.startswith("---\n"):
        return {}
    parts = md.split("\n---\n", 1)
    if len(parts) != 2:
        return {}
    out: dict[str, str] = {}
    for line in parts[0].splitlines()[1:]:
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        out[k.strip()] = v.strip()
    return out


def _wiki_pages() -> list[Path]:
    return sorted(p for p in WIKI_DIR.glob("*.md") if p.name != "README.md")


def check_wiki_frontmatter(findings: list[Finding]) -> None:
    required = {"source", "created", "updated", "tags"}
    for page in _wiki_pages():
        fm = _frontmatter(_read(page))
        missing = sorted(required - set(fm.keys()))
        if missing:
            findings.append(
                Finding(
                    "ERROR",
                    f"{page.relative_to(ROOT)} missing frontmatter keys: {', '.join(missing)}",
                )
            )


def check_wikilinks(findings: list[Finding]) -> None:
    pages = {p.stem for p in _wiki_pages()}
    for page in _wiki_pages():
        for target in WIKILINK_RE.findall(_read(page)):
            if target not in pages:
                findings.append(
                    Finding(
                        "ERROR",
                        f"{page.relative_to(ROOT)} has broken wikilink [[{target}]]",
                    )
                )


def check_orphans(findings: list[Finding]) -> None:
    index_path = WIKI_DIR / "index.md"
    if not index_path.exists():
        findings.append(Finding("ERROR", "wiki/index.md is missing"))
        return
    index_text = _read(index_path)
    linked = set(WIKILINK_RE.findall(index_text))
    for page in _wiki_pages():
        if page.stem == "index":
            continue
        if page.stem not in linked:
            findings.append(
                Finding("WARN", f"{page.relative_to(ROOT)} not linked from wiki/index.md")
            )


def check_rule_path_consistency(findings: list[Finding]) -> None:
    project_rules = RULES_DIR / "project-conventions.mdc"
    if not project_rules.exists():
        findings.append(Finding("ERROR", ".cursor/rules/project-conventions.mdc missing"))
        return
    text = _read(project_rules)
    expected = ["schwab_skill/webapp/main.py", "schwab_skill/webapp/main_saas.py"]
    for marker in expected:
        if marker not in text:
            findings.append(
                Finding(
                    "ERROR",
                    f"{project_rules.relative_to(ROOT)} missing expected path marker: {marker}",
                )
            )


def check_active_skills(findings: list[Finding]) -> None:
    if not ACTIVE_SKILLS.exists():
        findings.append(Finding("ERROR", ".cursor/skills/ACTIVE_SKILLS.md missing"))
        return
    text = _read(ACTIVE_SKILLS)
    skill_links = CODE_LINK_RE.findall(text)
    if not skill_links:
        findings.append(Finding("ERROR", "ACTIVE_SKILLS.md contains no skill path references"))
    for rel in skill_links:
        p = ROOT / rel
        if not p.exists():
            findings.append(Finding("ERROR", f"ACTIVE_SKILLS reference missing file: {rel}"))


def _skill_name(path: Path) -> str | None:
    if not path.exists():
        return None
    match = NAME_RE.search(_read(path))
    if not match:
        return None
    return match.group(1).strip()


def check_skill_identity_split(findings: list[Finding]) -> None:
    canonical_name = _skill_name(CANONICAL_SCHWAB_SKILL)
    extension_name = _skill_name(OPENCLAW_SCHWAB_SKILL)
    if not canonical_name or not extension_name:
        findings.append(Finding("ERROR", "Unable to read Schwab skill names from frontmatter"))
        return
    if canonical_name == extension_name:
        findings.append(
            Finding(
                "ERROR",
                "Canonical and OpenClaw Schwab skill names must differ to avoid routing ambiguity",
            )
        )


def main() -> int:
    findings: list[Finding] = []
    check_wiki_frontmatter(findings)
    check_wikilinks(findings)
    check_orphans(findings)
    check_rule_path_consistency(findings)
    check_active_skills(findings)
    check_skill_identity_split(findings)

    errors = [f for f in findings if f.level == "ERROR"]
    warns = [f for f in findings if f.level != "ERROR"]
    for f in findings:
        print(f"{f.level}: {f.message}")
    print(f"validate_docs_governance: {len(errors)} errors, {len(warns)} warnings")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
