"""
Question: What does a severe 30-day drop in total stablecoin supply (e.g., -$14.8B to -$16.7B in Nov 2026,
worse than the -$8.6B drop around the June/July 2026 $58k bottom) mean for BTC forward returns?
Does stablecoin 30-day change lead or lag BTC price bottoms, and which stablecoins (USDT, USDC,
or others) drove the current contraction? Also check @KryptoFynn's claim regarding whale flight
and retail leverage.
Data: DuckDB market.snapshot.duckdb (stablecoin_supply, cex_spot, deriv_oi, deriv_funding, bn_metrics_1h_hist)
Date: 2026-11-12
"""
from pathlib import Path
import duckdb
import pandas as pd
import numpy as np

ROOT = Path(__file__).parents[2]
DB_PATH = ROOT / "data" / "market.snapshot.duckdb"
OUT_PATH = ROOT / "data" / "outputs" / "stablecoin_30d_drop_backtest_2026-11-12.txt"

def main():
    con = duckdb.connect(str(DB_PATH), read_only=True)
    out = []
    def p(s=""):
        print(s)
        out.append(str(s))

    sc = con.execute("""
    SELECT date,
           MAX(CASE WHEN stablecoin='ALL' THEN circulating_usd END)/1e9 AS all_b,
           MAX(CASE WHEN stablecoin='USDT' THEN circulating_usd END)/1e9 AS usdt_b,
           MAX(CASE WHEN stablecoin='USDC' THEN circulating_usd END)/1e9 AS usdc_b
    FROM stablecoin_supply
    GROUP BY date
    ORDER BY date
    """).fetchdf()
    sc["date"] = pd.to_datetime(sc["date"])
    sc = sc.set_index("date").asfreq("D").ffill()
    sc["usdc_b"] = sc["usdc_b"].fillna(0.0)
    sc["other_b"] = sc["all_b"] - sc["usdt_b"] - sc["usdc_b"]

    for col in ["all_b", "usdt_b", "usdc_b", "other_b"]:
        sc[col + "_chg30"] = sc[col] - sc[col].shift(30)
        sc[col + "_pct30"] = (sc[col] / sc[col].shift(30) - 1) * 100
        sc[col + "_chg7"] = sc[col] - sc[col].shift(7)

    btc = con.execute("""
    SELECT CAST(ts AS DATE) AS date, close AS btc
    FROM cex_spot
    WHERE venue='binance' AND symbol='BTC' AND interval='1d'
    ORDER BY date
    """).fetchdf()
    btc["date"] = pd.to_datetime(btc["date"])
    btc = btc.drop_duplicates("date").set_index("date").asfreq("D").ffill()

    # Filter to >= 2020-01-01 where total stablecoin supply >= $5B (avoids 2018-2019 Omni USDT omission noise)
    df = sc.join(btc, how="inner").loc["2020-01-01":].copy()
    for h in [7, 14, 30, 60, 90, 180]:
        df[f"fwd_{h}d"] = (df["btc"].shift(-h) / df["btc"] - 1) * 100
        fwd_min = df["btc"].iloc[::-1].rolling(h, min_periods=1).min().iloc[::-1].shift(-1)
        df[f"mdd_{h}d"] = (fwd_min / df["btc"] - 1) * 100
    df["past_30d_btc"] = (df["btc"] / df["btc"].shift(30) - 1) * 100

    p("================================================================================")
    p("BACKTEST: STABLECOIN SUPPLY 30-DAY CHANGE VS BTC PRICE (2020-2026, ALL >= $5B)")
    p(f"Sample range: {df.index.min().strftime('%Y-%m-%d')} to {df.index.max().strftime('%Y-%m-%d')} (N={len(df)} days)")
    p("================================================================================\n")

    p("=== 1. CURRENT 30-DAY STABLECOIN DROP BREAKDOWN (LAST 15 DAYS) ===")
    cols1 = ["btc", "all_b", "all_b_chg30", "all_b_pct30", "usdt_b_chg30", "usdc_b_chg30", "other_b_chg30", "all_b_chg7"]
    p(df.tail(15)[cols1].round(2).to_string())

    p("\n=== 2. 2026 SUMMER ($58K BOTTOM) VS 2026 NOV (NOW): TIMING OF STABLECOIN 30D DROP ===")
    sub_sum = df.loc["2026-05-25":"2026-08-31"]
    btc_low_dt = sub_sum["btc"].idxmin()
    sc_low_dt = sub_sum["all_b_chg30"].idxmin()
    days_diff = (sc_low_dt - btc_low_dt).days
    pct_vs_low = (sub_sum.loc[sc_low_dt, "btc"] / sub_sum.loc[btc_low_dt, "btc"] - 1) * 100
    p(f"  * 2026 Summer BTC Daily Close Low : {btc_low_dt.strftime('%Y-%m-%d')} at BTC=${sub_sum.loc[btc_low_dt, 'btc']:,.2f}")
    p(f"    -> On {btc_low_dt.strftime('%Y-%m-%d')}, Stablecoin 30d change was {sub_sum.loc[btc_low_dt, 'all_b_chg30']:+.2f}B ({sub_sum.loc[btc_low_dt, 'all_b_pct30']:+.2f}%: USDT={sub_sum.loc[btc_low_dt, 'usdt_b_chg30']:+.2f}B, USDC={sub_sum.loc[btc_low_dt, 'usdc_b_chg30']:+.2f}B, Other={sub_sum.loc[btc_low_dt, 'other_b_chg30']:+.2f}B)")
    p(f"  * 2026 Summer Stablecoin 30d Trough: {sc_low_dt.strftime('%Y-%m-%d')} ({days_diff:+d} days vs BTC low) at SC_chg30={sub_sum.loc[sc_low_dt, 'all_b_chg30']:+.2f}B ({sub_sum.loc[sc_low_dt, 'all_b_pct30']:+.2f}%)")
    p(f"    -> On {sc_low_dt.strftime('%Y-%m-%d')}, BTC was at ${sub_sum.loc[sc_low_dt, 'btc']:,.2f} ({pct_vs_low:+.1f}% vs $58.6k close low), and 60d later BTC was at ${df.loc[sc_low_dt + pd.Timedelta(days=60), 'btc']:,.2f} ({df.loc[sc_low_dt, 'fwd_60d']:+.1f}%)")

    p("\n=== 3. ALL HISTORICAL EPISODES WHERE 30D STABLECOIN CHANGE CROSSED THRESHOLDS (2020-2026) ===")
    for thresh_name, cond in [
        ("SC_chg30 <= -$3.0B", df["all_b_chg30"] <= -3.0),
        ("SC_chg30 <= -$5.0B", df["all_b_chg30"] <= -5.0),
        ("SC_chg30 <= -$8.0B", df["all_b_chg30"] <= -8.0),
        ("SC_pct30 <= -2.5%", df["all_b_pct30"] <= -2.5),
        ("SC_pct30 <= -4.0%", df["all_b_pct30"] <= -4.0),
    ]:
        p(f"\n--- Threshold: {thresh_name} ---")
        sub_all = df[cond]
        p(f"  All-Days count (autocorrelated): {len(sub_all)} / {len(df)} ({len(sub_all)/len(df)*100:.1f}%)")
        for h in [7, 14, 30, 60, 90, 180]:
            v = sub_all[f"fwd_{h}d"].dropna()
            mdd = sub_all.loc[v.index, f"mdd_{h}d"]
            if len(v) > 0:
                p(f"    All-Days {h:3d}d: N={len(v):3d} | Win={(v>0).mean()*100:5.1f}% | Mean={v.mean():+6.1f}% | Med={v.median():+6.1f}% | MedMDD={mdd.median():+6.1f}% | WorstMDD={mdd.min():+6.1f}%")

        eps = []
        in_ep = False
        s_dt = None
        prev_dt = None
        for dt, val in cond.items():
            if val and not in_ep:
                in_ep = True
                s_dt = dt
            elif not val and in_ep:
                in_ep = False
                eps.append((s_dt, prev_dt))
            if val:
                prev_dt = dt
        if in_ep:
            eps.append((s_dt, prev_dt))
        merged = []
        for s, e in eps:
            if merged and (s - merged[-1][1]).days <= 14:
                merged[-1] = (merged[-1][0], e)
            else:
                merged.append((s, e))

        p(f"  Distinct Episodes (gap > 14d): {len(merged)} episodes")
        first_rows = []
        trough_rows = []
        for i, (s, e) in enumerate(merged, 1):
            ep_df = df.loc[s:e]
            t_dt = ep_df["all_b_chg30"].idxmin()
            rs = df.loc[s]
            rt = df.loc[t_dt]
            first_rows.append(rs)
            trough_rows.append(rt)
            p(f"    Ep#{i:02d}: {s.strftime('%Y-%m-%d')}..{e.strftime('%Y-%m-%d')} ({len(ep_df):2d}d)")
            p(f"      [First-Entry {s.strftime('%Y-%m-%d')}] BTC=${rs['btc']:,.0f} (past30d={rs['past_30d_btc']:+.1f}%) | SC_chg30={rs['all_b_chg30']:+.2f}B ({rs['all_b_pct30']:+.1f}%) | 30d={rs['fwd_30d']:+.1f}% 60d={rs['fwd_60d']:+.1f}% 90d={rs['fwd_90d']:+.1f}% 180d={rs['fwd_180d']:+.1f}% | 90d_MDD={rs['mdd_90d']:+.1f}%")
            p(f"      [SC-Trough   {t_dt.strftime('%Y-%m-%d')}] BTC=${rt['btc']:,.0f} (past30d={rt['past_30d_btc']:+.1f}%) | SC_chg30={rt['all_b_chg30']:+.2f}B ({rt['all_b_pct30']:+.1f}%, USDT={rt['usdt_b_chg30']:+.2f}B, USDC={rt['usdc_b_chg30']:+.2f}B, Oth={rt['other_b_chg30']:+.2f}B) | 30d={rt['fwd_30d']:+.1f}% 60d={rt['fwd_60d']:+.1f}% 90d={rt['fwd_90d']:+.1f}% 180d={rt['fwd_180d']:+.1f}% | 90d_MDD={rt['mdd_90d']:+.1f}%")

        if first_rows:
            fdf = pd.DataFrame(first_rows)
            tdf = pd.DataFrame(trough_rows)
            p("  [Episode Summary — First-Entry Day (No Lookahead)]:")
            for h in [14, 30, 60, 90, 180]:
                v = fdf[f"fwd_{h}d"].dropna()
                mdd = fdf.loc[v.index, f"mdd_{h}d"]
                if len(v) > 0:
                    p(f"    First-Entry {h:3d}d: N={len(v):2d} | Win={(v>0).mean()*100:5.1f}% | Mean={v.mean():+6.1f}% | Med={v.median():+6.1f}% | MedMDD={mdd.median():+6.1f}% | WorstMDD={mdd.min():+6.1f}%")
            p("  [Episode Summary — Trough Day of SC_chg30 (In-Hindsight Trough, Has Lookahead)]:")
            for h in [14, 30, 60, 90, 180]:
                v = tdf[f"fwd_{h}d"].dropna()
                mdd = tdf.loc[v.index, f"mdd_{h}d"]
                if len(v) > 0:
                    p(f"    SC-Trough   {h:3d}d: N={len(v):2d} | Win={(v>0).mean()*100:5.1f}% | Mean={v.mean():+6.1f}% | Med={v.median():+6.1f}% | MedMDD={mdd.median():+6.1f}% | WorstMDD={mdd.min():+6.1f}%")

    p("\n=== 4. FACT-CHECKING @KryptoFynn's REPLY ('Whales fleeing at unprecedented rate, retail all-in leveraged long') ===")
    ls = con.execute("""
    SELECT CAST(ts AS DATE) AS d,
           AVG(count_long_short_ratio) AS retail_acct_ls,
           AVG(sum_toptrader_long_short_ratio) AS top_pos_ls,
           AVG(sum_open_interest_value)/1e8 AS bn_oi_yi
    FROM bn_metrics_1h_hist
    WHERE symbol='BTC'
    GROUP BY 1
    ORDER BY 1 DESC
    LIMIT 12
    """).fetchdf()
    p("Recent Binance BTC Long/Short Ratios & OI (symbol='BTC'):")
    p(ls.round(3).to_string())

    OUT_PATH.write_text("\n".join(out), encoding="utf-8")
    print(f"\nSaved to {OUT_PATH}")

if __name__ == "__main__":
    main()
