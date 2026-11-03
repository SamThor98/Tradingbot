"""Research warehouse (DuckDB views + schema) and ops/research name alignment."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from research.materialize import write_feature_panel  # noqa: E402
from research.ops_alignment import RAW_FEATURES_JSON_OVERFLOW, alignment_errors  # noqa: E402
from research.registry import enabled_feature_names, load_feature_registry  # noqa: E402
from research.warehouse import open_duckdb, pandera_feature_schema, validate_frame  # noqa: E402


def test_ops_research_alignment_passes_for_current_registry() -> None:
    errs = alignment_errors()
    assert errs == [], errs


def test_new_enabled_feature_under_different_ops_name_fails() -> None:
    reg = load_feature_registry(reload=True)
    fake = dict(reg)
    fake["features"] = list(reg["features"]) + [
        {
            "name": "brand_new_pit_field",
            "dtype": "float64",
            "category": "liquidity",
            "description": "ci ratchet",
            "data_source": "ohlcv",
            "pit_safe": True,
            "enabled": True,
            "ablation_group": "test",
            "status": "new",
            "coverage_risk": "low",
        }
    ]
    errs = alignment_errors(fake)
    assert any("brand_new_pit_field" in e for e in errs)


def test_overflow_covers_enabled_names_or_sql() -> None:
    enabled = enabled_feature_names(load_feature_registry(reload=True))
    errs = alignment_errors()
    assert not errs
    # Deferred/disabled names must not sneak into overflow-required enabled set
    assert "market_cap" not in enabled
    assert "market_cap" not in RAW_FEATURES_JSON_OVERFLOW


def test_warehouse_views_and_schema(tmp_path: Path) -> None:
    frame = pd.DataFrame(
        {
            "asof_date": ["2016-06-01"],
            "ticker": ["AAPL"],
            "candidate_set_version": ["stage2_pass_v1"],
            "feature_schema_version": [1],
            "dist_sma50_pct": [0.05],
            "signal_score": [70.0],
        }
    )
    written = write_feature_panel(frame, skill_dir=tmp_path, schema_version=1, ticker="AAPL")
    assert written
    errs = validate_frame(frame)
    # Missing many enabled columns is allowed (required=False / overflow)
    # asof + ticker must pass
    assert not any("missing asof_date" in e or "missing ticker" in e for e in errs)

    duckdb = pytest.importorskip("duckdb")
    con = open_duckdb(tmp_path)
    assert con is not None
    n = con.execute("SELECT COUNT(*) FROM features").fetchone()[0]
    assert int(n) >= 1
    schema = pandera_feature_schema()
    if schema is not None:
        # Partial frame: pandera required=False on features
        schema.validate(frame.assign(asof_date=pd.to_datetime(frame["asof_date"])), lazy=True)
    _ = duckdb
