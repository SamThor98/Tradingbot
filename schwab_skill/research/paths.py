"""Paths for the research Parquet warehouse."""

from __future__ import annotations

from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
RESEARCH_PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_REGISTRY_PATH = RESEARCH_PACKAGE_DIR / "feature_registry.json"
RESEARCH_STORE_DIR = SKILL_DIR / "research_store"


def research_store_dir(skill_dir: Path | None = None) -> Path:
    root = Path(skill_dir) if skill_dir is not None else SKILL_DIR
    return root / "research_store"


def panels_features_dir(
    *,
    schema_version: int,
    skill_dir: Path | None = None,
) -> Path:
    return research_store_dir(skill_dir) / "panels" / f"schema_v{schema_version}" / "features"


def datasets_dir(skill_dir: Path | None = None) -> Path:
    return research_store_dir(skill_dir) / "datasets"


def models_dir(skill_dir: Path | None = None) -> Path:
    return research_store_dir(skill_dir) / "models"


def ensure_research_store_layout(skill_dir: Path | None = None, *, schema_version: int = 1) -> Path:
    """Create research_store directory tree; return store root."""
    root = research_store_dir(skill_dir)
    for path in (
        panels_features_dir(schema_version=schema_version, skill_dir=skill_dir),
        root / "panels" / f"schema_v{schema_version}" / "labels",
        datasets_dir(skill_dir),
        models_dir(skill_dir),
    ):
        path.mkdir(parents=True, exist_ok=True)
    return root
