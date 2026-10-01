"""
Backtest: "leverage-driven rally" signal, using cross-venue open interest.

Question: when price rallies hard AND open interest jumps, does it matter whether spot buyers came along?
  A  rally + OI jump + spot CVD <= 0   (perps pushed it, spot did not follow)
  B  rally + OI jump + spot CVD  > 0   (perps and spot together)
  C  rally, OI did NOT jump            (price up without new leverage)
  ALL rally                             (any strong 3-day rally)
  BASE every day

Data (daily, UTC):
  price, spot CVD    Binance spot daily klines (bn_kline_1d); CVD = taker buy - taker sell, quote currency
  open interest      Binance USDT-M (bn_metrics_1h_hist + binance_1h, last value of the day, in coins)
                     + Bybit USDT perp (deriv_oi 1d, USD -> coins at that day's close). Bybit is only collected
                     for CEX_ASSETS; other coins (e.g. ZEC) are Binance-only. OKX (180 days) and Hyperliquid
                     (snapshots only) are too short to include without breaking the series.
Thresholds are expanding percentiles of the coin's own history (no look-ahead): rally = 3-day return in the
top 15%, OI jump = 3-day OI change (coins) in the top 20%. Events closer than 5 days to the previous event of
the same kind are skipped, so one move counts once.

  python3 scripts/research/backtest_oi_signal.py [--assets XRP SOL ZEC BTC ETH] [--rally 0.85] [--oi 0.80]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
H = (1, 3, 7)


def load(con, asset: str) -> tuple[pd.DataFrame, str]:
    px = con.execute("""SELECT CAST(ts AS DATE) d, close, high, low, quote_volume qv, taker_buy_quote tbq
                        FROM bn_kline_1d WHERE symbol = ? AND market = 'spot' ORDER BY 1""", [asset]).df()
    px["d"] = pd.to_datetime(px["d"])
    px = px.set_index("d")
    px["cvd"] = 2 * px["tbq"] - px["qv"]
    bn = con.execute("""SELECT CAST(ts AS DATE) d, last(sum_open_interest ORDER BY ts) oi FROM bn_metrics_1h_hist
                        WHERE symbol = ? GROUP BY 1""", [asset]).df()
    try:
        live = con.execute("""SELECT CAST("timestamp" AS DATE) d, last(sum_open_interest ORDER BY "timestamp") oi
                              FROM binance_1h WHERE symbol = ? AND sum_open_interest IS NOT NULL GROUP BY 1""",
                           [f"{asset}USDT"]).df()
    except Exception:
        live = pd.DataFrame(columns=["d", "oi"])
    bn = pd.concat([bn, live]).assign(d=lambda x: pd.to_datetime(x["d"])).drop_duplicates("d", keep="last").set_index("d")["oi"]
    # Bybit 1d rows are stamped 00:00 = OI at the end of the previous UTC day
    by = con.execute("""SELECT CAST(ts - INTERVAL 1 DAY AS DATE) d, oi_usd FROM deriv_oi
                        WHERE venue = 'bybit' AND interval = '1d' AND symbol = ?""", [asset]).df()
    df = px.join(bn.rename("oi_bn"), how="left")
    venues = "Binance"
    if len(by):
        by["d"] = pd.to_datetime(by["d"])
        df = df.join(by.set_index("d")["oi_usd"].rename("oi_by_usd"), how="left")
        df["oi_by"] = df["oi_by_usd"] / df["close"]
        df["oi"] = df["oi_bn"] + df["oi_by"]          # NaN unless both venues present: no jumps in the series
        venues = "Binance+Bybit"
    else:
        df["oi"] = df["oi_bn"]
    return df, venues


def features(df: pd.DataFrame, rally_q: float, oi_q: float) -> pd.DataFrame:
    df = df[df["oi"].notna()].copy()
    df["r3"] = df["close"] / df["close"].shift(3) - 1
    df["oi3"] = df["oi"] / df["oi"].shift(3) - 1
    df["cvd3"] = df["cvd"].rolling(3).sum() / df["qv"].rolling(3).sum()
    df["r3_pct"] = df["r3"].expanding(180).rank(pct=True)
    df["oi3_pct"] = df["oi3"].expanding(180).rank(pct=True)
    for h in H:
        df[f"f{h}"] = df["close"].shift(-h) / df["close"] - 1
    fut_low = pd.concat([df["low"].shift(-k) for k in range(1, 8)], axis=1).min(axis=1)
    fut_high = pd.concat([df["high"].shift(-k) for k in range(1, 8)], axis=1).max(axis=1)
    df["dd7"] = fut_low / df["close"] - 1
    df["ru7"] = fut_high / df["close"] - 1
    df.loc[df["close"].shift(-7).isna(), ["dd7", "ru7"]] = np.nan
    rally = df["r3_pct"] >= rally_q
    oiup = df["oi3_pct"] >= oi_q
    df["A"] = rally & oiup & (df["cvd3"] <= 0)
    df["B"] = rally & oiup & (df["cvd3"] > 0)
    df["C"] = rally & ~oiup & df["oi3_pct"].notna()
    df["ALL"] = rally
    df["BASE"] = df["r3_pct"].notna()
    return df


def declutter(mask: pd.Series, gap: int = 5) -> pd.Series:
    out, last = pd.Series(False, index=mask.index), None
    for t in mask.index[mask.fillna(False)]:
        if last is None or (t - last).days > gap:
            out[t] = True
            last = t
    return out


def stats(rows: pd.DataFrame) -> dict:
    r = rows.dropna(subset=["f7"])
    if not len(r):
        return {"n": 0}
    return {"n": len(r),
            **{f"avg{h}d": r[f"f{h}"].mean() * 100 for h in H},
            "med7d": r["f7"].median() * 100,
            "down7d%": (r["f7"] < 0).mean() * 100,
            "dd7": r["dd7"].mean() * 100, "ru7": r["ru7"].mean() * 100}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--assets", nargs="+", default=["XRP", "SOL", "ZEC", "BTC", "ETH"])
    ap.add_argument("--rally", type=float, default=0.85)
    ap.add_argument("--oi", type=float, default=0.80)
    ap.add_argument("--db", default=str(ROOT / "data" / "market.duckdb"))
    a = ap.parse_args()
    con = duckdb.connect(a.db, read_only=True)
    pd.set_option("display.width", 200)
    res, events, pooled = [], [], {k: [] for k in ["A", "B", "C", "ALL", "BASE"]}
    for asset in a.assets:
        df, venues = load(con, asset)
        f = features(df, a.rally, a.oi)
        for k in ["A", "B", "C", "ALL", "BASE"]:
            m = f[k] if k == "BASE" else declutter(f[k])
            rows = f[m]
            res.append({"asset": asset, "oi": venues, "since": f.index[f["BASE"]].min().date(), "sig": k, **stats(rows)})
            if venues == "Binance+Bybit":
                pooled[k].append(rows)
            if k == "A":
                for t, r in rows.iterrows():
                    events.append({"asset": asset, "date": t.date(), "r3%": r["r3"] * 100, "oi3%": r["oi3"] * 100,
                                   "cvd3%vol": r["cvd3"] * 100, "f1%": r["f1"] * 100, "f3%": r["f3"] * 100,
                                   "f7%": r["f7"] * 100, "dd7%": r["dd7"] * 100, "ru7%": r["ru7"] * 100})
    for k, parts in pooled.items():
        if parts:
            res.append({"asset": "POOLED*", "oi": "Binance+Bybit", "since": None, "sig": k, **stats(pd.concat(parts))})
    out = pd.DataFrame(res)
    print(out.round(1).to_string(index=False))
    print("\n* POOLED = coins with Binance+Bybit OI\n\nSignal A events:")
    print(pd.DataFrame(events).round(1).to_string(index=False))


if __name__ == "__main__":
    sys.exit(main())
