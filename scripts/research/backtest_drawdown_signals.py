"""
Which signals come before deeper pullbacks?

For each coin page coin (Binance data; Bybit funding where collected), daily UTC. Each signal is scored on what
happens next, compared with that coin's own normal:
  deep7%     share of events followed by a 7-day drawdown in the coin's worst 20% (normal = 20%)
  dd7x/dd14x average max drawdown over the next 7/14 days minus the coin's average (negative = deeper than usual)
  ru14x      same for the max run-up (to see whether a signal just means "bigger moves both ways")
  f14        average 14-day return
Thresholds are expanding percentiles of each coin's own history (min 180 days, no look-ahead). Events of the
same signal on the same coin closer than 7 days count once.

  python3 scripts/research/backtest_drawdown_signals.py [--coins BTC ETH ...]
"""
from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
COINS = ["BTC", "ETH", "XRP", "SOL", "ZEC", "DASH", "ZEN", "LDO", "ENA", "AAVE", "SUI", "UNI", "NEAR", "DOGE"]


def pct(s: pd.Series) -> pd.Series:
    return s.expanding(180).rank(pct=True)


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def load(con, a: str) -> pd.DataFrame:
    k = con.execute("""SELECT CAST(ts AS DATE) d, market, close, high, low, quote_volume qv, taker_buy_quote tbq
                       FROM bn_kline_1d WHERE symbol = ?""", [a]).df()
    k["d"] = pd.to_datetime(k["d"])
    spot = k[k.market == "spot"].set_index("d").sort_index()
    perp = k[k.market == "perp"].set_index("d")["close"].rename("perp")
    m = con.execute("""SELECT CAST(ts AS DATE) d, last(sum_open_interest ORDER BY ts) oi,
                              last(count_long_short_ratio ORDER BY ts) ls, last(sum_toptrader_long_short_ratio ORDER BY ts) ls_top
                       FROM bn_metrics_1h_hist WHERE symbol = ? GROUP BY 1""", [a]).df()
    m["d"] = pd.to_datetime(m["d"])
    f = con.execute("""SELECT CAST(ts AS DATE) d, venue, avg(funding_rate * 8 / interval_hours) * 1e4 bp
                       FROM deriv_funding WHERE symbol = ? AND venue IN ('binance', 'bybit') GROUP BY 1, 2""", [a]).df()
    f["d"] = pd.to_datetime(f["d"])
    fb = f[f.venue == "binance"].set_index("d")["bp"].rename("fund")
    fy = f[f.venue == "bybit"].set_index("d")["bp"].rename("fund_by")
    df = spot[["close", "high", "low", "qv", "tbq"]].join([perp, m.set_index("d"), fb, fy], how="left")
    return df[df["oi"].notna() | (df.index >= m["d"].min() if len(m) else False)]


