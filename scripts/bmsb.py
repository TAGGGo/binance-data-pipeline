#!/usr/bin/env python3
"""Bull Market Support Band (Benjamin Cowen / common definition) for BTC + the 56-coin universe.

BMSB = band between the 20-week SMA and the 21-week EMA of WEEKLY closes (weeks end Sunday UTC).
The current week uses the live Binance 1h close, so the band moves a little intraweek.
Prints BTC (price, band, 50D SMA), the coins asked for, and how many alts are above / inside / below.
Usage: python3 scripts/bmsb.py [SYM ...]      (default list: ETH XRP SOL ZEC NEAR DASH SUI BNB DOGE)
Project doc: claude/bmsb-2026-10-22.md (definition + BTC backtest).
"""
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
con = duckdb.connect(str(ROOT / "data/market.duckdb"), read_only=True)
k = con.execute("select symbol, ts, close from bn_kline_1d where market='spot' and ts>='2023-01-01'").df()
live = con.execute("select symbol, arg_max(close,ts) px, max(ts) t from bn_kline_1h where market='spot' "
                   "and ts>=now()-interval 3 day group by 1").df().set_index("symbol")
con.close()
k["ts"] = pd.to_datetime(k.ts)
c = k.pivot(index="ts", columns="symbol", values="close")
for s in live.index:
    if s in c.columns:
        c.loc[c.index[-1], s] = live.px[s]
w = c.resample("W-SUN").last()
sma20, ema21 = w.rolling(20).mean(), w.ewm(span=21, adjust=False).mean()
lo, hi = np.minimum(sma20, ema21), np.maximum(sma20, ema21)
px, d50 = c.iloc[-1], c.rolling(50).mean().iloc[-1]
t = pd.DataFrame({"px": px, "band_lo": lo.iloc[-1], "band_hi": hi.iloc[-1],
                  "vs_hi%": (px / hi.iloc[-1] - 1) * 100, "vs_lo%": (px / lo.iloc[-1] - 1) * 100,
                  "vs50d%": (px / d50 - 1) * 100}).dropna()
asof = (live.t.max() - pd.Timedelta(hours=7)).strftime("%m/%d %H:00 PT") if len(live) else "?"


def where(r):
    return "ABOVE" if r["vs_hi%"] > 0 else ("INSIDE" if r["vs_lo%"] >= 0 else "BELOW")


b = t.loc["BTC"]
print(f"BMSB (20W SMA / 21W EMA, weekly closes) as of {asof} (forming week = live 1h close)")
print(f"BTC {b.px:,.0f} | band {b.band_lo:,.0f}-{b.band_hi:,.0f} ({where(b)}, {b['vs_hi%']:+.1f}% vs top) | "
      f"50D SMA {d50['BTC']:,.0f} ({b['vs50d%']:+.1f}%) | band top last 4 wks: "
      + " ".join(f"{x:,.0f}" for x in hi["BTC"].tail(4)))
syms = sys.argv[1:] or ["ETH", "XRP", "SOL", "ZEC", "NEAR", "DASH", "SUI", "BNB", "DOGE"]
for s in syms:
    if s in t.index:
        r = t.loc[s]
        print(f"{s:5s} {r.px:>10.4g} band {r.band_lo:.4g}-{r.band_hi:.4g} {where(r):6s} {r['vs_hi%']:+6.1f}% vs top, {r['vs50d%']:+6.1f}% vs 50D")
    else:
        print(f"{s:5s} n/a (needs >= 21 weeks of Binance spot)")
a = t.drop("BTC")
st = a.apply(where, axis=1)
print(f"alts n={len(a)}: above {sum(st=='ABOVE')} / inside {sum(st=='INSIDE')} / below {sum(st=='BELOW')} | "
      f"median {a['vs_hi%'].median():+.1f}% vs band top | below: " + " ".join(st[st == 'BELOW'].index))
