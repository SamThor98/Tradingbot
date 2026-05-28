"""OptionsProvider — Schwab /chains JSON -> OptionsIntel.

``normalize_chain`` is a pure transform of the raw chain payload so it is
testable offline. Defensive against Schwab's nested ``callExpDateMap`` /
``putExpDateMap`` structure and tolerant of missing fields.
"""

from __future__ import annotations

from typing import Any

from core.contracts.symbol import OptionsIntel


def _f(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _underlying_price(chain: dict[str, Any]) -> float | None:
    px = _f(chain.get("underlyingPrice"))
    if px is not None:
        return px
    under = chain.get("underlying")
    if isinstance(under, dict):
        return _f(under.get("last") or under.get("mark") or under.get("close"))
    return None


def _nearest_atm(exp_map: dict[str, Any], underlying: float | None) -> tuple[str | None, dict[str, Any] | None]:
    """Return (expiry_key, atm_contract) for the nearest expiry's ATM strike."""
    if not isinstance(exp_map, dict) or not exp_map:
        return None, None
    expiry_key = sorted(exp_map.keys())[0]
    strikes = exp_map.get(expiry_key) or {}
    if not isinstance(strikes, dict) or not strikes:
        return expiry_key, None

    def _strike_val(k: str) -> float:
        try:
            return float(k)
        except (TypeError, ValueError):
            return float("inf")

    if underlying is not None:
        best_key = min(strikes.keys(), key=lambda k: abs(_strike_val(k) - underlying))
    else:
        best_key = sorted(strikes.keys(), key=_strike_val)[len(strikes) // 2]
    contracts = strikes.get(best_key) or []
    contract = contracts[0] if isinstance(contracts, list) and contracts else None
    return expiry_key, contract if isinstance(contract, dict) else None


def _mark(contract: dict[str, Any] | None) -> float | None:
    if not contract:
        return None
    return _f(contract.get("mark") or contract.get("last") or contract.get("closePrice"))


def _iv(contract: dict[str, Any] | None) -> float | None:
    if not contract:
        return None
    return _f(contract.get("volatility"))


class OptionsProvider:
    domain = "options"

    @staticmethod
    def normalize_chain(chain: dict[str, Any] | None) -> OptionsIntel:
        chain = chain or {}
        underlying = _underlying_price(chain)
        call_map = chain.get("callExpDateMap") or {}
        put_map = chain.get("putExpDateMap") or {}

        expiry, atm_call = _nearest_atm(call_map, underlying)
        _, atm_put = _nearest_atm(put_map, underlying)

        call_iv = _iv(atm_call)
        put_iv = _iv(atm_put)
        # Schwab quotes volatility as a percent (e.g. 28.5); normalize to fraction.
        atm_iv = None
        if call_iv is not None and put_iv is not None:
            atm_iv = round(((call_iv + put_iv) / 2.0) / 100.0, 4)
        elif call_iv is not None:
            atm_iv = round(call_iv / 100.0, 4)
        elif put_iv is not None:
            atm_iv = round(put_iv / 100.0, 4)

        skew = None
        if call_iv is not None and put_iv is not None:
            skew = round((put_iv - call_iv) / 100.0, 4)

        expected_move_pct = None
        call_mark, put_mark = _mark(atm_call), _mark(atm_put)
        if underlying and call_mark is not None and put_mark is not None and underlying > 0:
            expected_move_pct = round((call_mark + put_mark) / underlying * 100.0, 3)

        return OptionsIntel(
            atm_iv=atm_iv,
            put_call_skew=skew,
            expected_move_pct=expected_move_pct,
            nearest_expiry=(expiry.split(":")[0] if isinstance(expiry, str) else None),
        )
