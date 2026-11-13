"""
Check BTC Open Interest (USD vs BTC coin-denominated) and Long/Short Ratio:
1. Backtest whether high L/S ratio (>2.0, >2.5) is actually bearish or bullish.
2. Track OI in both USD and BTC terms across May 2026 ($82.8k -> $73.6k -> $59k) vs Sept-Nov 2026 ($87.4k -> $73.3k).
3. Check whether OI during the recent drop was newly added counter-trend longs or deleveraging from the $87k peak.
"""
import os
import duckdb
import pandas as pd
import numpy as np

ROOT = "/Users/ivantang/Projects/datahub/binance-data-pipeline"
DB_PATH = os.path.join(ROOT, "data", "market.snapshot.duckdb")
OUT_PATH = os.path.join(ROOT, "data", "outputs", "oi_and_ls_ratio_check_2026-11-11.txt")

con = duckdb.connect(DB_PATH, read_only=True)
lines = []
def log(s=""):
    print(s)
    lines.append(s)

df = con.execute("""
    SELECT ts::DATE as d,
           AVG(count_long_short_ratio) as retail_ls,
           AVG(sum_toptrader_long_short_ratio) as top_pos_ls,
           AVG(count_toptrader_long_short_ratio) as top_acct_ls,
           AVG(sum_open_interest_value)/1e8 as bn_oi_yi,
           AVG(sum_open_interest) as bn_oi_btc
    FROM bn_metrics_1h_hist
    WHERE symbol = 'BTC'
    GROUP BY 1
    ORDER BY 1
""").df()
df["d"] = df["d"].astype(str)

px = con.execute("""
    SELECT ts::DATE as d, close
    FROM bn_kline_1d
    WHERE symbol = 'BTC' AND market = 'spot'
    ORDER BY 1
""").df()
px["d"] = px["d"].astype(str)

oi3 = con.execute("""
    SELECT date::DATE as d,
           SUM(CASE WHEN venue IN ('binance','bybit','okx') THEN oi_usd ELSE 0 END)/1e8 as oi3_yi,
           SUM(oi_usd)/1e8 as oi4_yi
    FROM v_oi_daily_by_venue
    WHERE symbol = 'BTC'
    GROUP BY 1
    ORDER BY 1
""").df()
oi3["d"] = oi3["d"].astype(str)

m = pd.merge(df, px, on="d", how="inner")
m = pd.merge(m, oi3, on="d", how="left").set_index("d")
m["oi3_btc"] = (m["oi3_yi"] * 1e8) / m["close"]

r_min = m["retail_ls"].min()
r_med = m["retail_ls"].median()
r_p80 = m["retail_ls"].quantile(0.8)
r_p90 = m["retail_ls"].quantile(0.9)
r_max = m["retail_ls"].max()
log(f"Date range of bn_metrics_1h_hist: {m.index[0]} to {m.index[-1]} (n={len(m)} days)")
log(f"Retail L/S min={r_min:.2f}, median={r_med:.2f}, p80={r_p80:.2f}, p90={r_p90:.2f}, max={r_max:.2f}")

for h in [7, 14, 30]:
    m[f"fwd_{h}d"] = (m["close"].shift(-h) / m["close"] - 1) * 100

log("\n=== 1. BACKTEST OF BINANCE LONG/SHORT RATIO (ALL DAYS vs BUCKETS) ===")
for label, cond in [
    ("All days", m["retail_ls"] > 0),
    ("Retail L/S < 1.2 (Low)", m["retail_ls"] < 1.2),
    ("Retail L/S 1.2 - 1.8 (Normal)", (m["retail_ls"] >= 1.2) & (m["retail_ls"] < 1.8)),
    ("Retail L/S 1.8 - 2.3 (Elevated)", (m["retail_ls"] >= 1.8) & (m["retail_ls"] < 2.3)),
    ("Retail L/S >= 2.3 (High)", m["retail_ls"] >= 2.3),
    ("Retail L/S >= 2.6 (Extreme High)", m["retail_ls"] >= 2.6),
]:
    sub = m[cond].dropna(subset=["fwd_7d", "fwd_14d", "fwd_30d"])
    if len(sub) == 0:
        continue
    w7 = (sub["fwd_7d"] > 0).mean() * 100
    w14 = (sub["fwd_14d"] > 0).mean() * 100
    w30 = (sub["fwd_30d"] > 0).mean() * 100
    m7 = sub["fwd_7d"].median()
    m14 = sub["fwd_14d"].median()
    m30 = sub["fwd_30d"].median()
    log(f"{label:32s} (n={len(sub):4d}): 7d Win={w7:4.1f}% (Med {m7:+5.1f}%) | 14d Win={w14:4.1f}% (Med {m14:+5.1f}%) | 30d Win={w30:4.1f}% (Med {m30:+5.1f}%)")

