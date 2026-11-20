"""Hyperliquid watch-list readout: big positions, liquidation clusters and trigger (stop / TP) clusters near price.

  python3 scripts/hl_whales_brief.py [BTC] [band_pct=8]
"""
import sys
from pathlib import Path

import duckdb

sym = (sys.argv[1] if len(sys.argv) > 1 else "BTC").upper()
band = float(sys.argv[2]) if len(sys.argv) > 2 else 8
con = duckdb.connect(str(Path(__file__).resolve().parents[1] / "data" / "market.duckdb"), read_only=True)
t = con.execute("select max(ts) from hl_whale_pos").fetchone()[0]
p = con.execute("select * from hl_whale_pos where ts=? and coin=?", [t, sym]).df()
has_o = con.execute("select count(*) from information_schema.tables where table_name='hl_whale_orders'").fetchone()[0]
o = con.execute("select * from hl_whale_orders where ts=(select max(ts) from hl_whale_orders) and coin=?", [sym]).df() if has_o else None
px = (p.notional_usd / p["size"].abs()).median() if len(p) else None
print(f"{sym} Hyperliquid watch-list @ {t} UTC: {len(p)} positions, long ${p[p['size']>0].notional_usd.sum()/1e6:,.0f}M, "
      f"short ${p[p['size']<0].notional_usd.sum()/1e6:,.0f}M, mark ~{px:,.0f}" if px else f"no {sym} positions")
if px:
    step = 10 ** max(0, len(str(int(px))) - 3)        # BTC 1,000 buckets, ETH 100, SOL 10...
    def bucket(df, col, label):
        d = df[(df[col] - px).abs() <= px * band / 100].copy()
        if d.empty:
            print(f"{label}: none within +-{band:.0f}%"); return
        d["b"] = (d[col] // step) * step
        g = d.groupby("b").agg(usd=("notional_usd", "sum"), n=("address", "nunique")).sort_index(ascending=False)
        print(f"{label} within +-{band:.0f}% ($M, accounts):")
        print("  " + " | ".join(f"{b:,.0f}: {u/1e6:,.1f} ({n})" for b, (u, n) in g.iterrows()))
    bucket(p[p['size'] > 0].dropna(subset=["liq_px"]), "liq_px", "long liquidations")
    bucket(p[p['size'] < 0].dropna(subset=["liq_px"]), "liq_px", "short liquidations")
    if o is not None and len(o):
        bucket(o[o.side == "A"], "trigger_px", "sell triggers (long stops / TPs)")
        bucket(o[o.side == "B"], "trigger_px", "buy triggers (short stops / TPs)")
    top = p.reindex(p.notional_usd.sort_values(ascending=False).index).head(8)
    print("biggest:")
    for _, r in top.iterrows():
        print(f"  {r.address[:8]} {'LONG ' if r['size']>0 else 'SHORT'} ${r.notional_usd/1e6:,.1f}M entry {r.entry_px:,.0f} "
              f"liq {r.liq_px:,.0f}" if r.liq_px == r.liq_px and r.liq_px else
              f"  {r.address[:8]} {'LONG ' if r['size']>0 else 'SHORT'} ${r.notional_usd/1e6:,.1f}M entry {r.entry_px:,.0f} liq -")
