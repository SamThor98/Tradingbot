from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class GuardrailBucket:
    min_value: float
    max_value: float | None
    multiplier: float

    def matches(self, value: float) -> bool:
        if value < self.min_value:
            return False
        if self.max_value is not None and value >= self.max_value:
            return False
        return True


@dataclass(frozen=True)
class AdaptiveGuardrailPolicy:
    min_signal_score: float
    strong_signal_score: float
    extra_position_slots: int
    min_size_multiplier: float
    max_size_multiplier: float
    score_buckets: tuple[GuardrailBucket, ...]
    vcp_buckets: tuple[GuardrailBucket, ...]

    def allows_entry(self, signal_score: float) -> bool:
        return signal_score >= self.min_signal_score

    def size_multiplier(self, signal_score: float, vcp_volume_ratio: float) -> float:
        score_mult = _match_multiplier(self.score_buckets, signal_score, default=1.0)
        vcp_mult = _match_multiplier(self.vcp_buckets, vcp_volume_ratio, default=1.0)
        raw = float(score_mult) * float(vcp_mult)
        return max(self.min_size_multiplier, min(self.max_size_multiplier, raw))

    def max_positions_for_candidate(self, base_max_positions: int, signal_score: float) -> int:
        if signal_score >= self.strong_signal_score:
            return max(1, int(base_max_positions) + max(0, int(self.extra_position_slots)))
        return max(1, int(base_max_positions))


def default_adaptive_guardrail_policy() -> AdaptiveGuardrailPolicy:
    # Conservative defaults inferred from recent S&P500 all-trades analysis:
    # - Higher score buckets tended to produce better average net return.
    # - Lower VCP volume-ratio buckets (<0.70) outperformed in that sample.
    return AdaptiveGuardrailPolicy(
        min_signal_score=50.0,
        strong_signal_score=70.0,
        extra_position_slots=2,
        min_size_multiplier=0.50,
        max_size_multiplier=1.50,
        score_buckets=(
            GuardrailBucket(min_value=50.0, max_value=60.0, multiplier=0.90),
            GuardrailBucket(min_value=60.0, max_value=70.0, multiplier=1.00),
            GuardrailBucket(min_value=70.0, max_value=None, multiplier=1.20),
        ),
        vcp_buckets=(
            GuardrailBucket(min_value=float("-inf"), max_value=0.70, multiplier=1.20),
            GuardrailBucket(min_value=0.70, max_value=0.80, multiplier=1.05),
            GuardrailBucket(min_value=0.80, max_value=0.90, multiplier=0.85),
            GuardrailBucket(min_value=0.90, max_value=None, multiplier=0.75),
        ),
    )


def load_adaptive_guardrail_policy(skill_dir: Path, policy_path: str) -> AdaptiveGuardrailPolicy:
    path = Path(policy_path)
    if not path.is_absolute():
        path = skill_dir / path
    if not path.is_file():
        return default_adaptive_guardrail_policy()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default_adaptive_guardrail_policy()

    default = default_adaptive_guardrail_policy()
    score_buckets = _parse_buckets(raw.get("score_buckets"), fallback=default.score_buckets)
    vcp_buckets = _parse_buckets(raw.get("vcp_buckets"), fallback=default.vcp_buckets)
    return AdaptiveGuardrailPolicy(
        min_signal_score=_as_float(raw.get("min_signal_score"), default.min_signal_score),
        strong_signal_score=_as_float(raw.get("strong_signal_score"), default.strong_signal_score),
        extra_position_slots=max(0, _as_int(raw.get("extra_position_slots"), default.extra_position_slots)),
        min_size_multiplier=max(0.1, _as_float(raw.get("min_size_multiplier"), default.min_size_multiplier)),
        max_size_multiplier=max(0.1, _as_float(raw.get("max_size_multiplier"), default.max_size_multiplier)),
        score_buckets=score_buckets,
        vcp_buckets=vcp_buckets,
    )


def _match_multiplier(buckets: tuple[GuardrailBucket, ...], value: float, default: float) -> float:
    for bucket in buckets:
        if bucket.matches(value):
            return float(bucket.multiplier)
    return float(default)


def _parse_buckets(raw: Any, fallback: tuple[GuardrailBucket, ...]) -> tuple[GuardrailBucket, ...]:
    if not isinstance(raw, list) or not raw:
        return fallback
    out: list[GuardrailBucket] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        min_value = _as_float(item.get("min"), 0.0)
        max_raw = item.get("max")
        max_value = None if max_raw is None else _as_float(max_raw, min_value)
        mult = _as_float(item.get("multiplier"), 1.0)
        out.append(GuardrailBucket(min_value=min_value, max_value=max_value, multiplier=mult))
    return tuple(out) if out else fallback


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)
