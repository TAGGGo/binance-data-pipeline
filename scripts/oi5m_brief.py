"""5-minute OI readout in COINS, per venue (oi_5m). Use it to tell new positions from closing.

  python3 scripts/oi5m_brief.py [BTC] [hours=6] [minutes_per_row=15]

Rows in Pacific time: price (Binance), OI change in coins per venue during the row, total, and % of total.
Rule of thumb: price down + OI up = new shorts (or longs adding); price down + OI down = longs closing/liquidated;
price up + OI up = new longs; price up + OI down = short covering.
"""
import sys
from pathlib import Path

import duckdb
import pandas as pd

sym = (sys.argv[1] if len(sys.argv) > 1 else "BTC").upper()
hours = float(sys.argv[2]) if len(sys.argv) > 2 else 6
mins = int(sys.argv[3]) if len(sys.argv) > 3 else 15
con = duckdb.connect(str(Path(__file__).resolve().parents[1] / "data" / "market.duckdb"), read_only=True)
d = con.execute(f"""select ts, venue, contract, oi_coin c, oi_usd/nullif(oi_coin,0) px
    from oi_5m where symbol=? and ts >= now()::timestamp - interval {int(hours * 60) + mins} minute""", [sym]).df()
if d.empty:
    sys.exit("no oi_5m rows yet")
# one series per venue+contract, forward-filled so a venue whose latest bar isn't out yet doesn't look like a drop
c = d.pivot_table(index="ts", columns=["venue", "contract"], values="c").sort_index().ffill()
last_full = d.groupby(["venue", "contract"]).ts.max().min()
c = c[c.index <= last_full].T.groupby(level=0).sum(min_count=1).T
pxs = d[d.venue == "binance"] if (d.venue == "binance").any() else d[(d.venue == "okx") & (d.contract == "linear")]
px = pxs.groupby("ts").px.max()
r = c.resample(f"{mins}min").last()
out = r.diff()
out["total"] = out.sum(axis=1, min_count=1)
out["tot_%"] = out.total / r.sum(axis=1) * 100
out.insert(0, "px", px.resample(f"{mins}min").last())
out = out.iloc[1:]
out.index = out.index.tz_localize("UTC").tz_convert("America/Los_Angeles").strftime("%m/%d %H:%M")
print(f"{sym} OI change in coins per {mins}m (PT); last total OI {r.sum(axis=1).iloc[-1]:,.0f} coins")
out["tot_%"] = out["tot_%"].map(lambda v: f"{v:+.2f}" if v == v else "-")
print(out.round(0).to_string(na_rep="-"))
