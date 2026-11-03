from __future__ import annotations

import json

from core.rank_v2_mirofish_ablation import (
    FALLBACK_TOP_N,
    ablate_rank_v2_mirofish,
    parse_signals_payload,
    resolve_ablation_top_n,
)
from core.scoring_rank_v2 import compute_rank_score_v2
from scripts.analyze_rank_v2_mirofish_ablation import main as ablation_cli_main


def _row(
    ticker: str,
    *,
    signal_score: float,
    pts_volume: float = 10.0,
    pts_mirofish: float | None = 0.0,
    pts_52w: float = 20.0,
    nested: bool = False,
) -> dict:
    pack: dict = {"ticker": ticker, "signal_score": signal_score}
    comps = {"pts_volume": pts_volume, "pts_52w": pts_52w}
    if pts_mirofish is None:
        if nested:
            pack["score_components"] = comps
        else:
            pack["pts_volume"] = pts_volume
            pack["pts_52w"] = pts_52w
        return pack
    if nested:
        pack["score_components"] = {**comps, "pts_mirofish": pts_mirofish}
    else:
        pack["pts_volume"] = pts_volume
        pack["pts_52w"] = pts_52w
        pack["pts_mirofish"] = pts_mirofish
    return pack


WEIGHTS = {
    "signal_weight": 0.35,
    "volume_weight": 0.50,
    "mirofish_weight": 0.15,
    "exclude_52w": True,
}


def _synthetic_book() -> list[dict]:
    """Six names where high pts_mirofish names leave the live top-3 when zeroed."""
    return [
        _row("AAA", signal_score=80.0, pts_mirofish=0.0),
        _row("BBB", signal_score=78.0, pts_mirofish=15.0),
        _row("CCC", signal_score=79.0, pts_mirofish=0.0),
        _row("DDD", signal_score=70.0, pts_mirofish=0.0),
        _row("EEE", signal_score=60.0, pts_mirofish=15.0),
        _row("FFF", signal_score=50.0, pts_mirofish=None),
    ]


def test_parse_signals_payload_shapes() -> None:
    rows = [{"ticker": "AAA", "signal_score": 1}]
    assert parse_signals_payload(rows) == rows
    assert parse_signals_payload({"signals": rows}) == rows
    assert parse_signals_payload({"last_scan": {"signals": rows}}) == rows
    assert parse_signals_payload({"data": {"signals": rows}}) == rows
    assert parse_signals_payload("nope") == []


