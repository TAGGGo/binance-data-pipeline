"""
10-signal BTC bull/bear score (0-10), modelled on the composite @Mrluanluan showed (Article 2026-08-20:
"10 项链上/市场信号合成的 0-10 综合分 (网络活跃·盈利·需求·流动性·动量)", >=4 buy / <4 sell). His exact 10 inputs
are not published, so these are OUR picks, fixed in advance with folklore thresholds (no tuning):

  network   S1 active addresses 30d avg > 365d avg      S2 tx count 30d avg > 365d avg        (Coin Metrics)
  profit    S3 price > STH realized price               S4 STH-SOPR 30d avg > 1               (BGeometrics)
  demand    S5 price > True Market Mean                 S6 realized price up over 30 days     (BGeometrics)
  liquidity S7 stablecoin supply up over 30 days        S8 DXY below its 200-day average      (DefiLlama, TradingView)
  momentum  S9 price > 200-day average                  S10 50-day average > 200-day average

Backtest: hold BTC the day after the close where score >= K, else cash; 0.1% cost per switch.
--lag N delays the BGeometrics signals (S3-S6) by N days (free plan withholds the newest ~7 days).
Usage: python3 scripts/backtest_onchain_score.py [--lag 7] [--db data/market.snapshot.duckdb]
"""
from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BG = ROOT / "data/onchain/bgeometrics"


def bg(name):
    d = pd.read_csv(BG / f"{name}.csv", index_col=0, parse_dates=True)
    return pd.to_numeric(d.iloc[:, 0], errors="coerce")


def load(db):
    c = duckdb.connect(str(db), read_only=True)
    oc = c.execute("SELECT date, metric, value FROM onchain_daily WHERE asset='BTC'").df()
    oc = oc.pivot(index="date", columns="metric", values="value")
    oc.index = pd.to_datetime(oc.index)
    m = c.execute("SELECT date, stablecoin_mcap, dxy FROM v_macro_daily").df().set_index("date")
    m.index = pd.to_datetime(m.index)
    px = c.execute("SELECT CAST(ts AS DATE) AS date, close FROM bn_kline_1d WHERE symbol='BTC' AND market='spot'").df().set_index("date")["close"]
    px.index = pd.to_datetime(px.index)
    p = oc["PriceUSD"].combine_first(bg("btc-price").replace(0, np.nan))
    p = p.combine_first(px) if p.index.max() < px.index.max() else p
    idx = pd.date_range("2011-01-01", p.index.max(), freq="D")
    df = pd.DataFrame(index=idx)
    df["p"] = p.reindex(idx).ffill()
    df["aa"] = oc["AdrActCnt"].reindex(idx)
    df["tx"] = oc["TxCnt"].reindex(idx)
    df["sth_rp"] = bg("sth-realized-price").reindex(idx)
    df["sth_sopr"] = bg("sth-sopr").reindex(idx)
    df["tmm"] = bg("true-market-mean").reindex(idx)
    df["rp"] = bg("realized-price").reindex(idx)
    df["stable"] = m["stablecoin_mcap"].reindex(idx).ffill(limit=5)
    df["dxy"] = m["dxy"].reindex(idx).ffill(limit=5)
    return df


def signals(df, lag=0, no_stable=False):
    s = pd.DataFrame(index=df.index)
    sh = lambda x: x.shift(lag)
    s["S1"] = df.aa.rolling(30).mean() > df.aa.rolling(365).mean()
    s["S2"] = df.tx.rolling(30).mean() > df.tx.rolling(365).mean()
    s["S3"] = df.p > sh(df.sth_rp)
    s["S4"] = sh(df.sth_sopr.rolling(30).mean()) > 1
    s["S5"] = df.p > sh(df.tmm)
    s["S6"] = sh(df.rp) > sh(df.rp.shift(30))
    s["S7"] = df.stable > df.stable.shift(30)
    s["S8"] = df.dxy < df.dxy.rolling(200).mean()
    s["S9"] = df.p > df.p.rolling(200).mean()
    s["S10"] = df.p.rolling(50).mean() > df.p.rolling(200).mean()
    need = {"S1": df.aa.rolling(365).mean(), "S2": df.tx.rolling(365).mean(), "S3": sh(df.sth_rp), "S4": sh(df.sth_sopr),
            "S5": sh(df.tmm), "S6": sh(df.rp.shift(30)), "S7": df.stable.shift(30), "S8": df.dxy.rolling(200).mean(),
            "S9": df.p.rolling(200).mean(), "S10": df.p.rolling(200).mean()}
    if no_stable:
        s = s.drop(columns="S7"); need.pop("S7")
    ok = pd.concat([v.notna() for v in need.values()], axis=1).all(axis=1)
    sc = s.astype(int).sum(axis=1).where(ok)
    return s.astype(int), sc


