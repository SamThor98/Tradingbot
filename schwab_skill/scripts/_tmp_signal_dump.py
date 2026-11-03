import json
import sqlite3

con = sqlite3.connect("webapp/webapp.db")
cur = con.execute("select value_json from app_state where user_id='local' and key='last_scan'")
d = json.loads(cur.fetchone()[0])
sigs = d.get("signals") or []
print("scan at:", d.get("at"), "n signals:", len(sigs))
for s in sigs[:12]:
    ets = s.get("entry_timing_shadow") or {}
    print(
        s.get("ticker"),
        "| price", s.get("price"),
        "| buffer", ets.get("breakout_buffer_pct"),
        "| would_filter", ets.get("would_filter"),
        "| reasons", ets.get("would_filter_reasons"),
        "| pct52w", ets.get("pct_from_52w_high"),
    )
if sigs:
    print()
    print("full first signal keys:", sorted(sigs[0].keys()))
