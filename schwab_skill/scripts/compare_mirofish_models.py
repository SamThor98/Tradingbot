#!/usr/bin/env python3
"""Compare MiroFish convictions across LLM models on the same seed fingerprint.

Reads `.mirofish_model_log.jsonl` written during shadow scans. Does not call
the LLM. Pairing requires the same ticker + seed_fingerprint under two models.

Usage (from schwab_skill):
  python scripts/compare_mirofish_models.py
  python scripts/compare_mirofish_models.py --write-artifact
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from core.mirofish_model_log import (  # noqa: E402
    compare_mirofish_model_log,
    load_mirofish_model_log,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill-dir", default=str(SKILL_DIR))
    parser.add_argument(
        "--write-artifact",
        action="store_true",
        help="Write validation_artifacts/mirofish_model_compare.json",
    )
    args = parser.parse_args()
    skill_dir = Path(args.skill_dir)
    rows = load_mirofish_model_log(skill_dir)
    report = compare_mirofish_model_log(rows)
    report["log_rows"] = len(rows)
    models = sorted({str(r.get("llm_model") or "") for r in rows if r.get("llm_model")})
    report["models_seen"] = models

    if not rows:
        print("SKIP: no .mirofish_model_log.jsonl yet — run a scan with MiroFish enabled")
        verdict = "skip"
    elif report["paired_rows"] == 0:
        print(
            "SKIP: log has "
            f"{len(rows)} row(s) across models {models or ['(none)']} "
            "but no ticker+seed pairs with two models"
        )
        print("Re-run the same names after changing LLM_MODEL_NAME, or keep a gpt-4o-mini log then Grok.")
        verdict = "skip"
    else:
        print(
            "PASS: paired={n} mean_abs_conviction_delta={d} models={m}".format(
                n=report["paired_rows"],
                d=report["mean_abs_conviction_delta"],
                m=models,
            )
        )
        verdict = "pass"
    report["verdict"] = verdict

    if args.write_artifact:
        out_dir = skill_dir / "validation_artifacts"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "mirofish_model_compare.json"
        out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