def test_resolve_top_n_uses_signal_top_n_or_ten(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("SIGNAL_TOP_N", raising=False)
    import config

    config.clear_env_cache()
    assert resolve_ablation_top_n(None, skill_dir=tmp_path) == 5
    monkeypatch.setenv("SIGNAL_TOP_N", "0")
    config.clear_env_cache()
    assert resolve_ablation_top_n(None, skill_dir=tmp_path) == FALLBACK_TOP_N
    monkeypatch.setenv("SIGNAL_TOP_N", "8")
    config.clear_env_cache()
    assert resolve_ablation_top_n(None, skill_dir=tmp_path) == 8
    assert resolve_ablation_top_n(3, skill_dir=tmp_path) == 3


def test_zero_mirofish_book_is_identical() -> None:
    book = [
        _row("AAA", signal_score=80.0, pts_mirofish=0.0),
        _row("BBB", signal_score=70.0, pts_mirofish=0.0),
        _row("CCC", signal_score=60.0, pts_mirofish=0.0),
        _row("DDD", signal_score=50.0, pts_mirofish=0.0),
    ]
    report = ablate_rank_v2_mirofish(book, top_n=3, **WEIGHTS)
    assert report["ok"] is True
    assert report["changes_live_rank"] is False
    assert report["spearman"] == 1.0
    assert report["mean_abs_rank_delta"] == 0.0
    assert report["top_n_overlap"]["enter_count"] == 0
    assert report["top_n_overlap"]["leave_count"] == 0
    assert report["mirofish_coverage"]["fraction_missing_or_zero"] == 1.0


def test_high_mirofish_names_leave_top_n() -> None:
    report = ablate_rank_v2_mirofish(_synthetic_book(), top_n=3, **WEIGHTS)
    overlap = report["top_n_overlap"]
    assert "EEE" in overlap["leave"]
    assert "CCC" in overlap["enter"]
    assert overlap["leave_count"] >= 1
    assert overlap["enter_count"] == overlap["leave_count"]
    assert report["mean_abs_rank_delta"] is not None
    assert report["mean_abs_rank_delta"] > 0.0
    assert report["spearman"] is not None
    assert report["spearman"] < 1.0
    coverage = report["mirofish_coverage"]
    assert coverage["n_present"] == 2
    assert coverage["n_zero"] == 3
    assert coverage["n_missing"] == 1
    assert coverage["fraction_missing_or_zero"] == round(4 / 6, 4)


def test_nested_score_components_match_top_level() -> None:
    nested = [
        _row("AAA", signal_score=80.0, pts_mirofish=12.0, nested=True),
        _row("BBB", signal_score=80.0, pts_mirofish=0.0, nested=True),
        _row("CCC", signal_score=70.0, pts_mirofish=0.0, nested=True),
    ]
    flat = [
        _row("AAA", signal_score=80.0, pts_mirofish=12.0),
        _row("BBB", signal_score=80.0, pts_mirofish=0.0),
        _row("CCC", signal_score=70.0, pts_mirofish=0.0),
    ]
    a = ablate_rank_v2_mirofish(nested, top_n=2, **WEIGHTS)
    b = ablate_rank_v2_mirofish(flat, top_n=2, **WEIGHTS)
    assert a["top_n_overlap"]["baseline"] == b["top_n_overlap"]["baseline"]
    assert a["rows"][0]["rank_score_v2"] == b["rows"][0]["rank_score_v2"]


def test_ablated_score_matches_compute_rank_score_v2() -> None:
    row = _row("ZZZ", signal_score=80.0, pts_volume=10.0, pts_mirofish=9.0, pts_52w=20.0)
    report = ablate_rank_v2_mirofish([row, _row("YYY", signal_score=70.0), _row("XXX", signal_score=60.0)], top_n=2, **WEIGHTS)
    scored = next(item for item in report["rows"] if item["ticker"] == "ZZZ")
    expected_live = compute_rank_score_v2(
        signal_score=80.0,
        pts_volume=10.0,
        pts_mirofish=9.0,
        pts_52w=20.0,
        **WEIGHTS,
    )
    expected_zero = compute_rank_score_v2(
        signal_score=80.0,
        pts_volume=10.0,
        pts_mirofish=0.0,
        pts_52w=20.0,
        **WEIGHTS,
    )
    assert scored["rank_score_v2"] == expected_live
    assert scored["rank_score_v2_mirofish_zero"] == expected_zero


def test_empty_book_is_ok() -> None:
    report = ablate_rank_v2_mirofish([], top_n=10, **WEIGHTS)
    assert report["ok"] is True
    assert report["n_signals"] == 0
    assert report["spearman"] is None
    assert report["top_n_overlap"]["enter_count"] == 0
    assert report["mirofish_coverage"]["fraction_missing_or_zero"] is None


def test_cli_signals_json_writes_artifact(tmp_path, capsys) -> None:
    fixture = tmp_path / "signals.json"
    fixture.write_text(json.dumps(_synthetic_book()), encoding="utf-8")
    rc = ablation_cli_main(["--signals-json", str(fixture), "--top-n", "3"])
    assert rc == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["n_signals"] == 6
    assert payload["source"] == "signals_json"
    assert payload["changes_live_rank"] is False


def test_cli_skips_cleanly_when_sqlite_missing(tmp_path, capsys) -> None:
    missing = tmp_path / "nope.db"
    rc = ablation_cli_main(["--sqlite", str(missing)])
    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("SKIP:")
