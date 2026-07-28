"""Unit tests for Finnhub company-name enrich (fail-soft, never invent)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from core.company_name_lookup import attach_company_names, resolve_company_names


def test_attach_omits_unresolved_and_never_copies_ticker(tmp_path: Path) -> None:
    rows: list[dict[str, Any]] = [
        {"ticker": "AAPL", "price": 100},
        {"ticker": "ZZZZ", "price": 1},
    ]
    with patch(
        "core.company_name_lookup.resolve_company_names",
        return_value={"AAPL": "Apple Inc"},
    ):
        attach_company_names(rows, skill_dir=tmp_path)

    assert rows[0]["company_name"] == "Apple Inc"
    assert "company_name" not in rows[1]


def test_resolve_uses_cache_and_skips_fetch(tmp_path: Path) -> None:
    cache = {
        "AAPL": {"name": "Apple Inc", "stored_at": 9_999_999_999},
    }
    cache_path = tmp_path / ".company_name_cache.json"
    import json

    cache_path.write_text(json.dumps(cache), encoding="utf-8")

    with (
        patch("config.get_finnhub_api_key", return_value="tok"),
        patch("config.get_finnhub_timeout_sec", return_value=5.0),
        patch("core.company_name_lookup._fetch_profile_name") as fetch,
    ):
        out = resolve_company_names(["AAPL"], skill_dir=tmp_path)

    assert out == {"AAPL": "Apple Inc"}
    fetch.assert_not_called()


def test_resolve_fetches_and_caches(tmp_path: Path) -> None:
    with (
        patch("config.get_finnhub_api_key", return_value="tok"),
        patch("config.get_finnhub_timeout_sec", return_value=5.0),
        patch(
            "core.company_name_lookup._fetch_profile_name",
            return_value="NVIDIA Corp",
        ) as fetch,
    ):
        out = resolve_company_names(["NVDA"], skill_dir=tmp_path)

    assert out == {"NVDA": "NVIDIA Corp"}
    fetch.assert_called_once()
    cache_file = tmp_path / ".company_name_cache.json"
    assert cache_file.exists()
    assert "NVIDIA Corp" in cache_file.read_text(encoding="utf-8")


def test_resolve_no_key_returns_empty(tmp_path: Path) -> None:
    with (
        patch("config.get_finnhub_api_key", return_value=""),
        patch("config.get_finnhub_timeout_sec", return_value=5.0),
        patch("core.company_name_lookup._fetch_profile_name") as fetch,
    ):
        out = resolve_company_names(["AAPL"], skill_dir=tmp_path)

    assert out == {}
    fetch.assert_not_called()