def strat(p, sc, k, start, cost=0.001):
    r = p.pct_change()
    pos = (sc >= k).astype(float).shift(1)
    x = pd.DataFrame({"r": r, "pos": pos}).loc[start:].dropna()
    sw = x.pos.diff().abs().fillna(0)
    eq = (1 + x.pos * x.r - sw * cost).cumprod()
    bh = (1 + x.r).cumprod()
    yrs = len(x) / 365
    st = lambda e, rr: {"mult": e.iloc[-1], "cagr": e.iloc[-1] ** (1 / yrs) - 1,
                        "sharpe": rr.mean() / rr.std() * np.sqrt(365), "mdd": (e / e.cummax() - 1).min()}
    a = st(eq, x.pos * x.r - sw * cost)
    b = st(bh, x.r)
    a["in"] = x.pos.mean()
    a["trades"] = int((x.pos.diff() > 0).sum())
    return a, b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data/market.snapshot.duckdb"))
    ap.add_argument("--lag", type=int, default=0)
    ap.add_argument("--no-stable", action="store_true", help="drop S7 (stablecoins, only from 2017-11) to test from 2013; K is then out of 9")
    a = ap.parse_args()
    df = load(a.db)
    S, sc = signals(df, a.lag, a.no_stable)
    print(f"lag={a.lag}d on BGeometrics signals; score available from {sc.first_valid_index().date()}")
    for start in (["2014-01-01", "2018-01-01"] if a.no_stable else ["2018-01-01", "2020-01-02", "2023-01-01"]):
        rows = []
        for k in range(3, 8):
            st, bh = strat(df.p, sc, k, start)
            rows.append({"K": f">={k}", **{kk: st[kk] for kk in ["mult", "cagr", "sharpe", "mdd", "in", "trades"]}})
        rows.append({"K": "buy&hold", **{kk: bh[kk] for kk in ["mult", "cagr", "sharpe", "mdd"]}, "in": 1.0, "trades": 0})
        t = pd.DataFrame(rows).set_index("K")
        t["mult"] = t["mult"].round(1); t["cagr"] = (t["cagr"] * 100).round(0); t["sharpe"] = t["sharpe"].round(2)
        t["mdd"] = (t["mdd"] * 100).round(0); t["in"] = (t["in"] * 100).round(0)
        print(f"\n== from {start} ==\n{t.to_string()}")
    # events: score crosses up to >=4 (first time after >=30 days below)
    p = df.p
    up = (sc >= 4) & (sc.shift(1) < 4)
    ev, last = [], None
    for d in up[up].index:
        if last is None or (d - last).days >= 30:
            if (sc.loc[d - pd.Timedelta(days=30):d - pd.Timedelta(days=1)] < 4).all():
                ev.append(d)
        last = d
    fw = lambda d, n: p.get(d + pd.Timedelta(days=n), np.nan) / p[d] - 1
    E = pd.DataFrame({"px": [p[d] for d in ev], **{f"r{n}": [fw(d, n) for d in ev] for n in (30, 90, 180)}}, index=[d.date() for d in ev])
    base = {n: (p.shift(-n) / p - 1).loc[sc.first_valid_index():] for n in (30, 90, 180)}
    print("\n== score crosses up to >=4 after 30+ days below ==")
    print((E[["r30", "r90", "r180"]] * 100).round(0).assign(px=E.px.round(0)).to_string())
    for n in (30, 90, 180):
        x = E[f"r{n}"].dropna()
        print(f"  {n}d: median {x.median()*100:+.0f}% (up {int((x>0).mean()*100)}%, n={len(x)}) vs base median {base[n].median()*100:+.0f}% (up {int((base[n]>0).mean()*100)}%)")
    print("\n== latest ==")
    last = sc.last_valid_index()
    print(last.date(), "score", int(sc[last]), dict(S.loc[last]))
    print("last 40 days score:", "".join("-" if pd.isna(v) else str(int(v)) for v in sc.iloc[-40:]))


if __name__ == "__main__":
    main()
