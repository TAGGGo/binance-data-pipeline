#!/usr/bin/env python3
"""BTC watchlist snapshot (compact, ~20 lines). See project doc claude/btc-watchlist.md.

Prints: price vs 200d SMA / STH-RP / TMM (regime), STH-SOPR + STH realized loss (last days),
options buyer spend buy7 / net7 (Deribit trades, last 8 days, RED = < 0).
Run next to: scripts/flow_brief.py BTC 24 (order flow + Deribit snapshot) and
scripts/chip_concentration.py (near5 concentration).
Usage: python3 scripts/btc_watch.py
"""
import csv
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BG = ROOT / "data/onchain/bgeometrics"


def bg(name):
    p = BG / (name + ".csv")
    if not p.exists():
        return pd.Series(dtype=float)
    d = pd.read_csv(p, parse_dates=["d"]).set_index("d")
    return pd.to_numeric(d.iloc[:, 0], errors="coerce").dropna()


con = duckdb.connect(str(ROOT / "data/market.duckdb"), read_only=True)
px = con.execute("select ts, close from bn_kline_1d where symbol='BTC' and market='spot' order by ts").df().set_index("ts").close
live = con.execute("select ts, close from bn_kline_1h where symbol='BTC' and market='spot' order by ts desc limit 1").fetchone()
sma200 = px.iloc[:-1].rolling(200).mean().iloc[-1]
sth, tmm, sopr, loss = bg("sth-realized-price"), bg("true-market-mean"), bg("sth-sopr"), bg("realized_loss_sth")
p = live[1]
print("BTC %.0f (1h close %s UTC) | 200d SMA %.0f (%+.1f%%)" % (p, live[0].strftime("%m-%d %H:%M"), sma200, (p / sma200 - 1) * 100))
if len(sth) and len(tmm):
    print("STH-RP %.0f (%s, price %+.1f%%) | TMM %.0f (%s, price %+.1f%%) | STH-RP vs TMM %+.1f%% -> %s" % (
        sth.iloc[-1], sth.index[-1].strftime("%m-%d"), (p / sth.iloc[-1] - 1) * 100,
        tmm.iloc[-1], tmm.index[-1].strftime("%m-%d"), (p / tmm.iloc[-1] - 1) * 100,
        (sth.iloc[-1] / tmm.iloc[-1] - 1) * 100, "CONFIRMED BULL" if sth.iloc[-1] > tmm.iloc[-1] else "not confirmed (STH-RP < TMM)"))
if len(sopr):
    print("STH-SOPR last 5: " + "  ".join("%s %.4f" % (d.strftime("%m-%d"), v) for d, v in sopr.tail(5).items()))
if len(loss):
    l30 = loss.tail(30)
    print("STH realized loss $M last 5: " + "  ".join("%s %.0f" % (d.strftime("%m-%d"), v / 1e6) for d, v in loss.tail(5).items())
          + " | 30d worst %s %.0f" % (l30.idxmin().strftime("%m-%d"), l30.min() / 1e6))

f = ROOT / "data/onchain/deribit/opt_trades_daily.csv"
if f.exists():
    t = pd.read_csv(f, usecols=["d", "cp", "side", "prem_usd"], parse_dates=["d"])
    g = t.groupby(["d", "cp", "side"]).prem_usd.sum().unstack(["cp", "side"]).fillna(0)
    cb, cs, pb, ps = g[("C", "buy")], g[("C", "sell")], g[("P", "buy")], g[("P", "sell")]
    tot = (cb + pb).rolling(7).sum()
    buy7 = (cb - pb).rolling(7).sum() / tot
    net7 = ((cb - cs) - (pb - ps)).rolling(7).sum() / tot
    print("Options buyer spend (Deribit, 7d, RED<0)  d: buy7 / net7")
    for d in buy7.index[-8:]:
        b, n = buy7[d], net7[d]
        print("  %s  %+.3f%s / %+.3f%s" % (d.strftime("%m-%d"), b, " RED" if b < 0 else "    ", n, " RED" if n < 0 else ""))
    print("  (backtest: red = good 90d entry only in bear->bull transition; late-cycle red = trend ending. net7 red stronger.)")
