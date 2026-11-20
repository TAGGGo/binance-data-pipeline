"""Backtest Murphy's 'options buyer net call/put premium spending' red signal.
Reads dopt/opt_trades_daily_*.csv (dedupe days), BTC daily close, BGeometrics STH-RP + TMM.
"""
import glob, sys
import numpy as np, pandas as pd

S = "/tmp/claude-0/-home-claude/d3e2c835-4ccc-57ca-b3ed-396d0d02e2f7/scratchpad"
U = "/mnt/user-data/uploads/datahub/binance-data-pipeline/data"

frames = []
for f in [S + "/opt_trades_daily.csv"]:
    frames.append(pd.read_csv(f))
t = pd.concat(frames)
# dedupe overlapping workers: keep one tag per day (rows of a day are identical across tags)
t = t.drop_duplicates()
t["d"] = pd.to_datetime(t.d)

def piv(df):
    g = df.groupby(["d", "cp", "side"]).prem_usd.sum().unstack(["cp", "side"]).fillna(0)
    out = pd.DataFrame(index=g.index)
    out["cb"], out["cs"], out["pb"], out["ps"] = g[("C", "buy")], g[("C", "sell")], g[("P", "buy")], g[("P", "sell")]
    return out

a = piv(t)                      # all trades
nb = piv(t[t.block == 0])       # screen trades only
days = pd.date_range(a.index.min(), a.index.max())
a, nb = a.reindex(days).fillna(0), nb.reindex(days).fillna(0)

px = pd.read_csv(U + "/state/btc_1d_tmp.csv", parse_dates=["d"]).set_index("d").close
sth = pd.read_csv(U + "/onchain/bgeometrics/sth-realized-price.csv", parse_dates=["d"]).set_index("d").iloc[:, 0]
tmm = pd.read_csv(U + "/onchain/bgeometrics/true-market-mean.csv", parse_dates=["d"]).set_index("d").iloc[:, 0]

df = pd.DataFrame(index=days)
df["px"] = px.reindex(days)
df["sma200"] = px.rolling(200).mean().reindex(days)
df["bull_onchain"] = (sth.reindex(days).ffill() > tmm.reindex(days).ffill())
df["bull_sma"] = df.px > df.sma200
for name, x in (("all", a), ("screen", nb)):
    buy_diff = x.cb - x.pb                       # what buyers spent on calls minus puts
    net_diff = (x.cb - x.cs) - (x.pb - x.ps)     # net of sellers
    tot = (x.cb + x.pb).rolling(7).sum()
    df[name + "_buy7"] = buy_diff.rolling(7).sum() / tot
    df[name + "_net7"] = net_diff.rolling(7).sum() / tot
    df[name + "_buy1"] = buy_diff / (x.cb + x.pb)
for h in (7, 30, 90):
    df["f%d" % h] = df.px.shift(-h) / df.px - 1

def stats(sub, h):
    r = sub["f%d" % h].dropna()
    return "%3d  med %+5.1f%%  up %3.0f%%" % (len(r), r.median() * 100, (r > 0).mean() * 100) if len(r) else "  0"

def events(sig, gap=14):
    on = sig.fillna(False).values; idx = sig.index; ev = []; last = -999
    for i in range(len(on)):
        if on[i] and not (i > 0 and on[i - 1]) and i - last >= gap:
            ev.append(idx[i]); last = i
        elif on[i]:
            last = i if i - last < gap else last
    return ev

col = sys.argv[1] if len(sys.argv) > 1 else "all_buy7"
print("data", days.min().date(), "->", days.max().date(), "| signal", col, "| red share", round((df[col] < 0).mean() * 100), "%")
print("latest 10 days:"); print(df[[col, "all_net7", "px"]].tail(10).round(3).to_string())
for reg in ("bull_onchain", "bull_sma"):
    for state in (True, False):
        base = df[df[reg] == state]
        ev = [d for d in events(df[col] < 0) if df.loc[d, reg] == state]
        evd = df.loc[ev]
        print("\n%s=%s  base days %d" % (reg, state, len(base)))
        for h in (7, 30, 90):
            print("  %3dd  base %s | RED events %s" % (h, stats(base, h), stats(evd, h)))
    # quintiles within bull
q = df[df.bull_onchain].copy(); q["qt"] = pd.qcut(q[col], 5, labels=["Q1 most put", "Q2", "Q3", "Q4", "Q5 most call"])
print("\nquintiles of", col, "in on-chain bull (overlapping days):")
print(q.groupby("qt", observed=True).agg(n=("f30", "size"), f7=("f7", "median"), f30=("f30", "median"), up30=("f30", lambda r: (r > 0).mean()), f90=("f90", "median")).round(3).to_string())
df.to_pickle(S + "/optspend_df.pkl")
