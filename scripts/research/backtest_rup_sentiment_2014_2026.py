#!/usr/bin/env python3
"""
Backtest of Murphy's (@Murphychen888) "BTC: 市场情绪压力监测" (Glassnode Relative Unrealized Profit, RUP)
Question:
  1. Does RUP = Unrealized Profit / Market Cap match Murphy's 12-year chart (2014-2026), including the
     orange threshold line at 0.375 ("恐慌" blue zone) and red threshold around 0.350 ("绝望/投降" red zone)?
  2. What are the actual historical forward returns (7d, 14d, 30d, 60d, 90d, 180d, 365d), win rates, and
     maximum subsequent drawdowns when:
     (a) RUP drops into the Red Zone (< 0.350, and sensitivity 0.345 / 0.352)
     (b) RUP drops into the Blue Zone (< 0.375, and 0.350 <= RUP < 0.375, which is the current Nov 2026 state)
     (c) First entry day of each cluster vs all days in the zone
Data:
  - ~/Projects/btc-volatility/urpd/deltas.parquet (2010-08-01..2026-10-06) + data/onchain/node_urpd/deltas.csv (2026-10-07..2026-11-10)
  - data/onchain/self/urpd_metrics_daily.csv (daily close, supply, realized_price, mvrv, psip)
Date: 2026-11-11
"""

import csv
import math
import os
import duckdb
import numpy as np
import pandas as pd

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ON = os.path.join(REPO, "data", "onchain")
ND = os.path.join(ON, "node_urpd")
OUT_CSV = os.path.join(ON, "self", "rup_daily.csv")
OUT_TXT = os.path.join(REPO, "data", "outputs", "backtest_rup_sentiment_2014_2026.txt")
HIST = os.path.expanduser("~/Projects/btc-volatility/urpd/deltas.parquet")
SWITCH = "2026-10-07"


def build_rup_series():
    con = duckdb.connect()
    hist = con.execute(
        f"select d::varchar, bin, sum(delta)::double from read_parquet('{HIST}') where d < '{SWITCH}' group by 1,2 order by 1"
    ).fetchall()
    byday = {}
    for d, b, v in hist:
        byday.setdefault(d, []).append((int(b), v))

    seen = {}
    with open(os.path.join(ND, "deltas.csv")) as f:
        for r in csv.DictReader(f):
            if r["day"] >= SWITCH:
                seen[(r["h_end"], r["day"], int(r["bin"]))] = float(r["delta"])
    nd = {}
    for (_, d, b), v in seen.items():
        nd.setdefault(d, {})
        nd[d][b] = nd[d].get(b, 0.0) + v
    for d, m in nd.items():
        byday[d] = list(m.items())

    u_df = pd.read_csv(os.path.join(ON, "self", "urpd_metrics_daily.csv"))
    px = dict(zip(u_df["d"], u_df["close"]))
    psip_map = dict(zip(u_df["d"], u_df["psip"]))
    mvrv_map = dict(zip(u_df["d"], u_df["mvrv"]))

    urpd = {}
    rows = []
    for d in sorted(byday):
        for b, v in byday[d]:
            urpd[b] = urpd.get(b, 0.0) + v
        if d < "2010-08-01" or d not in px:
            continue
        p = px[d]
        sup = up = ul = 0.0
        for k, v in urpd.items():
            if v <= 0:
                continue
            sup += v
            cost = math.exp((k + 0.5) * 0.01)
            if cost < p:
                up += v * (p - cost)
            else:
                ul += v * (cost - p)
        mc = sup * p
        rup = up / mc
        rul = ul / mc
        nupl = (up - ul) / mc
        rows.append(
            {
                "d": d,
                "close": round(p, 2),
                "rup": round(rup, 6),
                "rul": round(rul, 6),
                "nupl": round(nupl, 6),
                "psip": psip_map.get(d, np.nan),
                "mvrv": mvrv_map.get(d, np.nan),
            }
        )

    df = pd.DataFrame(rows)
    df.to_csv(OUT_CSV, index=False)
    return df


