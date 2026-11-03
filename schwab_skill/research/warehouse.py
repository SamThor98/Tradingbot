"""DuckDB views + Pandera (or pandas) schemas over research_store/ Parquet."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

from research.paths import datasets_dir, panels_features_dir, research_store_dir
from research.registry import FEATURE_SCHEMA_VERSION, feature_entries

LOG = logging.getLogger(__name__)


def registry_schema_fields(registry: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    fields = [
        {"name": "asof_date", "dtype": "datetime64[ns]", "nullable": False},
        {"name": "ticker", "dtype": "str", "nullable": False},
        {"name": "candidate_set_version", "dtype": "str", "nullable": True},
        {"name": "feature_schema_version", "dtype": "int", "nullable": True},
    ]
    for feat in feature_entries(registry):
        fields.append(
            {
                "name": str(feat.get("name")),
                "dtype": str(feat.get("dtype") or "float64"),
                "nullable": True,
                "enabled": bool(feat.get("enabled")),
            }
        )
    return fields


def pandera_feature_schema(registry: dict[str, Any] | None = None) -> Any:
    """Build a Pandera DataFrameSchema when pandera is installed."""
    try:
        import pandera.pandas as pa
    except Exception:
        try:
            import pandera as pa
        except Exception:
            return None
    cols: dict[str, Any] = {
        "asof_date": pa.Column(pa.DateTime, coerce=True, nullable=False),
        "ticker": pa.Column(str, nullable=False),
    }
    for feat in feature_entries(registry):
        if not feat.get("enabled"):
            continue
        name = str(feat.get("name"))
        cols[name] = pa.Column(float, nullable=True, required=False)
    return pa.DataFrameSchema(cols, strict=False)


def validate_frame(df: pd.DataFrame, registry: dict[str, Any] | None = None) -> list[str]:
    """Return schema errors. Uses Pandera when available; else column presence."""
    errors: list[str] = []
    if "asof_date" not in df.columns:
        errors.append("missing asof_date")
    if "ticker" not in df.columns:
        errors.append("missing ticker")
    schema = pandera_feature_schema(registry)
    if schema is not None:
        try:
            work = df.copy()
            if "asof_date" in work.columns:
                work["asof_date"] = pd.to_datetime(work["asof_date"], errors="coerce")
            schema.validate(work, lazy=True)
        except Exception as exc:
            errors.append(str(exc))
    return errors


def open_duckdb(skill_dir: Path | None = None, *, schema_version: int = FEATURE_SCHEMA_VERSION) -> Any:
    """Register views over versioned Parquet panels. Returns a DuckDB connection or None."""
    try:
        import duckdb
    except Exception:
        LOG.warning("duckdb not installed; warehouse views unavailable")
        return None
    root = research_store_dir(skill_dir)
    con = duckdb.connect(database=":memory:")
    feat_glob = (panels_features_dir(schema_version=schema_version, skill_dir=skill_dir) / "year=*/*.parquet").as_posix()
    labels_glob = (root / "panels" / f"schema_v{schema_version}" / "labels" / "**" / "*.parquet").as_posix()
    ds_glob = (datasets_dir(skill_dir) / "*.parquet").as_posix()

    def _view(name: str, glob: str) -> None:
        try:
            con.execute(
                f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{glob}', union_by_name=true, filename=true)"
            )
        except Exception as exc:
            LOG.info("%s view skipped: %s", name, exc)
            con.execute(f"CREATE VIEW {name} AS SELECT NULL AS asof_date, NULL AS ticker WHERE 1=0")

    _view("features", feat_glob)
    _view("labels", labels_glob)
    _view("datasets", ds_glob)
    return con
