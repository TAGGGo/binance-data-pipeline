"""
Research Script:
  1. Objective evaluation of Benjamin Cowen's current thesis:
     - Check Weekly BMSB (20W SMA / 21W EMA) for SPY (SPX), QQQ (NDQ), MSTR, TOTAL2, TOTAL3, BTC, ETH.
     - Check Macro tightening in 2026 (2Y yield DGS2, 10Y yield DGS10, 10Y real yield DFII10, Fed Funds DFF,
       WTI Oil DCOILWTICO, VIX, Net Liquidity) vs 2023.
  2. Verify the historic 2025-2026 divergence between BTC and US Equities (SPY / QQQ):
     - Oct 2025 -> Jun 2026: BTC bear market while SPY/QQQ rose.
     - Jun 2026 -> Nov 2026: BTC bottomed & rallied (+28% above June low), while SPY/QQQ peaked and dropped
       back toward June 2026 levels and broke below their Weekly BMSB.
     - Historical backtest: What happens to BTC when SPX/QQQ break below their Weekly BMSB while BTC is testing its BMSB?
  3. Compare Today (2026-10/11) against BOTH:
     - 2023 April-June (Wave 1 pullback after cycle bottom, during May-July 2023 mini rate-hike / tightening cycle)
     - 2023 August-October (Wave 2 pullback, during surge in 10Y yields from 3.8% to 5.0% and SPX correction)

Data: data/market.snapshot.duckdb
Date: 2026-11-09
"""

from pathlib import Path
import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "market.snapshot.duckdb"
OUT_PATH = ROOT / "data" / "outputs" / "cowen_macro_equities_and_2023_apr_vs_aug_2026-11-09.txt"


def bmsb_stats(series: pd.Series, freq="W-SUN"):
    s = series.dropna().sort_index().loc[:"2026-11-08"]
    w = s.resample(freq).last().dropna()
    sma20 = w.rolling(20).mean()
    ema21 = w.ewm(span=21, adjust=False).mean()
    bot = pd.concat([sma20, ema21], axis=1).min(axis=1)
    top = pd.concat([sma20, ema21], axis=1).max(axis=1)
    df = pd.DataFrame({
        "close": w,
        "sma20": sma20,
        "ema21": ema21,
        "bot": bot,
        "top": top,
        "vs_bot": (w / bot - 1.0) * 100.0,
        "vs_top": (w / top - 1.0) * 100.0,
    })
    return df


