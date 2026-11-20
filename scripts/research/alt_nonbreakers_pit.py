"""BTC consecutive 20-day-low days: do alts that did NOT break their own low beat BTC over the next 20 days?
Point-in-time universe (top N alts by trailing 30-day avg quote volume, incl. delisted), from
data/universe/spot_usdt_1d_spliced.csv.gz (scripts/ops/fetch_universe.py). Question: Avalon 2026-11-06
("BTC 不停破低，山寨破不了了…涨起来不可限量"). Usage: python3 scripts/research/alt_nonbreakers_pit.py [--top 100]
"""
import argparse
from pathlib import Path
import numpy as np, pandas as pd
REPO = Path(__file__).resolve().parents[2]
ap = argparse.ArgumentParser(); ap.add_argument("--top", type=int, default=100); ap.add_argument("--h", type=int, default=20); a = ap.parse_args()
d = pd.read_csv(REPO / "data/universe/spot_usdt_1d_spliced.csv.gz", parse_dates=["t"])
C = d.pivot_table(index="t", columns="symbol", values="c"); L = d.pivot_table(index="t", columns="symbol", values="l")
Q = d.pivot_table(index="t", columns="symbol", values="qv")
b = C["BTCUSDT"]; prev = L.rolling(20, min_periods=20).min().shift(1); vol30 = Q.rolling(30, min_periods=25).mean().shift(1)
H = a.h; bb = (L["BTCUSDT"] < prev["BTCUSDT"]).values; ev = []; last = -99
for i in range(30, len(C) - H):
    if bb[i] and bb[i-10:i].any() and i - last >= 5: ev.append(i); last = i
rows = []
for i in ev:
    t = C.index[i]; bf = b.iloc[i+H] / b.iloc[i] - 1
    v = vol30.iloc[i].drop("BTCUSDT").dropna(); uni = v[v > 0].sort_values(ascending=False).head(a.top).index
    for s in uni:
        if pd.isna(C[s].iloc[i]) or pd.isna(prev[s].iloc[i]): continue
        fut = C[s].iloc[i+1:i+H+1].dropna(); f = -1.0 if fut.empty else fut.iloc[-1] / C[s].iloc[i] - 1
        dist = L[s].iloc[i] / prev[s].iloc[i] - 1
        rows.append(dict(t=t, s=s, grp="A broke" if dist < 0 else ("C held>=10%" if dist >= 0.10 else "B held<10%"), rel=(1+f)/(1+bf)-1, bf=bf))
R = pd.DataFrame(rows)
def pe(x): s = x.groupby("t").rel.apply(lambda r: (r > 0).mean() * 100); return f"{s.mean():.0f}% ({len(s)})" if len(s) else "-"
print(f"events {len(ev)}, top {a.top}, horizon {H}d; per-event share beating BTC (events)")
for era, g in [("all", R), ("2021-23", R[R.t < "2024-01-01"]), ("2024+", R[R.t >= "2024-01-01"])]:
    for lab, c in [("all", g), ("BTC up", g[g.bf > 0]), ("BTC up>=10%", g[g.bf >= 0.10])]:
        print(f"{era:8s} {lab:12s}", " | ".join(f"{k}: {pe(c[c.grp == k])}" for k in ["A broke", "B held<10%", "C held>=10%"]))
out = REPO / "data/outputs/alt_nonbreakers_pit_rows.csv"; R.to_csv(out, index=False); print("rows ->", out)
