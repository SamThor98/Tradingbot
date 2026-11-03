from __future__ import annotations

import pytest

from config import (
    get_counterfactual_logging_enabled,
    get_meta_policy_mode,
    get_meta_policy_size_mult_max,
    get_meta_policy_size_mult_min,
    get_mirofish_abstain_disagreement,
    get_mirofish_abstain_mode,
    get_mirofish_weighting_mode,
    get_uncertainty_mode,
)


@pytest.fixture(autouse=True)
def _clean_mode_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Process env wins over the tmp_path .env these tests write, so drop the
    # keys under test in case the developer's shell exports them. Import-time
    # `.env` promotion is handled globally in tests/conftest.py.
    for key in (
        "MIROFISH_WEIGHTING_MODE",
        "MIROFISH_ABSTAIN_MODE",
        "MIROFISH_ABSTAIN_DISAGREEMENT",
        "META_POLICY_MODE",
        "UNCERTAINTY_MODE",
        "COUNTERFACTUAL_LOGGING_ENABLED",
    ):
        monkeypatch.delenv(key, raising=False)


def test_agent_intelligence_config_defaults(tmp_path) -> None:
    assert get_mirofish_weighting_mode(tmp_path) == "off"
    assert get_mirofish_abstain_mode(tmp_path) == "shadow"
    assert get_mirofish_abstain_disagreement(tmp_path) == 0.65
    assert get_meta_policy_mode(tmp_path) == "shadow"
    assert get_uncertainty_mode(tmp_path) == "shadow"
    assert get_counterfactual_logging_enabled(tmp_path) is True
    assert float(get_meta_policy_size_mult_min(tmp_path)) <= 1.0
    assert float(get_meta_policy_size_mult_max(tmp_path)) >= 1.0


def test_agent_intelligence_mode_parsing(tmp_path) -> None:
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "MIROFISH_WEIGHTING_MODE=live",
                "META_POLICY_MODE=shadow",
                "UNCERTAINTY_MODE=live",
                "COUNTERFACTUAL_LOGGING_ENABLED=true",
            ]
        )
    )
    assert get_mirofish_weighting_mode(tmp_path) == "live"
    assert get_meta_policy_mode(tmp_path) == "shadow"
    assert get_uncertainty_mode(tmp_path) == "live"
    assert get_counterfactual_logging_enabled(tmp_path) is True
