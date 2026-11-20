"""
Backtest of the viral Douyin blogger (@文主任) Gold Position Sizing Strategy.

Question:
  Does the gold position formula:
    Gold_Weight = ((Mom_Score + Fed_Score) / 2) * Vol_Coef
  where:
    - Mom_Score = 0.5 * I(Gold_6m_ret > Treasury_6m_ret) + 0.5 * I(Gold_12m_ret > Treasury_12m_ret)
    - Fed_Score = 1 if US_2Y_Yield < Fed_Funds_Rate else 0
    - Vol_Coef  = min(1.0, 15% / Gold_Recent_Annualized_Vol)
  outperform Buy-and-Hold Gold, a static 50/50 Gold+Cash portfolio, or simple trend following
  (e.g., 200-day SMA / 12-month absolute momentum) across full multi-decade macro cycles?
  Furthermore, does each component (Mom_Score, Fed_Score, Vol_Coef) actually add value, or does
  one component drag the strategy down?

Data:
  - FRED DGS2 (2-Year Treasury Constant Maturity Rate, 1976-06 to 2026-11)
  - FRED DFF (Effective Federal Funds Rate, 1954-07 to 2026-11)
  - TradingView TVC:GOLD daily spot gold price (1976-06 to 2026-11)
  - Yahoo Finance GLD (Gold ETF, 2004-11 to 2026-11) and SHY (1-3Y Treasury ETF total return, 2002-07 to 2026-11)

Date: 2026-11-20
"""
import datetime
import os
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).parents[2]
DB_PATH = ROOT / "data" / "market.snapshot.duckdb"
OUT_PATH = ROOT / "data" / "outputs" / "backtest_gold_position_strategy_20261120.txt"


def fetch_yahoo(symbol: str) -> pd.DataFrame:
    headers = {"User-Agent": "Mozilla/5.0"}
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&period1=-631152000&period2=2000000000"
    r = requests.get(url, headers=headers, timeout=15)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    ts = res["timestamp"]
    quote = res["indicators"]["quote"][0]
    adj = res["indicators"].get("adjclose", [{}])[0].get("adjclose", quote["close"])
    dates = [datetime.datetime.utcfromtimestamp(t).date() for t in ts]
    df = pd.DataFrame({"date": pd.to_datetime(dates), "close": quote["close"], "adjclose": adj})
    df = df.dropna().drop_duplicates(subset=["date"]).set_index("date").sort_index()
    return df


def fetch_tv_gold() -> pd.DataFrame:
    os.environ["TZ"] = "UTC"
    time.tzset()
    from tvDatafeed import Interval, TvDatafeed
    tv = TvDatafeed()
    df = tv.get_hist(symbol="GOLD", exchange="TVC", interval=Interval.in_daily, n_bars=15000)
    out = df.reset_index().rename(columns={"datetime": "ts"})
    out["date"] = (pd.to_datetime(out["ts"]) + pd.Timedelta(hours=12)).dt.normalize()
    out = out.dropna(subset=["close"]).drop_duplicates(subset=["date"]).set_index("date").sort_index()
    return out[["close"]]


def compute_metrics(daily_ret: pd.Series, weight_series: pd.Series = None, rf_daily: pd.Series = None) -> dict:
    daily_ret = daily_ret.dropna()
    if len(daily_ret) == 0:
        return {}
    n_years = (daily_ret.index[-1] - daily_ret.index[0]).days / 365.25
    cum = (1 + daily_ret).cumprod()
    total_ret = cum.iloc[-1] - 1.0
    cagr = (cum.iloc[-1] ** (1.0 / n_years) - 1.0) if n_years > 0 else 0.0
    ann_vol = daily_ret.std() * np.sqrt(252)
    sharpe0 = (cagr / ann_vol) if ann_vol > 0 else 0.0
    if rf_daily is not None:
        ex_ret = daily_ret - rf_daily.reindex(daily_ret.index).fillna(0.0)
        ex_ann = ex_ret.mean() * 252
        sharpe_rf = (ex_ann / ann_vol) if ann_vol > 0 else 0.0
    else:
        sharpe_rf = sharpe0
    peak = cum.cummax()
    dd = cum / peak - 1.0
    max_dd = dd.min()
    calmar = (cagr / abs(max_dd)) if max_dd < 0 else 0.0
    avg_w = weight_series.reindex(daily_ret.index).mean() if weight_series is not None else 1.0
    turnover_yr = (weight_series.reindex(daily_ret.index).diff().abs().sum() / n_years) if weight_series is not None else 0.0
    return {
        "n_years": n_years,
        "cagr": cagr * 100,
        "ann_vol": ann_vol * 100,
        "sharpe_rf": sharpe_rf,
        "sharpe0": sharpe0,
        "max_dd": max_dd * 100,
        "calmar": calmar,
        "avg_w": avg_w * 100,
        "turnover_yr": turnover_yr * 100,
        "final_mult": cum.iloc[-1],
    }


