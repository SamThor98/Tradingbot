#!/usr/bin/env python3
"""Bounded MiroFish Grok smoke: probe LLM, scan 3 names, persist last_scan, compare.

Does not place orders. Caps LLM to 2 tickers. From schwab_skill/:
  python scripts/run_mirofish_grok_smoke.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from core.env_local import parse_env_file  # noqa: E402
from core.llm_endpoint import resolve_llm_endpoint  # noqa: E402
from core.mirofish_live_compare import (  # noqa: E402
    build_mirofish_live_compare_report,
    default_backtest_skip_mirofish,
    write_mirofish_live_compare_report,
)
from core.rank_v2_mirofish_ablation import ablate_rank_v2_mirofish  # noqa: E402
from core.scan_service import run_scan  # noqa: E402
from engine_analysis import _call_llm, _load_env  # noqa: E402

TICKERS = ["AAPL", "MSFT", "NVDA"]
LOCAL_USER_ID = "local"
SQLITE_PATH = SKILL_DIR / "webapp" / "webapp.db"


def _key_class(value: str) -> str:
    text = (value or "").strip().lower()
    if not text:
        return "missing"
    if text.startswith("xai-"):
        return "xai-prefix"
    if text.startswith("sk-"):
        return "openai-sk-prefix"
    return "other-nonempty"


def _persist_last_scan(last_scan: dict[str, Any]) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from webapp.models import AppState

    engine = create_engine(
        f"sqlite:///{SQLITE_PATH.resolve().as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        row = db.query(AppState).filter(AppState.user_id == LOCAL_USER_ID, AppState.key == "last_scan").first()
        if not row:
            db.add(AppState(user_id=LOCAL_USER_ID, key="last_scan", value_json=last_scan))
        else:
            row.value_json = last_scan
        db.commit()
    finally:
        db.close()


def main() -> int:
    os.environ.setdefault("MIROFISH_MAX_LLM_TICKERS_PER_SCAN", "2")
    env_file = parse_env_file(SKILL_DIR / ".env")
    endpoint = resolve_llm_endpoint(_load_env())
    print(
        "routing provider={p} model={m} key_class={k} weighting={w}".format(
            p=endpoint.provider,
            m=endpoint.model,
            k=_key_class(endpoint.api_key),
            w=env_file.get("MIROFISH_WEIGHTING_MODE", ""),
        )
    )

    probe = _call_llm(
        'Return exactly {"ok":true}',
        "Return one JSON object only.",
        _load_env(),
    )
    print("probe_empty", not bool((probe or "").strip()), "probe_len", len(probe or ""))

    scan_out = run_scan(
        skill_dir=SKILL_DIR,
        watchlist_override=TICKERS,
        env_overrides={"MIROFISH_MAX_LLM_TICKERS_PER_SCAN": "2", "SIGNAL_TOP_N": "0"},
    )
    diagnostics = scan_out.diagnostics or {}
    signals = scan_out.signals or []
    miro = diagnostics.get("mirofish_llm") if isinstance(diagnostics.get("mirofish_llm"), dict) else {}
    print(
        "scan tickers={n} signals={s} blocked={b} dq={dq} mirofish_llm={m}".format(
            n=len(TICKERS),
            s=len(signals),
            b=diagnostics.get("scan_blocked_reason"),
            dq=diagnostics.get("data_quality"),
            m=miro,
        )
    )
    for sig in signals[:8]:
        print(
            "signal {t} conv={c} model={m} provider={p}".format(
                t=sig.get("ticker"),
                c=sig.get("mirofish_conviction"),
                m=sig.get("mirofish_llm_model"),
                p=sig.get("mirofish_llm_provider"),
            )
        )

    now_iso = datetime.now(timezone.utc).isoformat()
    last_scan = {
        "at": now_iso,
        "signals_found": len(signals),
        "signals": signals,
        "shortlist_signals": scan_out.shortlist_signals or [],
        "diagnostics": diagnostics,
        "label": "mirofish_grok_smoke",
    }
    _persist_last_scan(last_scan)
    print("persisted last_scan", SQLITE_PATH)

    compare = build_mirofish_live_compare_report(
        last_scan,
        backtest_skip_mirofish=default_backtest_skip_mirofish(),
    )
    path = write_mirofish_live_compare_report(compare, SKILL_DIR)
    print("fidelity", compare.get("verdict"), compare.get("fidelity_gap"), "wrote", path)

    ablation = ablate_rank_v2_mirofish(signals, top_n=3, skill_dir=SKILL_DIR)
    art = SKILL_DIR / "validation_artifacts" / "rank_v2_mirofish_ablation.json"
    art.parent.mkdir(parents=True, exist_ok=True)
    art.write_text(json.dumps(ablation, indent=2, default=str) + "\n", encoding="utf-8")
    overlap = ablation.get("top_n_overlap") or {}
    print(
        "ablation n={n} spearman={sp} enter={e} leave={l} wrote {p}".format(
            n=ablation.get("n_signals"),
            sp=ablation.get("spearman"),
            e=overlap.get("enter_count"),
            l=overlap.get("leave_count"),
            p=art,
        )
    )
    return 0 if not diagnostics.get("scan_blocked") else 1


if __name__ == "__main__":
    raise SystemExit(main())