def features(df: pd.DataFrame) -> pd.DataFrame:
    c = df["close"]
    x = pd.DataFrame(index=df.index)
    x["rsi"] = rsi(c)
    x["ext50"] = c / c.rolling(50).mean() - 1
    x["r7"] = c / c.shift(7) - 1
    x["rv"] = np.log(c).diff().rolling(14).std()
    x["fund3"] = df["fund"].rolling(3, min_periods=2).mean()
    x["fund3_by"] = df["fund_by"].rolling(3, min_periods=2).mean()
    x["basis3"] = (df["perp"] / c - 1).rolling(3).mean() * 1e4
    x["oi30"] = df["oi"] / df["oi"].shift(30) - 1
    x["oi_hi"] = (df["oi"] >= df["oi"].rolling(90).max() * 0.999) & (c >= c.rolling(90).max() * 0.95)
    x["ls"] = df["ls"]
    x["cvd3"] = (2 * df["tbq"] - df["qv"]).rolling(3).sum() / df["qv"].rolling(3).sum()
    P = {k: pct(x[k]) for k in ["ext50", "r7", "rv", "fund3", "fund3_by", "basis3", "oi30", "ls"]}
    S = {
        "Funding high (top 10%, >1.5bp)": (P["fund3"] >= .9) & (x["fund3"] > 1.5),
        "Funding high on Binance AND Bybit": (P["fund3"] >= .9) & (P["fund3_by"] >= .9) & (x["fund3"] > 1.5),
        "Perp premium/basis high (top 10%)": P["basis3"] >= .9,
        "RSI > 75": x["rsi"] > 75,
        "Far above 50d MA (top 10%)": P["ext50"] >= .9,
        "7d rally (top 5%)": P["r7"] >= .95,
        "OI at 90d high + price near 90d high": x["oi_hi"] & P["ext50"].notna(),
        "OI 30d growth (top 10%)": P["oi30"] >= .9,
        "Retail long/short ratio high (top 10%)": P["ls"] >= .9,
        "Retail long/short ratio low (bottom 10%)": P["ls"] <= .1,
        "Stretched + funding high (ext50 top10 & fund top20)": (P["ext50"] >= .9) & (P["fund3"] >= .8),
        "Stretched + OI growth (ext50 top10 & oi30 top10)": (P["ext50"] >= .9) & (P["oi30"] >= .9),
        "Stretched + OI + funding (all top 20%)": (P["ext50"] >= .8) & (P["oi30"] >= .8) & (P["fund3"] >= .8),
        "Rally + spot selling (r7 top10, cvd3<0)": (P["r7"] >= .9) & (x["cvd3"] < 0),
        "[control] high volatility (top 20%)": P["rv"] >= .8,
        "[control] after 7d crash (bottom 5%)": P["r7"] <= .05,
    }
    lo = pd.concat([df["low"].shift(-k) for k in range(1, 15)], axis=1)
    hi = pd.concat([df["high"].shift(-k) for k in range(1, 15)], axis=1)
    out = pd.DataFrame(index=df.index)
    out["dd7"] = lo.iloc[:, :7].min(axis=1) / c - 1
    out["dd14"] = lo.min(axis=1) / c - 1
    out["ru14"] = hi.max(axis=1) / c - 1
    out["f14"] = c.shift(-14) / c - 1
    out.loc[c.shift(-14).isna(), :] = np.nan
    out["valid"] = P["ext50"].notna() & out["dd14"].notna()
    return out, S


def declutter(mask: pd.Series, gap: int = 7) -> pd.Series:
    keep, last = [], None
    for t in mask.index[mask.fillna(False).astype(bool)]:
        if last is None or (t - last).days > gap:
            keep.append(t)
            last = t
    return pd.Series(mask.index.isin(keep), index=mask.index)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--coins", nargs="+", default=COINS)
    ap.add_argument("--db", default=str(ROOT / "data" / "market.duckdb"))
    a = ap.parse_args()
    con = duckdb.connect(a.db, read_only=True)
    rows = {}
    for coin in a.coins:
        df = load(con, coin)
        out, S = features(df)
        v = out[out["valid"]]
        if len(v) < 200:
            continue
        q20 = v["dd7"].quantile(0.2)
        base = v[["dd7", "dd14", "ru14"]].mean()
        for name, m in S.items():
            ev = declutter(m & out["valid"])
            e = out[ev]
            if not len(e):
                continue
            r = pd.DataFrame({"coin": coin, "deep": (e["dd7"] <= q20).astype(float),
                              "dd7x": e["dd7"] - base["dd7"], "dd14x": e["dd14"] - base["dd14"],
                              "ru14x": e["ru14"] - base["ru14"], "f14": e["f14"], "dd14": e["dd14"]})
            rows.setdefault(name, []).append(r)
    res = []
    for name, parts in rows.items():
        r = pd.concat(parts)
        p = r["deep"].mean()
        res.append({"signal": name, "n": len(r), "coins": r["coin"].nunique(),
                    "deep7%": p * 100, "±": 100 * np.sqrt(p * (1 - p) / len(r)),
                    "dd7x": r["dd7x"].mean() * 100, "dd14x": r["dd14x"].mean() * 100,
                    "ru14x": r["ru14x"].mean() * 100, "f14": r["f14"].mean() * 100, "f14med": r["f14"].median() * 100,
                    "dd14<-20%": (r["dd14"] <= -0.2).mean() * 100})
    out = pd.DataFrame(res).sort_values("deep7%", ascending=False)
    pd.set_option("display.width", 220)
    print(out.round(1).to_string(index=False))
    print("\nnormal: deep7% = 20; dd7x/dd14x/ru14x = 0 (in % points vs the coin's own average)")


if __name__ == "__main__":
    main()
