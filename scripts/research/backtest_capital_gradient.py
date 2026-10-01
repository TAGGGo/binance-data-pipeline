"""Reconstruction of Murphy's "capital vs price increment gradient" (资金与价格增量梯度)
and a backtest of its bearish divergence at price highs. 2026-09-30.

Net capital flow = change in realized cap (realized price x supply; supply = sum of
BGeometrics hodl waves). Indicator (default) = N-day realized-cap growth, z-scored vs
trailing 365 days. Variant 'gap' = z(realized-cap growth) - z(price growth). Variant 'inc' = z((dMcap - dRcap)/Rcap), best match to his chart.
This is OUR reconstruction from his description; his exact formula is unknown.

Event: close is a new H-day closing high, an earlier H-day high happened 10..60 days
before, and the indicator now is >= DROP lower than at that earlier high.
Declustered: first event after >= 30 days without one. Control = new highs WITHOUT divergence.
"""
import argparse, pandas as pd, numpy as np
D = "data/onchain/bgeometrics/"
ap = argparse.ArgumentParser()
ap.add_argument("--n", type=int, default=30)
ap.add_argument("--high", type=int, default=90)
ap.add_argument("--drop", type=float, default=0.5)
ap.add_argument("--start", default="2013-01-01")
ap.add_argument("--variant", default="flow", choices=["flow", "gap", "inc"])
ap.add_argument("--events", action="store_true")
a = ap.parse_args()

def rd(f): x = pd.read_csv(D + f + ".csv", parse_dates=["d"]).set_index("d"); return x.apply(pd.to_numeric, errors="coerce")
px = rd("btc-price")["btcPrice"]
rp = rd("realized-price")["realizedPrice"]
sup = rd("hodl-waves-supply").sum(axis=1)
df = pd.concat({"px": px, "rp": rp, "sup": sup}, axis=1).dropna()
df = df[(df.px > 0) & (df.sup > 0)]
df["rcap"] = df.rp * df.sup
n = a.n
g_cap = df.rcap.pct_change(n)
g_px = df.px.pct_change(n)
z = lambda s: (s - s.rolling(365, min_periods=180).mean()) / s.rolling(365, min_periods=180).std()
df["flow_usd_n"] = df.rcap.diff(n)
if a.variant == "flow": df["ind"] = z(g_cap)
elif a.variant == "gap": df["ind"] = z(g_cap) - z(g_px)
else:  # inc: N-day market-cap increment minus realized-cap increment, / realized cap, z vs 365d.
    # Closest match to the shape of Murphy's 2026-09-27 chart (peak late Aug, falling into late Sep).
    df["ind"] = z((df.px * df.sup).diff(n) / df.rcap - df.rcap.diff(n) / df.rcap)
df = df[df.index >= a.start].copy()

hi = df.px >= df.px.rolling(a.high, min_periods=a.high).max()
ev_div, ev_ctl = [], []
hi_days = df.index[hi]
for t in hi_days:
    prev = [p for p in hi_days if 10 <= (t - p).days <= 60]
    if not prev or pd.isna(df.ind.get(t)): continue
    ref = max(df.ind.loc[prev].max(), -9)
    (ev_div if ref - df.ind[t] >= a.drop else ev_ctl).append(t)

def declu(days, gap=30):
    out, last = [], None
    for t in days:
        if last is None or (t - last).days >= gap: out.append(t)
        last = t
    return out

def fwd(days):
    rows = []
    for t in days:
        i = df.index.get_loc(t); p0 = df.px.iloc[i]
        r = {"date": t.date(), "px": round(p0), "ind": round(df.ind.iloc[i], 2)}
        for h in (7, 14, 30, 60, 90):
            r[f"f{h}"] = df.px.iloc[i + h] / p0 - 1 if i + h < len(df) else np.nan
        w = df.px.iloc[i + 1:i + 31]
        r["dd30"] = w.min() / p0 - 1 if len(w) == 30 else np.nan
        rows.append(r)
    return pd.DataFrame(rows)

def summ(name, d):
    d = d.dropna(subset=["f30"])
    if d.empty: print(name, "no events"); return
    s = {"events": len(d)}
    for h in (7, 14, 30, 60, 90):
        c = d[f"f{h}"].dropna(); s[f"med{h}"] = f"{c.median():+.1%}"; s[f"up{h}"] = f"{(c > 0).mean():.0%}"
    s["dd30_med"] = f"{d.dd30.median():+.1%}"; s["dd30<-10%"] = f"{(d.dd30 < -0.10).mean():.0%}"
    print(f"{name:28s}", "  ".join(f"{k}={v}" for k, v in s.items()))

print(f"variant={a.variant} n={n}d high={a.high}d drop>={a.drop}z  data {df.index[0].date()}..{df.index[-1].date()}")
D1, C1 = fwd(declu(ev_div)), fwd(declu(ev_ctl))
summ("DIVERGENCE at new high", D1)
summ("new high, no divergence", C1)
base = fwd(list(df.index[::7]))
summ("BASE (weekly, all days)", base)
for since in ("2020-01-01",):
    summ(f"DIVERGENCE since {since[:4]}", D1[pd.to_datetime(D1.date) >= since])
    summ(f"no-div since {since[:4]}", C1[pd.to_datetime(C1.date) >= since])
if a.events:
    pd.set_option("display.width", 200)
    D2 = D1.copy(); [D2.__setitem__(c, D2[c].map(lambda v: f"{v:+.1%}")) for c in ("f7","f14","f30","f60","f90","dd30")]; print(D2.to_string())
tail = df.tail(20)[["px", "ind", "flow_usd_n"]].copy(); tail["flow_usd_n"] = (tail.flow_usd_n / 1e9).round(1)
print("\nlast 20 days (flow_usd_n in $B over n days):"); print(tail.round(2).to_string())
last_hi = hi_days[-1] if len(hi_days) else None
print("last new high:", last_hi.date() if last_hi is not None else None, "div events in last 30d:", [t.date() for t in ev_div if (df.index[-1] - t).days <= 30])
