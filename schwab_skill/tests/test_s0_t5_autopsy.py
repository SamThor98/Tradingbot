from __future__ import annotations

from research.s0_t5_autopsy import autopsy_report


def _record(
    *,
    ticker: str,
    ret: float,
    hit: bool,
    price: float,
    sma50: float,
) -> dict:
    return {
        "ticker": ticker,
        "sleeve_id": "S0",
        "source": "signal_scanner",
        "created_at": "2026-09-04T14:12:47+00:00",
        "prediction": {
            "direction": "long",
            "entry_reference_px": price,
            "levels": {"sma_50": sma50, "sma_200": price * 0.8},
            "advisory": {"p_up_10d": 0.4},
        },
        "raw_context": {"signal_score": 40, "mirofish_conviction": 10},
        "outcomes": {"5": {"return_pct": ret, "thesis_hit": hit}},
    }


def test_extended_flag_needs_both_sides_and_a_worse_mean() -> None:
    records = []
    for index in range(8):
        records.append(_record(ticker=f"E{index}", ret=-8.0, hit=False, price=120.0, sma50=100.0))
    for index in range(8):
        records.append(_record(ticker=f"B{index}", ret=-1.0, hit=False, price=105.0, sma50=100.0))
    report = autopsy_report(records)
    assert report["decision"] == "shadow_counter"
    assert report["pre_registered"]["flagged"]["n"] == 8
    assert report["pre_registered"]["other"]["n"] == 8


def test_thin_flagged_side_does_not_authorize_a_counter() -> None:
    records = [_record(ticker="E0", ret=-10.0, hit=False, price=120.0, sma50=100.0)]
    for index in range(10):
        records.append(_record(ticker=f"B{index}", ret=-1.0, hit=True, price=105.0, sma50=100.0))
    report = autopsy_report(records)
    assert report["decision"] == "no_shadow_counter"
    assert report["pre_registered"]["shadow_counter_warranted"] is False


def test_s1_rows_are_excluded() -> None:
    record = _record(ticker="PEAD", ret=5.0, hit=True, price=10.0, sma50=9.0)
    record["sleeve_id"] = "S1"
    report = autopsy_report([record])
    assert report["n"] == 0
    assert report["decision"] == "no_shadow_counter"