def main():
    con = duckdb.connect(str(DB_PATH), read_only=True)
    dgs2 = con.execute("SELECT date, value AS dgs2 FROM fred_series WHERE series_id='DGS2' ORDER BY date").df()
    dff = con.execute("SELECT date, value AS dff FROM fred_series WHERE series_id='DFF' ORDER BY date").df()
    dgs2["date"] = pd.to_datetime(dgs2["date"])
    dff["date"] = pd.to_datetime(dff["date"])
    dgs2 = dgs2.set_index("date")
    dff = dff.set_index("date")

    print("Fetching TVC:GOLD...")
    gold_tv = fetch_tv_gold()
    print("Fetching Yahoo GLD & SHY...")
    gld_yf = fetch_yahoo("GLD")
    shy_yf = fetch_yahoo("SHY")

    # Build master daily dataframe aligned to gold trading days from 1976-06-01
    df = gold_tv.loc["1976-06-01":].copy()
    df.rename(columns={"close": "gold"}, inplace=True)
    df = df.join(dgs2, how="left").join(dff, how="left")
    df["dgs2"] = df["dgs2"].ffill()
    df["dff"] = df["dff"].ffill()
    df = df.dropna(subset=["gold", "dgs2", "dff"])

    # Also join SHY and GLD for ETF era (2004-11-18+)
    df["gld_adj"] = gld_yf["adjclose"].reindex(df.index).ffill()
    df["shy_adj"] = shy_yf["adjclose"].reindex(df.index).ffill()

    # Daily returns
    df["gold_ret"] = df["gold"].pct_change().fillna(0.0)
    # Cash daily return: using 3-month T-bill / Fed Funds proxy (DFF / 100 / 252) or 2Y yield
    # For uninvested cash in the portfolio, earning risk-free rate (DFF/100/252) vs 0% cash
    df["rf_ret"] = (df["dff"].shift(1).fillna(0.0) / 100.0) / 252.0
    df["dgs2_yield_ret"] = (df["dgs2"].shift(1).fillna(0.0) / 100.0) / 252.0

    # Approximate 2Y Treasury total return index for 1976-2026:
    # A constant-maturity 2Y note has modified duration ~ 1.85 years.
    # Daily total return ~ yield/252 - duration * delta(yield)
    d_y2 = (df["dgs2"] - df["dgs2"].shift(1)).fillna(0.0) / 100.0
    df["ust2y_tr_ret"] = df["dgs2_yield_ret"] - 1.85 * d_y2
    df["ust2y_tr_idx"] = (1.0 + df["ust2y_tr_ret"]).cumprod()

    # Also Cumulative Cash index (earning T-bill / DFF yield, i.e., "国库券收益" literal T-bill)
    df["tbill_idx"] = (1.0 + df["rf_ret"]).cumprod()

    # Compare our synthetic 2Y total return index vs actual SHY total return in 2003-2026
    sub_shy = df.dropna(subset=["shy_adj"]).copy()
    sub_shy["shy_ret"] = sub_shy["shy_adj"].pct_change().fillna(0.0)
    corr_shy = sub_shy["shy_ret"].rolling(126).sum().corr(sub_shy["ust2y_tr_ret"].rolling(126).sum())

    # Calculate Blogger Signals (strictly at close of day t, applied to return of day t+1 -> no lookahead)
    # Variant A (Original video text: "和同期国库券收益比" -> T-bill cumulative return over 6m/12m)
    # Variant B (Claude calculator screenshot: "黄金 vs 2年期美债 SHY 总回报")
    for bench_name, bench_col in [("TBill_Yield", "tbill_idx"), ("UST2Y_TotalRet", "ust2y_tr_idx")]:
        g_6m = df["gold"] / df["gold"].shift(126) - 1.0
        g_12m = df["gold"] / df["gold"].shift(252) - 1.0
        b_6m = df[bench_col] / df[bench_col].shift(126) - 1.0
        b_12m = df[bench_col] / df[bench_col].shift(252) - 1.0
        df[f"mom_{bench_name}"] = 0.5 * (g_6m > b_6m).astype(float) + 0.5 * (g_12m > b_12m).astype(float)

    # Also absolute momentum (> 0)
    df["mom_abs"] = 0.5 * ((df["gold"] / df["gold"].shift(126) - 1.0) > 0).astype(float) + \
                    0.5 * ((df["gold"] / df["gold"].shift(252) - 1.0) > 0).astype(float)

    # Fed Score: 2Y Treasury Yield < Fed Funds Rate -> 1, else 0
    df["fed_score"] = (df["dgs2"] < df["dff"]).astype(float)

    # Volatility Coefficient: min(1.0, 15% / ann_vol)
    for w in [20, 60]:
        vol = df["gold_ret"].rolling(w).std() * np.sqrt(252) * 100.0
        df[f"vol_coef_{w}d"] = (15.0 / vol).clip(upper=1.0).fillna(0.0)

    # Wait for 252 trading days warmup -> start backtest from 1977-06-03
    bt = df.iloc[255:].copy()

    lines = []
    def log(s=""):
        print(s)
        lines.append(s)

    log("=" * 80)
    log("BACKTEST REPORT: DOUYIN BLOGGER (@文主任) GOLD POSITION SIZING STRATEGY")
    log(f"Generated: 2026-11-20 | Sample: {bt.index[0].date()} to {bt.index[-1].date()} ({len(bt)} trading days, ~{(bt.index[-1]-bt.index[0]).days/365.25:.1f} years)")
    log("=" * 80)
    log(f"Synthetic 2Y UST Total Return vs SHY 6m Return Correlation (2002-2026): {corr_shy:.4f}")

    # Define strategy weights (before shift)
    # 1. Blogger Exact (UST2Y Total Return benchmark, 20d vol, daily or monthly rebalance)
    bt["w_blogger_20d_ust2y"] = ((bt["mom_UST2Y_TotalRet"] + bt["fed_score"]) / 2.0) * bt["vol_coef_20d"]
    bt["w_blogger_20d_tbill"] = ((bt["mom_TBill_Yield"] + bt["fed_score"]) / 2.0) * bt["vol_coef_20d"]
    bt["w_blogger_60d_ust2y"] = ((bt["mom_UST2Y_TotalRet"] + bt["fed_score"]) / 2.0) * bt["vol_coef_60d"]

    # Monthly rebalanced version of blogger strategy (signals observed only at last trading day of each month)
    m_end_w = bt.groupby(bt.index.to_period("M"))["w_blogger_20d_ust2y"].tail(1)
    bt["w_blogger_monthly"] = m_end_w.reindex(bt.index, method="ffill").fillna(0.0)

    # Component Decomposition (to see what works and what hurts!)
    bt["w_only_mom_ust2y"] = bt["mom_UST2Y_TotalRet"]
    bt["w_only_mom_tbill"] = bt["mom_TBill_Yield"]
    bt["w_only_mom_abs"] = bt["mom_abs"]
    bt["w_only_fed"] = bt["fed_score"]
    bt["w_mom_ust2y_plus_vol20"] = bt["mom_UST2Y_TotalRet"] * bt["vol_coef_20d"]
    bt["w_mom_tbill_plus_vol20"] = bt["mom_TBill_Yield"] * bt["vol_coef_20d"]
    bt["w_sma200"] = (bt["gold"] > bt["gold"].rolling(200).mean()).astype(float)
    bt["w_sma200_vol20"] = bt["w_sma200"] * bt["vol_coef_20d"]
    bt["w_bh"] = 1.0
    bt["w_half"] = 0.5
    bt["w_avg_match"] = bt["w_blogger_20d_ust2y"].mean()  # static weight matching blogger's average exposure

    strategies = [
        ("Buy & Hold Gold (100%)", "w_bh"),
        ("Static 50/50 (Gold + Cash)", "w_half"),
        (f"Static {bt['w_avg_match'].iloc[0]*100:.1f}% Gold (Matches Blogger Avg Weight)", "w_avg_match"),
        ("Blogger Formula (UST2Y Bench, 20d Vol, Daily)", "w_blogger_20d_ust2y"),
        ("Blogger Formula (T-Bill Bench, 20d Vol, Daily)", "w_blogger_20d_tbill"),
        ("Blogger Formula (UST2Y Bench, 60d Vol, Daily)", "w_blogger_60d_ust2y"),
        ("Blogger Formula (UST2Y Bench, 20d Vol, Monthly)", "w_blogger_monthly"),
        ("Component 1 Only: Momentum vs UST2Y (0 / 0.5 / 1)", "w_only_mom_ust2y"),
        ("Component 1 Only: Momentum vs T-Bill (0 / 0.5 / 1)", "w_only_mom_tbill"),
        ("Component 1 Only: Absolute Momentum > 0", "w_only_mom_abs"),
        ("Component 2 Only: Fed Score (2Y < EFFR)", "w_only_fed"),
        ("Momentum (vs UST2Y) * Vol_Coef (Drop Fed Score)", "w_mom_ust2y_plus_vol20"),
        ("Momentum (vs T-Bill) * Vol_Coef (Drop Fed Score)", "w_mom_tbill_plus_vol20"),
        ("Simple 200d SMA Trend (0 or 1)", "w_sma200"),
        ("200d SMA Trend * Vol_Coef", "w_sma200_vol20"),
    ]

    for cash_mode in ["Uninvested Cash Earns T-Bill/EFFR (Portfolio View)", "Uninvested Cash Earns 0% (Excess Return / Standalone View)"]:
        log("\n" + "-" * 80)
        log(f"FULL SAMPLE (1977-06 to 2026-11, ~49.5 Years) — {cash_mode}")
        log("-" * 80)
        log(f"{'Strategy':<52} | {'CAGR%':>6} | {'Vol%':>6} | {'Sharpe':>6} | {'MaxDD%':>7} | {'Calmar':>6} | {'AvgW%':>6}")
        log("-" * 102)
        for label, col in strategies:
            w = bt[col].shift(1).fillna(0.0)
            # Transaction cost: 5 bps (0.05%) per unit of weight change for active strategies
            tc = 0.0 if col in ("w_bh", "w_half", "w_avg_match") else w.diff().abs().fillna(0.0) * 0.0005
            if "Earns T-Bill" in cash_mode:
                ret = w * bt["gold_ret"] + (1.0 - w) * bt["rf_ret"] - tc
                m = compute_metrics(ret, w, bt["rf_ret"])
                sh = m["sharpe_rf"]
            else:
                ret = w * bt["gold_ret"] - tc
                m = compute_metrics(ret, w, None)
                sh = m["sharpe0"]
            log(f"{label:<52} | {m['cagr']:>6.2f} | {m['ann_vol']:>6.2f} | {sh:>6.2f} | {m['max_dd']:>7.2f} | {m['calmar']:>6.2f} | {m['avg_w']:>6.1f}")

    # Now ETF Era (2005-11-18 to 2026-11-19) using actual GLD and SHY total return!
    etf = df.loc["2004-11-18":].copy()
    etf["gld_ret"] = etf["gld_adj"].pct_change().fillna(0.0)
    etf["shy_ret"] = etf["shy_adj"].pct_change().fillna(0.0)
    g6 = etf["gld_adj"] / etf["gld_adj"].shift(126) - 1.0
    g12 = etf["gld_adj"] / etf["gld_adj"].shift(252) - 1.0
    s6 = etf["shy_adj"] / etf["shy_adj"].shift(126) - 1.0
    s12 = etf["shy_adj"] / etf["shy_adj"].shift(252) - 1.0
    etf["mom_shy"] = 0.5 * (g6 > s6).astype(float) + 0.5 * (g12 > s12).astype(float)
    etf["fed_score"] = (etf["dgs2"] < etf["dff"]).astype(float)
    vol20 = etf["gld_ret"].rolling(20).std() * np.sqrt(252) * 100.0
    etf["vol_coef"] = (15.0 / vol20).clip(upper=1.0).fillna(0.0)

    etf["w_blogger"] = ((etf["mom_shy"] + etf["fed_score"]) / 2.0) * etf["vol_coef"]
    etf["w_blogger_m"] = etf.groupby(etf.index.to_period("M"))["w_blogger"].tail(1).reindex(etf.index, method="ffill").fillna(0.0)
    etf["w_only_mom"] = etf["mom_shy"]
    etf["w_only_fed"] = etf["fed_score"]
    etf["w_mom_vol"] = etf["mom_shy"] * etf["vol_coef"]
    etf["w_sma200"] = (etf["gld_adj"] > etf["gld_adj"].rolling(200).mean()).astype(float)
    etf["w_bh"] = 1.0
    etf["w_half"] = 0.5

    etf_bt = etf.iloc[255:].copy()
    etf_bt["w_avg_match"] = etf_bt["w_blogger"].mean()

    log("\n" + "=" * 80)
    log(f"ACTUAL ETF ERA (GLD vs SHY, {etf_bt.index[0].date()} to {etf_bt.index[-1].date()}, ~{len(etf_bt)/252:.1f} Years)")
    log("=" * 80)
    etf_strats = [
        ("Buy & Hold GLD (100%)", "w_bh"),
        ("Static 50/50 (GLD + SHY)", "w_half"),
        (f"Static {etf_bt['w_avg_match'].iloc[0]*100:.1f}% GLD + SHY (Matches Avg Weight)", "w_avg_match"),
        ("Blogger Formula (Daily, Uninvested in SHY)", "w_blogger"),
        ("Blogger Formula (Monthly, Uninvested in SHY)", "w_blogger_m"),
        ("Momentum Score Only (GLD vs SHY, 0/0.5/1)", "w_only_mom"),
        ("Fed Score Only (2Y < EFFR, 0/1)", "w_only_fed"),
        ("Momentum * Vol_Coef (Drop Fed Score)", "w_mom_vol"),
        ("Simple 200d SMA (GLD vs SHY)", "w_sma200"),
    ]
    for cash_mode in ["Uninvested in SHY", "Uninvested in 0% Cash"]:
        log(f"\n--- ETF Era ({cash_mode}, 5 bps Transaction Cost) ---")
        log(f"{'Strategy':<48} | {'CAGR%':>6} | {'Vol%':>6} | {'Sharpe':>6} | {'MaxDD%':>7} | {'Calmar':>6} | {'AvgW%':>6}")
        log("-" * 98)
        for label, col in etf_strats:
            w = etf_bt[col].shift(1).fillna(0.0)
            tc = 0.0 if col in ("w_bh", "w_half", "w_avg_match") else w.diff().abs().fillna(0.0) * 0.0005
            if cash_mode == "Uninvested in SHY":
                ret = w * etf_bt["gld_ret"] + (1.0 - w) * etf_bt["shy_ret"] - tc
                m = compute_metrics(ret, w, etf_bt["rf_ret"])
                sh = m["sharpe_rf"]
            else:
                ret = w * etf_bt["gld_ret"] - tc
                m = compute_metrics(ret, w, None)
                sh = m["sharpe0"]
            log(f"{label:<48} | {m['cagr']:>6.2f} | {m['ann_vol']:>6.2f} | {sh:>6.2f} | {m['max_dd']:>7.2f} | {m['calmar']:>6.2f} | {m['avg_w']:>6.1f}")

    # Inspect the 4 discrete states of (Mom_Score, Fed_Score) across 1977-2026!
    log("\n" + "=" * 80)
    log("FORWARD RETURN & WIN RATE BY SIGNAL REGIME (1977-06 to 2026-11)")
    log("=" * 80)
    bt["fwd_21d"] = bt["gold"].shift(-21) / bt["gold"] - 1.0
    bt["fwd_63d"] = bt["gold"].shift(-63) / bt["gold"] - 1.0
    bt["fwd_126d"] = bt["gold"].shift(-126) / bt["gold"] - 1.0
    bt["fwd_252d"] = bt["gold"].shift(-252) / bt["gold"] - 1.0
    bt["next_1d"] = bt["gold_ret"].shift(-1)

    log("\n1. By Momentum Score (Gold vs 2Y UST Total Return):")
    for val, grp in bt.groupby("mom_UST2Y_TotalRet"):
        ann_ret = grp["next_1d"].mean() * 252 * 100
        ann_vol = grp["next_1d"].std() * np.sqrt(252) * 100
        wr_3m = (grp["fwd_63d"].dropna() > 0).mean() * 100
        wr_6m = (grp["fwd_126d"].dropna() > 0).mean() * 100
        med_6m = grp["fwd_126d"].dropna().median() * 100
        log(f"  Mom_Score = {val:.1f} | Days: {len(grp):>5} ({len(grp)/len(bt)*100:>4.1f}%) | Next-Day Ann.Ret: {ann_ret:>6.2f}% (Vol {ann_vol:>5.2f}%) | 3m WinRate: {wr_3m:>5.1f}% | 6m WinRate: {wr_6m:>5.1f}% (Med {med_6m:>+6.2f}%)")

    log("\n2. By Fed Score (2Y Yield < Fed Funds Rate):")
    for val, grp in bt.groupby("fed_score"):
        ann_ret = grp["next_1d"].mean() * 252 * 100
        ann_vol = grp["next_1d"].std() * np.sqrt(252) * 100
        wr_3m = (grp["fwd_63d"].dropna() > 0).mean() * 100
        wr_6m = (grp["fwd_126d"].dropna() > 0).mean() * 100
        med_6m = grp["fwd_126d"].dropna().median() * 100
        log(f"  Fed_Score = {val:.1f} | Days: {len(grp):>5} ({len(grp)/len(bt)*100:>4.1f}%) | Next-Day Ann.Ret: {ann_ret:>6.2f}% (Vol {ann_vol:>5.2f}%) | 3m WinRate: {wr_3m:>5.1f}% | 6m WinRate: {wr_6m:>5.1f}% (Med {med_6m:>+6.2f}%)")

    log("\n3. Joint 2x3 Matrix (Mom_Score x Fed_Score):")
    for (m_val, f_val), grp in bt.groupby(["mom_UST2Y_TotalRet", "fed_score"]):
        raw_w = (m_val + f_val) / 2.0
        ann_ret = grp["next_1d"].mean() * 252 * 100
        wr_6m = (grp["fwd_126d"].dropna() > 0).mean() * 100
        med_6m = grp["fwd_126d"].dropna().median() * 100
        log(f"  Mom={m_val:.1f}, Fed={f_val:.0f} (BaseWeight={raw_w*100:>4.0f}%) | Days: {len(grp):>5} ({len(grp)/len(bt)*100:>4.1f}%) | Next-Day Ann.Ret: {ann_ret:>6.2f}% | 6m WinRate: {wr_6m:>5.1f}% (Med {med_6m:>+6.2f}%)")

    log("\n4. By Volatility Regime (20d Annualized Vol > 15% vs <= 15%):")
    bt["high_vol"] = (bt["vol_coef_20d"] < 1.0)
    for hv, grp in bt.groupby("high_vol"):
        ann_ret = grp["next_1d"].mean() * 252 * 100
        ann_vol = grp["next_1d"].std() * np.sqrt(252) * 100
        sh = ann_ret / ann_vol if ann_vol > 0 else 0
        log(f"  Vol > 15% = {str(hv):<5} | Days: {len(grp):>5} ({len(grp)/len(bt)*100:>4.1f}%) | Next-Day Ann.Ret: {ann_ret:>6.2f}% | Future Realized Vol: {ann_vol:>5.2f}% | Return/Vol: {sh:>5.2f}")

    # Sub-period breakdown across major macro cycles
    log("\n" + "=" * 80)
    log("CYCLE-BY-CYCLE SUB-PERIOD BREAKDOWN (0% Cash / Standalone Return View)")
    log("=" * 80)
    cycles = [
        ("1977-06 to 1980-09 (1970s Stagflation Mania)", "1977-06-03", "1980-09-30"),
        ("1980-10 to 2001-03 (20-Year Secular Gold Bear)", "1980-10-01", "2001-03-30"),
        ("2001-04 to 2011-09 (2000s Super-Bull & GFC)", "2001-04-01", "2011-09-06"),
        ("2011-09 to 2018-08 (Post-QE Bear & Consolidation)", "2011-09-07", "2018-08-15"),
        ("2018-08 to 2026-11 (COVID + Rate Hikes + 2024-26 Cycle)", "2018-08-16", "2026-11-19"),
        ("2025-01 to 2026-11 (Recent Parabolic Top & 2026 Crash)", "2025-01-01", "2026-11-19"),
    ]
    for cname, s_dt, e_dt in cycles:
        sub = bt.loc[s_dt:e_dt]
        if len(sub) == 0:
            continue
        log(f"\n[{cname}] (Gold spot: ${sub['gold'].iloc[0]:.1f} -> ${sub['gold'].iloc[-1]:.1f}, {(sub['gold'].iloc[-1]/sub['gold'].iloc[0]-1)*100:+.1f}%)")
        for label, col in [
            ("Buy & Hold Gold (100%)", "w_bh"),
            ("Blogger Formula (UST2Y, 20d Vol)", "w_blogger_20d_ust2y"),
            ("Momentum Only (vs UST2Y)", "w_only_mom_ust2y"),
            ("Momentum * Vol_Coef (No Fed Score)", "w_mom_ust2y_plus_vol20"),
            ("Fed Score Only (2Y < EFFR)", "w_only_fed"),
            ("200d SMA Trend", "w_sma200"),
        ]:
            w = sub[col].shift(1).fillna(0.0)
            tc = 0.0 if col == "w_bh" else w.diff().abs().fillna(0.0) * 0.0005
            ret = w * sub["gold_ret"] - tc
            m = compute_metrics(ret, w, None)
            log(f"  {label:<36} | CAGR: {m['cagr']:>+6.2f}% | Vol: {m['ann_vol']:>5.2f}% | Sharpe: {m['sharpe0']:>+5.2f} | MaxDD: {m['max_dd']:>6.2f}% | AvgW: {m['avg_w']:>4.1f}%")

    # Detailed Timeline of 2024-2026 (Monthly snapshots)
    log("\n" + "=" * 80)
    log("RECENT TIMELINE (2024-01 to 2026-11): HOW THE FORMULA BEHAVED AT THE TOP & CRASH")
    log("=" * 80)
    rec = bt.loc["2024-01-01":].groupby(bt.loc["2024-01-01":].index.to_period("M")).tail(1)
    log(f"{'Date':<10} | {'Gold$':>7} | {'2Y%':>5} | {'EFFR%':>5} | {'Mom':>4} | {'Fed':>4} | {'Vol20%':>6} | {'VolCoef':>7} | {'BloggerW%':>9} | {'Mom*VolW%':>9}")
    log("-" * 92)
    for dt, row in rec.iterrows():
        vol20_val = 15.0 / row["vol_coef_20d"] if row["vol_coef_20d"] < 1.0 else (bt["gold_ret"].loc[:dt].iloc[-20:].std()*np.sqrt(252)*100)
        log(f"{str(dt.date()):<10} | {row['gold']:>7.1f} | {row['dgs2']:>5.2f} | {row['dff']:>5.2f} | {row['mom_UST2Y_TotalRet']:>4.1f} | {row['fed_score']:>4.0f} | {vol20_val:>6.1f} | {row['vol_coef_20d']:>7.2f} | {row['w_blogger_20d_ust2y']*100:>8.1f}% | {row['w_mom_ust2y_plus_vol20']*100:>8.1f}%")

    # Also log latest daily row (2026-11-19)
    last_dt = bt.index[-1]
    row = bt.iloc[-1]
    vol20_val = bt["gold_ret"].iloc[-20:].std() * np.sqrt(252) * 100
    g6_now = (bt["gold"].iloc[-1] / bt["gold"].iloc[-127] - 1) * 100
    g12_now = (bt["gold"].iloc[-1] / bt["gold"].iloc[-253] - 1) * 100
    u6_now = (bt["ust2y_tr_idx"].iloc[-1] / bt["ust2y_tr_idx"].iloc[-127] - 1) * 100
    u12_now = (bt["ust2y_tr_idx"].iloc[-1] / bt["ust2y_tr_idx"].iloc[-253] - 1) * 100
    log("-" * 92)
    log(f"LATEST ({last_dt.date()}): Gold=${row['gold']:.1f} (6m {g6_now:+.1f}% vs UST2Y {u6_now:+.1f}%; 12m {g12_now:+.1f}% vs UST2Y {u12_now:+.1f}%)")
    log(f"  DGS2={row['dgs2']:.2f}%, EFFR={row['dff']:.2f}% | 20d Vol={vol20_val:.1f}% (VolCoef={row['vol_coef_20d']:.2f}) | Blogger Weight={row['w_blogger_20d_ust2y']*100:.1f}%")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nSaved full output to {OUT_PATH}")


if __name__ == "__main__":
    main()
