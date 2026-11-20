#!/usr/bin/env python3
"""Live perp OI (per exchange + summed in USD) and funding, no VPN, via TradingView symbols. Added 2026-10-23.

Binance futures (fapi) refuses US IPs, so the hourly pipeline only gets OI during its VPN step (20-80 min behind);
that DB data is now used as the history/backfill for backtests. For "now", use this script.
TradingView symbols used (checked 10/23 on DASH):
  OI:      BINANCE / BYBIT / BITGET  <SYM>USDT.P_OI  (unit: coins)
           OKX  <SYM>USDT.P_OI, HYPERLIQUID <SYM>USDC.P_OI  (unit: USD; HL matched our DB $3.69M vs $3.73M)
  Funding: <EX>:<SYM>USDT.P_FR (per funding interval, e.g. -3.1e-05 = -0.0031%); premium BINANCE:<SYM>USDT.P_PREMIUM
TradingView's own "Open Interest Agg" indicator covers MORE venues (DASH 10/23: chart 49.1M vs this sum ~42M), so
compare changes, not levels. OKX/Bybit/Bitget units are assumptions from magnitude; re-check if a total looks off.
Usage: python3 scripts/live_oi.py DASH [ZEC BTC ...] [--bars 6]
Needs tvDatafeed (device_bash VM: pip install --user git+https://github.com/rongardF/tvdatafeed; Mac .venv has it).
Last row = the CURRENT, still-forming hour. Times in PT.
"""
import logging
import sys

import pandas as pd
from tvDatafeed import Interval, TvDatafeed

logging.getLogger("tvDatafeed.main").setLevel(logging.CRITICAL)
bars = int(sys.argv[sys.argv.index("--bars") + 1]) if "--bars" in sys.argv else 6
syms = [a.upper() for a in sys.argv[1:] if not a.startswith("--") and not a.isdigit()] or ["BTC"]
VENUES = [("BINANCE", "{s}USDT.P_OI", "coin"), ("BYBIT", "{s}USDT.P_OI", "coin"), ("BITGET", "{s}USDT.P_OI", "coin"),
          ("OKX", "{s}USDT.P_OI", "usd"), ("HYPERLIQUID", "{s}USDC.P_OI", "usd")]
tv = TvDatafeed()


def get(sym, ex):
    try:
        df = tv.get_hist(symbol=sym, exchange=ex, interval=Interval.in_1_hour, n_bars=bars)
        return None if df is None or df.empty else df
    except Exception:
        return None


def pt(idx):
    idx = pd.to_datetime(idx)
    idx = idx.tz_localize("UTC") if idx.tz is None else idx
    return idx.tz_convert("America/Los_Angeles").strftime("%m/%d %H:00 PT")


for s in syms:
    px = get(f"{s}USDT.P", "BINANCE")
    if px is None:
        print(s, "no Binance perp price")
        continue
    out = pd.DataFrame({"px": px.close, "hi": px.high, "lo": px.low})
    usd_cols = []
    for ex, pat, unit in VENUES:
        df = get(pat.format(s=s), ex)
        if df is None:
            continue
        v = df.close.reindex(out.index)
        out[f"{ex[:3]}_$M"] = (v * out.px if unit == "coin" else v) / 1e6
        usd_cols.append(f"{ex[:3]}_$M")
        if ex == "BINANCE":
            out["BIN_coins"] = v
    out["SUM_$M"] = out[usd_cols].sum(axis=1, min_count=1)
    out["SUM_coins_chg%"] = ((out["SUM_$M"] / out.px).pct_change() * 100)
    fr = get(f"{s}USDT.P_FR", "BINANCE")
    if fr is not None:
        out["bin_fund_%"] = (fr.close.reindex(out.index) * 100)
    out.index = pt(out.index)
    print(f"{s}: perp OI by venue ($M), sum, Binance funding (last row = current hour, forming)")
    print(out.round(3).to_string())