def main():
    con = duckdb.connect(str(DB_PATH), read_only=True)
    lines = []

    def log(msg=""):
        print(msg)
        lines.append(msg)

    # 1. Load equities (SPY, QQQ, MSTR, IWM, NVDA, TLT, GLD)
    eq = con.execute("""
        SELECT ticker, CAST(date AS TIMESTAMP) AS dt, close
        FROM equity_daily
        WHERE ticker IN ('SPY', 'QQQ', 'MSTR', 'IWM', 'NVDA', 'TLT', 'GLD', 'COIN')
        ORDER BY ticker, dt
    """).df()
    eq_p = eq.pivot_table(index="dt", columns="ticker", values="close").sort_index()

    # 2. Load BTC & ETH from bn_kline_1d
    crypto = con.execute("""
        SELECT symbol, CAST(ts AS TIMESTAMP) AS dt, close
        FROM bn_kline_1d
        WHERE market = 'spot' AND symbol IN ('BTC', 'ETH')
        ORDER BY symbol, dt
    """).df()
    cr_p = crypto.pivot_table(index="dt", columns="symbol", values="close").sort_index()

    # 3. Load TOTAL, TOTAL2, TOTAL3, DXY, US02Y, US10Y from tv_bars
    tv = con.execute("""
        SELECT symbol, CAST(ts AS DATE) AS dt, LAST(close ORDER BY ts) AS close
        FROM tv_bars
        WHERE symbol IN ('CRYPTOCAP:TOTAL', 'CRYPTOCAP:TOTAL2', 'CRYPTOCAP:TOTAL3',
                         'TVC:DXY', 'TVC:US02Y', 'TVC:US10Y', 'BITSTAMP:BTCUSD')
        GROUP BY symbol, CAST(ts AS DATE)
        ORDER BY symbol, dt
    """).df()
    tv["dt"] = pd.to_datetime(tv["dt"])
    tv_p = tv.pivot_table(index="dt", columns="symbol", values="close").sort_index()

    # 4. Load FRED series
    fred = con.execute("""
        SELECT series_id, CAST(date AS TIMESTAMP) AS dt, value
        FROM fred_series
        WHERE series_id IN ('DFF', 'DGS2', 'DGS10', 'DGS30', 'DFII10', 'T10YIE',
                            'VIXCLS', 'DCOILWTICO', 'BAMLH0A0HYM2', 'WALCL', 'WTREGEN', 'RRPONTSYD')
        ORDER BY series_id, dt
    """).df()
    fr_p = fred.pivot_table(index="dt", columns="series_id", values="value").sort_index().ffill()

    log("=" * 100)
    log("PART 1: VERIFYING BENJAMIN COWEN'S CROSS-ASSET BMSB SIGNAL (SPY, QQQ, MSTR, TOTAL2, TOTAL3, BTC, ETH)")
    log("=" * 100)

    assets = {
        "BTC": cr_p["BTC"],
        "ETH": cr_p["ETH"],
        "TOTAL2 ($B)": tv_p["CRYPTOCAP:TOTAL2"] / 1e9,
        "TOTAL3 ($B)": tv_p["CRYPTOCAP:TOTAL3"] / 1e9,
        "SPY (SPX)": eq_p["SPY"],
        "QQQ (NDQ)": eq_p["QQQ"],
        "MSTR": eq_p["MSTR"],
        "IWM (Russell)": eq_p["IWM"],
    }

    for name, s in assets.items():
        b = bmsb_stats(s)
        last4 = b.tail(5)
        r = last4.iloc[-1]
        st = "ABOVE BMSB" if r["close"] > r["top"] else ("BELOW BMSB" if r["close"] < r["bot"] else "IN BMSB")
        log(f"  * {name:<13} | Latest ({last4.index[-1].strftime('%Y-%m-%d')}): Close={r['close']:8.2f} | "
            f"BMSB=[{r['bot']:8.2f} .. {r['top']:8.2f}] | {st:<10} (vs Bot {r['vs_bot']:+5.1f}%, vs Top {r['vs_top']:+5.1f}%)")
        # Show last 4 weeks progression
        wk_str = " -> ".join([f"{idx.strftime('%m/%d')}:{row['close']:.1f}({row['vs_bot']:+.1f}%)" for idx, row in last4.iterrows()])
        log(f"      Last 5w (close & vs_BMSB_bot): {wk_str}")

    log("\n" + "=" * 100)
    log("PART 2: THE HISTORIC 2025-2026 BTC vs US STOCKS (SPY/QQQ) DIVERGENCE")
    log("=" * 100)
    comb = pd.DataFrame({
        "BTC": cr_p["BTC"],
        "ETH": cr_p["ETH"],
        "SPY": eq_p["SPY"],
        "QQQ": eq_p["QQQ"],
        "MSTR": eq_p["MSTR"],
    }).ffill().dropna(subset=["BTC", "SPY", "QQQ"])

    checkpoints = [
        ("2025-10-06 (BTC Cycle Peak)", "2025-10-06"),
        ("2026-01-02 (Start of 2026)", "2026-01-02"),
        ("2026-03-01 (Spring 2026)", "2026-03-01"),
        ("2026-06-01 (Early June 2026)", "2026-06-01"),
        ("2026-06-30 (BTC Cycle Bottom)", "2026-06-30"),
        ("2026-09-25 (Late Sept 2026)", "2026-09-25"),
        ("2026-10-04 (BTC Rally Peak)", "2026-10-04"),
        ("2026-11-06 (Latest Equity Close)", "2026-11-06"),
        ("2026-11-09 (Latest Crypto Close)", "2026-11-09"),
    ]
    for label, dt_str in checkpoints:
        sub = comb.loc[:dt_str]
        if len(sub) == 0:
            continue
        r = sub.iloc[-1]
        log(f"  * {label:<34} ({sub.index[-1].strftime('%Y-%m-%d')}): "
            f"BTC=${r['BTC']:8,.0f} | SPY=${r['SPY']:6.2f} | QQQ=${r['QQQ']:6.2f} | MSTR=${r['MSTR']:6.2f}")

    # Find exact 2026 peaks and June 2026 levels for SPY and QQQ
    comb_2026 = comb.loc["2026-01-01":]
    for col in ["BTC", "SPY", "QQQ", "MSTR"]:
        pk_dt = comb_2026[col].idxmax()
        pk_val = comb_2026[col].max()
        lo_dt = comb_2026[col].idxmin()
        lo_val = comb_2026[col].min()
        jun_mean = comb.loc["2026-06-01":"2026-06-30", col].mean()
        jun_end = comb.loc[:"2026-06-30", col].iloc[-1]
        cur_val = comb_2026[col].iloc[-1]
        # Find earliest date in 2026 when price first reached cur_val
        first_reach = comb_2026[comb_2026[col] >= cur_val].index[0].strftime("%Y-%m-%d")
        log(f"\n  [{col}] 2026 Anatomy:")
        log(f"    - 2026 Low : {lo_val:,.2f} on {lo_dt.strftime('%Y-%m-%d')} | 2026 Peak: {pk_val:,.2f} on {pk_dt.strftime('%Y-%m-%d')}")
        log(f"    - End of June 2026 (2026-06-30): {jun_end:,.2f} (June avg: {jun_mean:,.2f})")
        log(f"    - Current ({comb_2026.index[-1].strftime('%Y-%m-%d')}): {cur_val:,.2f} "
            f"(from 2026 Peak: {(cur_val/pk_val - 1)*100:+.1f}%, vs 2026-06-30: {(cur_val/jun_end - 1)*100:+.1f}%, "
            f"back to level first reached on {first_reach})")

    log("\n" + "=" * 100)
    log("PART 3: MACRO TIGHTENING & 'MINI RATE HIKE / YIELD SURGE' (2026 vs 2023 APR-JUN vs 2023 AUG-OCT)")
    log("=" * 100)
    macro_dates = [
        ("2023-04-14 (2023 Apr BTC Peak)", "2023-04-14"),
        ("2023-05-15 (2023 May Pullback)", "2023-05-15"),
        ("2023-06-14 (2023 Jun FOMC Trough)", "2023-06-14"),
        ("2023-07-13 (2023 Jul BTC Peak)", "2023-07-13"),
        ("2023-08-20 (2023 Aug BMSB Break)", "2023-08-20"),
        ("2023-09-11 (2023 Sep BTC Trough)", "2023-09-11"),
        ("2023-10-19 (2023 Oct 10Y Peak 5%)", "2023-10-19"),
        ("2026-06-30 (2026 Jun BTC Bottom)", "2026-06-30"),
        ("2026-08-15 (2026 Aug Mid-Rally)", "2026-08-15"),
        ("2026-10-04 (2026 Oct BTC Peak)", "2026-10-04"),
        ("2026-11-06 (2026 Nov Today)", "2026-11-06"),
    ]
    for label, dt_str in macro_dates:
        r = fr_p.loc[:dt_str].iloc[-1]
        dxy_val = tv_p["TVC:DXY"].dropna().loc[:dt_str].iloc[-1] if "TVC:DXY" in tv_p else np.nan
        net_liq = (r.get("WALCL", np.nan) - r.get("WTREGEN", np.nan) - r.get("RRPONTSYD", 0)*1000) / 1e6
        log(f"  * {label:<34}: Fed(DFF)={r['DFF']:.2f}% | 2Y={r['DGS2']:.2f}% | 10Y={r['DGS10']:.2f}% | "
            f"Real10Y={r['DFII10']:.2f}% | Oil=${r['DCOILWTICO']:.1f} | VIX={r['VIXCLS']:.1f} | DXY={dxy_val:.1f} | NetLiq=${net_liq:.2f}T")

    # Show 2026 trajectory of DFF, DGS2, DGS10, DFII10, DCOILWTICO, VIXCLS
    fr_2026 = fr_p.loc["2026-01-01":]
    log("\n--- 2026 Macro Tightening Trajectory (Monthly End) ---")
    for m_end in fr_2026.resample("ME").last().index:
        r = fr_2026.loc[:m_end].iloc[-1]
        dxy_val = tv_p["TVC:DXY"].dropna().loc[:m_end].iloc[-1]
        net_liq = (r.get("WALCL", np.nan) - r.get("WTREGEN", np.nan) - r.get("RRPONTSYD", 0)*1000) / 1e6
        log(f"  {m_end.strftime('%Y-%m-%d')}: DFF={r['DFF']:.2f}% | 2Y={r['DGS2']:.2f}% | 10Y={r['DGS10']:.2f}% | "
            f"Real10Y={r['DFII10']:.2f}% | Breakeven={r['T10YIE']:.2f}% | Oil=${r['DCOILWTICO']:.1f} | VIX={r['VIXCLS']:.1f} | DXY={dxy_val:.1f} | NetLiq=${net_liq:.2f}T")

    log("\n" + "=" * 100)
    log("PART 4: HEAD-TO-HEAD COMPARISON — IS TODAY MORE LIKE 2023 APR-JUN OR 2023 AUG-OCT?")
    log("=" * 100)
    # Compare 2023 Apr-Jun pullback vs 2023 Jul-Sep pullback vs 2026 Oct-Nov pullback
    btc_s = cr_p["BTC"].dropna()
    eth_s = cr_p["ETH"].dropna()
    btc_bmsb = bmsb_stats(btc_s)
    eth_bmsb = bmsb_stats(eth_s)
    spy_bmsb = bmsb_stats(eq_p["SPY"])
    qqq_bmsb = bmsb_stats(eq_p["QQQ"])

    # Compute exact metrics for the three pullbacks:
    # 1. 2023 Wave 1 pullback: Peak 2023-04-14 ($30,472) -> Trough 2023-06-14 ($25,128), at Day 36 = 2023-05-20
    # 2. 2023 Wave 2 pullback: Peak 2023-07-13 ($31,474) -> Trough 2023-09-11 ($25,162), at Day 36 = 2023-08-18
    # 3. 2026 Wave 1 pullback: Peak 2026-10-04 ($87,304) -> Current Day 36 = 2026-11-09 ($74,851 / $75,338)
    episodes = [
        ("2023 Apr-Jun (Wave 1 Pullback)", "2022-11-21", "2023-04-14", "2023-05-20", "2023-06-14"),
        ("2023 Jul-Sep (Wave 2 Pullback)", "2022-11-21", "2023-07-13", "2023-08-18", "2023-09-11"),
        ("2026 Oct-Nov (Current Pullback)", "2026-06-30", "2026-10-04", "2026-11-08", "2026-11-08"),
    ]
    for name, cyc_low_dt, pk_dt, d36_dt, tr_dt in episodes:
        low_px = btc_s.loc[:cyc_low_dt].iloc[-1]
        pk_px = btc_s.loc[:pk_dt].iloc[-1]
        d36_px = btc_s.loc[:d36_dt].iloc[-1]
        tr_px = btc_s.loc[:tr_dt].iloc[-1]
        days_low_to_pk = (pd.to_datetime(pk_dt) - pd.to_datetime(cyc_low_dt)).days
        days_pk_to_tr = (pd.to_datetime(tr_dt) - pd.to_datetime(pk_dt)).days
        rally_pct = (pk_px / low_px - 1) * 100
        dd_at_d36 = (d36_px / pk_px - 1) * 100
        dd_at_tr = (tr_px / pk_px - 1) * 100

        # BMSB status at Day 36 and at Trough
        b_d36 = btc_bmsb.loc[:d36_dt].iloc[-1]
        e_d36 = eth_bmsb.loc[:d36_dt].iloc[-1]
        s_d36 = spy_bmsb.loc[:d36_dt].iloc[-1]
        q_d36 = qqq_bmsb.loc[:d36_dt].iloc[-1]

        b_tr = btc_bmsb.loc[:tr_dt].iloc[-1]
        e_tr = eth_bmsb.loc[:tr_dt].iloc[-1]

        # 2Y yield change over prior 45 days at Day 36
        dgs2_now = fr_p["DGS2"].loc[:d36_dt].iloc[-1]
        dgs2_45d = fr_p["DGS2"].loc[:(pd.to_datetime(d36_dt) - pd.Timedelta(days=45))].iloc[-1]
        dgs10_now = fr_p["DGS10"].loc[:d36_dt].iloc[-1]
        dgs10_45d = fr_p["DGS10"].loc[:(pd.to_datetime(d36_dt) - pd.Timedelta(days=45))].iloc[-1]

        log(f"\n  >>> {name}:")
        log(f"    - Cycle Low ({cyc_low_dt}): ${low_px:,.0f} -> Rally Peak ({pk_dt}, +{days_low_to_pk}d): ${pk_px:,.0f} ({rally_pct:+.1f}%)")
        log(f"    - At Day ~36 ({d36_dt}): BTC=${d36_px:,.0f} (DD={dd_at_d36:+.1f}%) | Final Trough ({tr_dt}, Day {days_pk_to_tr}): ${tr_px:,.0f} (DD={dd_at_tr:+.1f}%)")
        log(f"    - BMSB at Day ~36: BTC vs Bot={b_d36['vs_bot']:+.1f}% (vs Top={b_d36['vs_top']:+.1f}%) | "
            f"ETH vs Bot={e_d36['vs_bot']:+.1f}% | SPY vs Bot={s_d36['vs_bot']:+.1f}% | QQQ vs Bot={q_d36['vs_bot']:+.1f}%")
        log(f"    - BMSB at Trough ({tr_dt}): BTC vs Bot={b_tr['vs_bot']:+.1f}% (vs Top={b_tr['vs_top']:+.1f}%) | ETH vs Bot={e_tr['vs_bot']:+.1f}%")
        log(f"    - Macro 45d Shift at Day ~36: 2Y Yield {dgs2_45d:.2f}% -> {dgs2_now:.2f}% ({dgs2_now-dgs2_45d:+.2f}%) | "
            f"10Y Yield {dgs10_45d:.2f}% -> {dgs10_now:.2f}% ({dgs10_now-dgs10_45d:+.2f}%)")

    log("\n" + "=" * 100)
    log("PART 5: HISTORICAL BACKTEST — WHAT HAPPENED WHEN SPY/QQQ TESTED/BROKE WEEKLY BMSB (2017-2026)?")
    log("=" * 100)
    # Find every week since 2017 where SPY or QQQ closed inside or below its Weekly BMSB after being above it
    spy_w = spy_bmsb.copy()
    qqq_w = qqq_bmsb.copy()
    btc_w = btc_bmsb.copy()

    common_w = spy_w.index.intersection(btc_w.index)
    spy_w = spy_w.loc[common_w]
    qqq_w = qqq_w.loc[common_w]
    btc_w = btc_w.loc[common_w]

    in_or_below = (spy_w["close"] <= spy_w["top"]) | (qqq_w["close"] <= qqq_w["top"])
    events = []
    last_i = -10
    for i in range(2, len(common_w)):
        if in_or_below.iloc[i] and (spy_w["close"].iloc[i-1] > spy_w["top"].iloc[i-1] or i - last_i >= 12):
            if i - last_i >= 8:
                last_i = i
                dt = common_w[i]
                b_now = btc_w["close"].iloc[i]
                s_now = spy_w["close"].iloc[i]
                b_4w = (btc_w["close"].iloc[i+4] / b_now - 1)*100 if i+4 < len(common_w) else np.nan
                b_13w = (btc_w["close"].iloc[i+13] / b_now - 1)*100 if i+13 < len(common_w) else np.nan
                b_26w = (btc_w["close"].iloc[i+26] / b_now - 1)*100 if i+26 < len(common_w) else np.nan
                s_13w = (spy_w["close"].iloc[i+13] / s_now - 1)*100 if i+13 < len(common_w) else np.nan
                log(f"  Week {dt.strftime('%Y-%m-%d')}: SPY vs BMSB_Top={spy_w['vs_top'].iloc[i]:+5.1f}% (Bot={spy_w['vs_bot'].iloc[i]:+5.1f}%) | "
                    f"QQQ vs Bot={qqq_w['vs_bot'].iloc[i]:+5.1f}% | BTC vs Bot={btc_w['vs_bot'].iloc[i]:+5.1f}% | "
                    f"Fwd BTC: +4w={b_4w:+5.1f}%, +13w={b_13w:+5.1f}%, +26w={b_26w:+5.1f}% (SPY +13w={s_13w:+5.1f}%)")

    OUT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nSaved to {OUT_PATH}")


if __name__ == "__main__":
    main()