def main():
    os.makedirs(os.path.dirname(OUT_TXT), exist_ok=True)
    df = build_rup_series()
    df = df[df["d"] >= "2014-01-01"].reset_index(drop=True)

    horizons = [7, 14, 30, 60, 90, 180, 365]
    closes = df["close"].values
    n = len(df)
    for h in horizons:
        fwd = np.full(n, np.nan)
        max_dd = np.full(n, np.nan)
        for i in range(n):
            if i + h < n:
                fwd[i] = (closes[i + h] / closes[i] - 1.0) * 100.0
                min_p = np.min(closes[i + 1 : i + h + 1])
                max_dd[i] = (min_p / closes[i] - 1.0) * 100.0
        df[f"ret_{h}d"] = fwd
        df[f"mdd_{h}d"] = max_dd

    lines = []
    def p(*args):
        s = " ".join(str(a) for a in args)
        print(s)
        lines.append(s)

    p("=" * 80)
    p("BACKTEST: BTC Relative Unrealized Profit (RUP) — Murphy's '市场情绪压力监测'")
    p("Period: 2014-01-01 to 2026-11-10 (N =", len(df), "days)")
    p("=" * 80)

    # 1. Check 2026 summer red days count across thresholds
    p("\n[1] 2026 Threshold Calibration (Checking Murphy's '7d + 4d = 11 days in Red Zone'):")
    df26 = df[df["d"] >= "2026-01-01"]
    for th in [0.345, 0.348, 0.349, 0.350, 0.351, 0.352, 0.355]:
        sub = df26[df26["rup"] < th]
        p(f"  Threshold RUP < {th:.3f}: {len(sub):2d} days in 2026 -> dates: {list(sub['d'].values)}")

    # 2. Cycle-by-cycle summary of Red Zone (< 0.350) and Blue Zone (< 0.375)
    p("\n[2] Historical Cycles Where RUP Dropped Below 0.375 (Blue) and 0.350 (Red):")
    periods = [
        ("2014-2016 Bear", "2014-01-01", "2016-12-31"),
        ("2018-2019 Bear", "2018-01-01", "2019-12-31"),
        ("2020-03 Crash", "2020-01-01", "2020-12-31"),
        ("2022-2023 Bear", "2022-01-01", "2023-12-31"),
        ("2026 Cycle", "2026-01-01", "2026-11-10"),
    ]
    for label, s_dt, e_dt in periods:
        sub = df[(df["d"] >= s_dt) & (df["d"] <= e_dt)]
        blue = sub[sub["rup"] < 0.375]
        red = sub[sub["rup"] < 0.350]
        min_row = sub.loc[sub["rup"].idxmin()]
        p(
            f"  {label:16s}: Blue(<0.375)={len(blue):3d}d | Red(<0.350)={len(red):3d}d | "
            f"Min RUP={min_row['rup']:.4f} on {min_row['d']} (${min_row['close']:,.0f})"
        )

    # 3. Episode / Cluster analysis (first day entering zone vs subsequent path)
    def get_episodes(mask, gap_days=14):
        idx = np.where(mask)[0]
        if len(idx) == 0:
            return []
        eps = []
        start = idx[0]
        prev = idx[0]
        for i in idx[1:]:
            if i - prev > gap_days:
                eps.append((start, prev))
                start = i
            prev = i
        eps.append((start, prev))
        return eps

    for zone_name, mask_fn in [
        ("RED ZONE (RUP < 0.350 — '绝望/投降')", lambda d: d["rup"] < 0.350),
        ("BLUE ZONE ALL (RUP < 0.375 — '恐慌+绝望')", lambda d: d["rup"] < 0.375),
        ("BLUE ONLY (0.350 <= RUP < 0.375 — '纯蓝色恐慌区, 当前状态')", lambda d: (d["rup"] >= 0.350) & (d["rup"] < 0.375)),
    ]:
        p("\n" + "=" * 80)
        p(f"ZONE ANALYSIS: {zone_name}")
        p("=" * 80)
        mask = mask_fn(df).values
        sub = df[mask]
        p(f"Total days in zone (2014-2026): {len(sub)} / {len(df)} ({len(sub)/len(df)*100:.1f}%)")
        for h in horizons:
            valid = sub[f"ret_{h}d"].dropna()
            mdd = sub[f"mdd_{h}d"].dropna()
            if len(valid) > 0:
                win = (valid > 0).mean() * 100.0
                p(
                    f"  All-Days {h:3d}d: N={len(valid):3d} | Win={win:5.1f}% | "
                    f"Mean={valid.mean():+6.1f}% | Med={valid.median():+6.1f}% | "
                    f"MedMDD={mdd.median():+5.1f}% | WorstMDD={mdd.min():+5.1f}%"
                )

        eps = get_episodes(mask, gap_days=10)
        p(f"\nDistinct Episodes (gap > 10d): {len(eps)} episodes")
        for k, (s_i, e_i) in enumerate(eps, 1):
            r0 = df.iloc[s_i]
            r_end = df.iloc[e_i]
            sub_ep = df.iloc[s_i : e_i + 1]
            r_min = sub_ep.loc[sub_ep["close"].idxmin()]
            # Max drawdown within 90d of FIRST entry day s_i
            end_90 = min(n, s_i + 91)
            min_90_p = np.min(closes[s_i:end_90])
            dd_90 = (min_90_p / r0["close"] - 1.0) * 100.0
            ret30 = f"{r0['ret_30d']:+.1f}%" if not np.isnan(r0["ret_30d"]) else "N/A"
            ret90 = f"{r0['ret_90d']:+.1f}%" if not np.isnan(r0["ret_90d"]) else "N/A"
            ret180 = f"{r0['ret_180d']:+.1f}%" if not np.isnan(r0["ret_180d"]) else "N/A"
            ret365 = f"{r0['ret_365d']:+.1f}%" if not np.isnan(r0["ret_365d"]) else "N/A"
            p(
                f"  Ep#{k:02d}: {r0['d']}..{r_end['d']} ({e_i-s_i+1:3d}d) | "
                f"Entry=${r0['close']:,.0f} (RUP={r0['rup']:.4f}) | "
                f"EpLow=${r_min['close']:,.0f} | 90d_MDD={dd_90:+.1f}% | "
                f"30d={ret30} 90d={ret90} 180d={ret180} 365d={ret365}"
            )

        # Summary of First-Entry win rates across completed episodes
        first_rows = df.iloc[[s_i for s_i, _ in eps]]
        p("\n  [First-Entry Day Summary Across Episodes]:")
        for h in [7, 14, 30, 60, 90, 180, 365]:
            v = first_rows[f"ret_{h}d"].dropna()
            m = first_rows[f"mdd_{h}d"].dropna()
            if len(v) > 0:
                p(
                    f"    First-Entry {h:3d}d: N={len(v):2d} | Win={(v>0).mean()*100:5.1f}% | "
                    f"Mean={v.mean():+6.1f}% | Med={v.median():+6.1f}% | "
                    f"MedMDD={m.median():+5.1f}% | WorstMDD={m.min():+5.1f}%"
                )

    with open(OUT_TXT, "w") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
