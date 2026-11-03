#!/usr/bin/env python3
"""Validate pre-registered hypothesis cards. Does not run backtests."""

from __future__ import annotations

import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from core.hypothesis_card import load_cards  # noqa: E402


def main() -> int:
    cards, issues = load_cards(SKILL_DIR)
    for issue in issues:
        print(f"FAIL: {issue}")
    if issues:
        print(f"validate_hypothesis_cards: {len(issues)} errors, {len(cards)} valid")
        return 1
    for card in cards:
        print(f"OK {card.card_id} {card.kind} {card.status} {card.fingerprint}")
    print(f"validate_hypothesis_cards: {len(cards)} cards")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