log("\n=== 2. EVERY EPISODE WHERE RETAIL L/S EXCEEDED 2.5 (DECLUSTERED 14D) ===")
high_days = m[m["retail_ls"] >= 2.5]
last_dt = None
ep_rows = []
for d, r in high_days.iterrows():
    dt = pd.to_datetime(d)
    if last_dt is None or (dt - last_dt).days >= 14:
        c = r["close"]
        ls = r["retail_ls"]
        f7 = r["fwd_7d"]
        f14 = r["fwd_14d"]
        f30 = r["fwd_30d"]
        log(f"  {d}: Price=${c:,.0f} | L/S={ls:.2f} | Fwd 7d={f7:+.1f}% | Fwd 14d={f14:+.1f}% | Fwd 30d={f30:+.1f}%")
        ep_rows.append(r)
        last_dt = dt

ep_df = pd.DataFrame(ep_rows).dropna(subset=["fwd_7d", "fwd_14d", "fwd_30d"])
w7_n = int((ep_df["fwd_7d"] > 0).sum())
w14_n = int((ep_df["fwd_14d"] > 0).sum())
w30_n = int((ep_df["fwd_30d"] > 0).sum())
log(f"Declustered episodes (n={len(ep_df)}):")
log(f"  7d  Win={(ep_df['fwd_7d']>0).mean()*100:.1f}% ({w7_n}/{len(ep_df)}), Med={ep_df['fwd_7d'].median():+.1f}%")
log(f"  14d Win={(ep_df['fwd_14d']>0).mean()*100:.1f}% ({w14_n}/{len(ep_df)}), Med={ep_df['fwd_14d'].median():+.1f}%")
log(f"  30d Win={(ep_df['fwd_30d']>0).mean()*100:.1f}% ({w30_n}/{len(ep_df)}), Med={ep_df['fwd_30d'].median():+.1f}%")
ep_23_26 = ep_df[ep_df.index >= "2023-01-01"]
w7_23 = int((ep_23_26["fwd_7d"] > 0).sum())
w14_23 = int((ep_23_26["fwd_14d"] > 0).sum())
w30_23 = int((ep_23_26["fwd_30d"] > 0).sum())
log(f"Declustered 2023-2026 episodes (n={len(ep_23_26)}):")
log(f"  7d  Win={(ep_23_26['fwd_7d']>0).mean()*100:.1f}% ({w7_23}/{len(ep_23_26)}), Med={ep_23_26['fwd_7d'].median():+.1f}%")
log(f"  14d Win={(ep_23_26['fwd_14d']>0).mean()*100:.1f}% ({w14_23}/{len(ep_23_26)}), Med={ep_23_26['fwd_14d'].median():+.1f}%")
log(f"  30d Win={(ep_23_26['fwd_30d']>0).mean()*100:.1f}% ({w30_23}/{len(ep_23_26)}), Med={ep_23_26['fwd_30d'].median():+.1f}%")

log("\n=== 3. MAY-JUNE 2026 OI & L/S TRAJECTORY (82.8k -> 73.6k -> 59.1k) ===")
for d in ["2026-05-06", "2026-05-15", "2026-05-20", "2026-05-25", "2026-05-28", "2026-05-31", "2026-06-02", "2026-06-05", "2026-06-15", "2026-06-25"]:
    if d in m.index:
        r = m.loc[d]
        c, ls, tls, b_usd, b_btc, o3_usd, o3_btc = r["close"], r["retail_ls"], r["top_pos_ls"], r["bn_oi_yi"], r["bn_oi_btc"], r["oi3_yi"], r["oi3_btc"]
        log(f"  {d}: Price=${c:,.0f} | L/S={ls:.2f} (TopPos={tls:.2f}) | BnOI=${b_usd:.1f}亿 ({b_btc:,.0f} BTC) | 3ExOI=${o3_usd:.1f}亿 ({o3_btc:,.0f} BTC)")

log("\n=== 4. SEPT-NOV 2026 OI & L/S TRAJECTORY (87.4k -> 73.3k) ===")
for d in ["2026-08-01", "2026-09-01", "2026-09-21", "2026-10-02", "2026-10-10", "2026-10-21", "2026-10-28", "2026-10-30", "2026-11-04", "2026-11-05", "2026-11-08", "2026-11-09", "2026-11-10"]:
    if d in m.index:
        r = m.loc[d]
        c, ls, tls, b_usd, b_btc, o3_usd, o3_btc = r["close"], r["retail_ls"], r["top_pos_ls"], r["bn_oi_yi"], r["bn_oi_btc"], r["oi3_yi"], r["oi3_btc"]
        log(f"  {d}: Price=${c:,.0f} | L/S={ls:.2f} (TopPos={tls:.2f}) | BnOI=${b_usd:.1f}亿 ({b_btc:,.0f} BTC) | 3ExOI=${o3_usd:.1f}亿 ({o3_btc:,.0f} BTC)")

with open(OUT_PATH, "w") as f:
    f.write("\n".join(lines) + "\n")
