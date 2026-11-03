import json
from pathlib import Path

import pandas as pd

rows = json.loads(Path("validation_artifacts/entry_timing_replay_cache_control_legacy_aug.json").read_text(encoding="utf-8"))
df = pd.DataFrame(rows)
df["entry_dt"] = pd.to_datetime(df["entry_date"])
print("entry date range:", df["entry_dt"].min(), "->", df["entry_dt"].max())
print("\nby era date ranges:")
for era, grp in df.groupby("era"):
    print(f"  {era}: {grp['entry_dt'].min().date()} -> {grp['entry_dt'].max().date()}  n={len(grp)}")

buf = pd.to_numeric(df["breakout_buffer_pct"], errors="coerce")
df["wf"] = buf < 0.01
recent = df[df["entry_dt"] >= "2026-05-01"].copy()
recent["iso"] = recent["entry_dt"].dt.strftime("%Y-%m-%d")
daily = recent.groupby("iso").agg(n=("wf", "size"), rate=("wf", "mean"))
daily["rate"] = (daily["rate"] * 100).round(1)
print("\nlast ~30 offline replay days (n, would-filter %):")
print(daily.tail(30).to_string())
