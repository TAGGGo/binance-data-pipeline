#!/usr/bin/env python3
"""GOOG/BTC ratio = one GOOG share in BTC, and BTC for Avalon's 198 shares. Added 2026-10-30 after Claude
compared mismatched %-changes (GOOG vs prior close, BTC vs UTC day) and got the direction wrong.
RULE: never infer the ratio from two %-changes; always divide price by price at named timestamps.
  python3 scripts/goog_btc.py GOOG_NOW BTC_NOW      (live prices you just fetched; both required)
Reference points = GOOG close vs BTC at 4 PM ET that day (bn_kline_1h spot, the hour ending 16:00 ET)."""
import sys
from zoneinfo import ZoneInfo
import duckdb, pandas as pd
SH = 198
REFS = ["2026-08-04", "2026-10-08", "2026-10-23", "2026-10-28"]
g_now, b_now = float(sys.argv[1]), float(sys.argv[2])
c = duckdb.connect("data/market.duckdb", read_only=True)
def ref(d):
    t = pd.Timestamp(d + " 16:00", tz=ZoneInfo("America/New_York")).tz_convert("UTC").tz_localize(None) - pd.Timedelta(hours=1)
    g = c.execute("select close from equity_daily where ticker='GOOG' and date=?", [d]).fetchone()
    b = c.execute("select close from bn_kline_1h where symbol='BTC' and market='spot' and ts=?", [t]).fetchone()
    return (g[0], b[0]) if g and b else None
last = c.execute("select max(date) from equity_daily where ticker='GOOG'").fetchone()[0].isoformat()
now = g_now / b_now
print(f"NOW   GOOG {g_now:.2f} / BTC {b_now:,.0f} = {now:.6f}  -> {SH*now:.3f} BTC")
for d in REFS + ([last] if last not in REFS else []):
    r = ref(d)
    if r:
        x = r[0] / r[1]
        print(f"{d}  GOOG {r[0]:.2f} / BTC {r[1]:,.0f} = {x:.6f}  -> {SH*x:.3f} BTC   now vs this: {(now/x-1)*100:+.1f}%")
