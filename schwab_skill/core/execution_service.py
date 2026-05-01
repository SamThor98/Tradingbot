from __future__ import annotations

from pathlib import Path
from typing import Any

from execution import place_order


def submit_order(
    *,
    ticker: str,
    qty: int,
    side: str,
    order_type: str,
    price_hint: float | None = None,
    mirofish_conviction: float | None = None,
    sector_etf: str | None = None,
    skill_dir: Path,
) -> dict[str, Any] | str:
    return place_order(
        ticker=ticker,
        qty=qty,
        side=side,
        order_type=order_type,
        price_hint=price_hint,
        mirofish_conviction=mirofish_conviction,
        sector_etf=sector_etf,
        skill_dir=skill_dir,
    )
