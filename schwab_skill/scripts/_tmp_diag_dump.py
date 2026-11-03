import json
import sqlite3

con = sqlite3.connect("webapp/webapp.db")
cur = con.execute("select value_json from app_state where user_id='local' and key='last_scan'")
row = cur.fetchone()
d = json.loads(row[0])
diag = d.get("diagnostics") or {}
print("scan at:", d.get("at"), "signals:", d.get("signals_found"))
keys = ("entry", "breakout", "stage_a", "stage2", "vcp", "sector", "watchlist", "self_study", "regime", "rank_filter", "quality")
interesting = {k: v for k, v in diag.items() if any(s in k for s in keys)}
print(json.dumps(interesting, indent=1, default=str))
