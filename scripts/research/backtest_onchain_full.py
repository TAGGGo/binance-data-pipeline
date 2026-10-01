"""Full-history BTC on-chain signal backtest (BGeometrics CSVs, 2013-01 .. latest).

Each signal = a condition on day t using only data up to t (fixed thresholds from common usage,
or expanding percentiles). Events are declustered: the first day the condition is true after
being false for >= 30 days. Outcome: BTC forward 30/90/180-day return from BGeometrics btc-price.
Compared with the base rate over all days since 2013. Unreviewed; small samples (a handful of
cycles), thresholds are the folklore ones (chosen with hindsight by the people who publish them).

  python3 scripts/research/backtest_onchain_full.py [--start 2013-01-01] [--events]
"""
import argparse, glob, os
import numpy as np, pandas as pd

D = "data/onchain/bgeometrics"
ap = argparse.ArgumentParser(); ap.add_argument("--start", default="2013-01-01"); ap.add_argument("--events", action="store_true")
a = ap.parse_args()

def col(name, c=None):
    m = pd.read_csv(f"{D}/{name}.csv", parse_dates=["d"]).set_index("d")
    return pd.to_numeric(m[c or m.columns[0]], errors="coerce")

df = pd.DataFrame({"px": col("btc-price")})
for n in ["sth-mvrv", "mvrv", "mvrv-zscore", "nupl", "sth-sopr", "realized-price", "sth-realized-price",
          "true-market-mean", "puell-multiple", "nrpl-usd", "realized-profit", "realized-loss", "lth-realized-price"]:
    df[n] = col(n)
df["ats"] = col("accumulation-trend-score", "ats")
df["lthchg"] = col("accumulation-trend-score", "lthChange30d")
df = df[df.px > 0].sort_index()
df["rcap"] = df["realized-price"] * 1  # proxy scale; nrpl normalized by realized price * supply below
sup = col("supply-current") if os.path.exists(f"{D}/supply-current.csv") else None
H = [30, 90, 180]
for h in H:
    df[f"f{h}"] = df.px.shift(-h) / df.px - 1
df["sthsopr7"] = df["sth-sopr"].rolling(7).mean()
# NRPL 30d as % of realized cap (realized price x ~supply); supply approximated by market cap / price if absent
df["nrpl30"] = df["nrpl-usd"].rolling(30).sum() / (df["realized-price"])  # BTC-equivalent units
df["nrpl30_pct"] = df["nrpl30"].expanding(365).rank(pct=True)
df["rp30_pct"] = df["realized-profit"].rolling(30).sum().div(df["realized-price"]).expanding(365).rank(pct=True)
df["ma200"] = df.px.rolling(200).mean()
below = lambda x, y: df[x] < df[y]
above_both = (df.px > df["sth-realized-price"]) & (df.px > df["true-market-mean"])

S = {
    # overheated / top-ish
    "STH-MVRV > 1.3": df["sth-mvrv"] > 1.3,
    "STH-MVRV > 1.5": df["sth-mvrv"] > 1.5,
    "MVRV Z > 5": df["mvrv-zscore"] > 5,
    "MVRV > 3": df["mvrv"] > 3,
    "NUPL > 0.6": df["nupl"] > 0.6,
    "Puell > 3": df["puell-multiple"] > 3,
    "NRPL 30d top 10% (expanding)": df["nrpl30_pct"] > 0.9,
    "Realized profit 30d top 10%": df["rp30_pct"] > 0.9,
    "ATS(BG) > 0.9": df["ats"] > 0.9,
    "LTH 30d change < -1% supply-ish (< -200K BTC)": df["lthchg"] < -200_000,
    # washed-out / bottom-ish
    "STH-MVRV < 0.8": df["sth-mvrv"] < 0.8,
    "MVRV < 1 (price < realized price)": df["mvrv"] < 1,
    "MVRV Z < 0": df["mvrv-zscore"] < 0,
    "NUPL < 0": df["nupl"] < 0,
    "Puell < 0.5": df["puell-multiple"] < 0.5,
    "STH-SOPR 7d < 0.95": df["sthsopr7"] < 0.95,
    "NRPL 30d bottom 10%": df["nrpl30_pct"] < 0.1,
    "ATS(BG) < 0.1": df["ats"] < 0.1,
    "Price < LTH realized price": df.px < df["lth-realized-price"],
    # trend / reclaim
    "Reclaim STH-RP & TMM": above_both,
    "Lose STH-RP": df.px < df["sth-realized-price"],
}
d0 = df[df.index >= a.start]

def events(cond, gap=30):
    c = cond.reindex(d0.index).fillna(False).astype(bool)
    prev = c.shift(1, fill_value=False).rolling(gap, min_periods=1).max().astype(bool)
    # first true day after >= gap days all-false
    return d0.index[c & ~prev]

def summ(ix):
    out = {}
    for h in H:
        r = d0.loc[ix, f"f{h}"].dropna()
        out[h] = (len(r), (r > 0).mean() if len(r) else np.nan, r.median() if len(r) else np.nan)
    return out

base = summ(d0.index)
print(f"Period {d0.index[0].date()} .. {d0.index[-1].date()}  (price to {df.index[-1].date()})")
print("BASE all days: " + "  ".join(f"{h}d win {w:.0%} med {m:+.0%}" for h, (n, w, m) in base.items()))
print(f"\n{'signal':<44}{'n':>3} | {'30d win/med':>13} | {'90d win/med':>13} | {'180d win/med':>14} | now")
for name, cond in S.items():
    ix = events(cond)
    s = summ(ix)
    now = bool(cond.reindex(df.index).iloc[-1]) if len(cond) else None
    cells = [f"{w:>4.0%} {m:+6.0%}" if n else "   -        " for h, (n, w, m) in s.items()]
    print(f"{name:<44}{len(ix):>3} | {cells[0]:>13} | {cells[1]:>13} | {cells[2]:>14} | {'ON' if now else ''}")
    if a.events:
        print("   ", ", ".join(f"{d.date()}({d0.loc[d,'f90']:+.0%})" if pd.notna(d0.loc[d,'f90']) else f"{d.date()}(-)" for d in ix))
last = df.iloc[-1]
print(f"\nNOW {df.index[-1].date()}: px {last.px:,.0f} sth-mvrv {last['sth-mvrv']:.2f} mvrv {last.mvrv:.2f} Z {last['mvrv-zscore']:.2f} nupl {last.nupl:.2f} puell {last['puell-multiple']:.2f} "
      f"sth-sopr7 {last.sthsopr7:.3f} nrpl30 pct {last.nrpl30_pct:.0%} rp30 pct {last.rp30_pct:.0%} ats {last.ats:.2f} lthchg30 {last.lthchg:,.0f}")
