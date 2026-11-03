"""Ad-hoc: distribution of per-entry-date would-filter rates in the offline replay cache."""
import json
from pathlib import Path

import pandas as pd

path = Path("validation_artifacts/entry_timing_replay_cache_control_legacy_aug.json")
rows = json.loads(path.read_text(encoding="utf-8"))
df = pd.DataFrame(rows)
print("rows:", len(df), "cols:", sorted(df.columns))

buf = pd.to_numeric(df["breakout_buffer_pct"], errors="coerce")
df["wf"] = buf < 0.01
df["entry_iso"] = pd.to_datetime(df["entry_date"]).dt.strftime("%Y-%m-%d")

overall = df["wf"].mean() * 100
print(f"overall would-filter (buffer<1%): {overall:.1f}%  n={len(df)}")

daily = df.groupby("entry_iso").agg(n=("wf", "size"), rate=("wf", "mean"))
daily["rate"] *= 100
big = daily[daily["n"] >= 10]
print(f"days total={len(daily)}  days with n>=10: {len(big)}")
qs = [0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95]
print("daily rate percentiles (n>=10 days):")
for q in qs:
    print(f"  p{int(q * 100):02d}: {big['rate'].quantile(q):.1f}%")
print(f"  share of days (n>=10) with rate > 65%: {(big['rate'] > 65).mean() * 100:.1f}%")
print(f"  share of days (n>=10) with rate >= 80%: {(big['rate'] >= 80).mean() * 100:.1f}%")
print(f"  share of days (n>=10) with rate < 35%: {(big['rate'] < 35).mean() * 100:.1f}%")

# weekly (5-session) rolling aggregate on trade-weighted basis
daily_sorted = daily.sort_index()
daily_sorted["wf_cnt"] = daily_sorted["n"] * daily_sorted["rate"] / 100
roll_n = daily_sorted["n"].rolling(5).sum()
roll_wf = daily_sorted["wf_cnt"].rolling(5).sum()
roll_rate = (roll_wf / roll_n * 100).dropna()
print("rolling 5-session pooled rate percentiles:")
for q in qs:
    print(f"  p{int(q * 100):02d}: {roll_rate.quantile(q):.1f}%")
print(f"  share of 5-session windows > 65%: {(roll_rate > 65).mean() * 100:.1f}%")
print(f"  share of 5-session windows >= 82%: {(roll_rate >= 82).mean() * 100:.1f}%")

# by era for context
if "era" in df.columns:
    print("\nby era:")
    for era, grp in df.groupby("era"):
        print(f"  {era}: {grp['wf'].mean() * 100:.1f}%  n={len(grp)}")
