"""
Backtest:
  1. Benjamin Cowen's Bull Market Support Band (BMSB = 20-Week SMA & 21-Week EMA):
     - Exact current BMSB levels for BTC, ETH, ZEC, UNI, AAVE, NEAR, and Top-30 / Top-50 altcoins.
     - How does BMSB support strength compare across BTC vs ETH vs Altcoins?
     - When ETH & most alts break below BMSB first while BTC tests BMSB (like 2023 Aug-Sep and Today 2026 Nov),
       what happened next in 2019, 2020, 2021, 2023, 2024, 2025, 2026?
     - Specifically check 2023 Aug-Oct (when Cowen famously warned BTC would follow ETH/alts into a deeper
       second-half-of-pre-halving-year drop after breaking BMSB in Aug 2023, right before the Oct 2023 reversal).
  2. ZEC Deep Dive:
     - Can ZEC be bought now ($1,172 - $1,213), or has it not finished washing out?
     - Compare ZEC's current structure (distance to 50d MA, BMSB 20W SMA / 21W EMA, 100d MA, 200d MA,
       and pullback duration/depth) against every prior pullback in ZEC's 2025-2026 bull run.

Data:
  - data/universe/spot_usdt_1d_spliced.csv.gz
  - data/market.snapshot.duckdb (bn_kline_1d)
Date: 2026-11-09
"""

from pathlib import Path
import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
UNIVERSE_CSV = ROOT / "data" / "universe" / "spot_usdt_1d_spliced.csv.gz"
DB_PATH = ROOT / "data" / "market.snapshot.duckdb"
OUT_PATH = ROOT / "data" / "outputs" / "bmsb_cowen_and_zec_2026-11-09.txt"


def load_data():
    df = pd.read_csv(UNIVERSE_CSV, parse_dates=["t"])
    df["date"] = pd.to_datetime(df["t"]).dt.tz_localize(None)
    df["base"] = df["symbol"].str.replace("USDT$", "", regex=True)
    p = df.pivot_table(index="date", columns="base", values="c").sort_index()
    q = df.pivot_table(index="date", columns="base", values="qv").sort_index()

    # Also get full BTC & ETH history from 2017-08-17 in bn_kline_1d
    con = duckdb.connect(str(DB_PATH), read_only=True)
    k1d = con.execute("""
        SELECT symbol, CAST(ts AS TIMESTAMP) AS date, close, high, low
        FROM bn_kline_1d
        WHERE market = 'spot'
        ORDER BY symbol, date
    """).df()
    con.close()
    return p, q, k1d


def compute_weekly_bmsb(daily_series):
    """
    Resample daily close to weekly (Sunday close, standard crypto weekly candle 'W-SUN'),
    and compute:
      - 20W SMA (20-week simple moving average)
      - 21W EMA (21-week exponential moving average, adjust=False)
      - Band Top = max(20W SMA, 21W EMA)
      - Band Bot = min(20W SMA, 21W EMA)
    """
    w = daily_series.dropna().resample("W-SUN").last()
    sma20 = w.rolling(20).mean()
    ema21 = w.ewm(span=21, adjust=False).mean()
    out = pd.DataFrame({
        "close": w,
        "sma20": sma20,
        "ema21": ema21,
        "bmsb_top": np.maximum(sma20, ema21),
        "bmsb_bot": np.minimum(sma20, ema21),
        "bmsb_mid": 0.5 * (sma20 + ema21),
    })
    out["in_band"] = (out["close"] <= out["bmsb_top"]) & (out["close"] >= out["bmsb_bot"])
    out["above_band"] = out["close"] > out["bmsb_top"]
    out["below_band"] = out["close"] < out["bmsb_bot"]
    return out


def main():
    p, q, k1d = load_data()
    p = p.loc[:"2026-11-08"].copy()
    q = q.loc[:"2026-11-08"].copy()

    lines = []
    def log(s=""):
        print(s)
        lines.append(s)

    # Full BTC & ETH daily series from 2017-08-17 through 2026-11-08
    btc_full = k1d[k1d["symbol"] == "BTC"].set_index("date")["close"].loc[:"2026-11-08"]
    eth_full = k1d[k1d["symbol"] == "ETH"].set_index("date")["close"].loc[:"2026-11-08"]

    btc_w = compute_weekly_bmsb(btc_full)
    eth_w = compute_weekly_bmsb(eth_full)

    log("=" * 100)
    log("PART 1: BENJAMIN COWEN'S BULL MARKET SUPPORT BAND (BMSB = 20W SMA & 21W EMA) — CURRENT STATUS")
    log("=" * 100)
    log("Definition: Bull Market Support Band = 20-Week SMA and 21-Week EMA (on Weekly W-SUN closes).")

    for name, wdf in [("BTC", btc_w), ("ETH", eth_w)]:
        log(f"\n--- {name} Last 8 Weekly Closes vs Bull Market Support Band (BMSB) ---")
        for dt, r in wdf.iloc[-8:].iterrows():
            status = "ABOVE BAND" if r["above_band"] else ("IN BAND" if r["in_band"] else "BELOW BAND")
            dist_top = (r["close"] / r["bmsb_top"] - 1) * 100
            dist_bot = (r["close"] / r["bmsb_bot"] - 1) * 100
            log(
                f"  Week {dt.strftime('%Y-%m-%d')}: Close=${r['close']:>9,.2f} | "
                f"20W SMA=${r['sma20']:>9,.2f} | 21W EMA=${r['ema21']:>9,.2f} | "
                f"Band=[${r['bmsb_bot']:,.2f} .. ${r['bmsb_top']:,.2f}] | "
                f"Status={status:<10} (vs Top {dist_top:+5.1f}%, vs Bot {dist_bot:+5.1f}%)"
            )

    # Check BMSB status across Top-30 liquid altcoins + ZEC, UNI, AAVE, NEAR, SOL, BNB, XRP, DOGE on 2026-11-08
    STABLES = {
        "USDT", "USDC", "BUSD", "TUSD", "FDUSD", "DAI", "USDP", "USDD", "PYUSD", "USD1",
        "EUR", "EURI", "AEUR", "PAXG", "WBTC", "WBETH", "BETH", "STETH", "CBETH", "RETH", "WETH"
    }
    alt_cols = [c for c in p.columns if c not in STABLES and c not in ("BTC", "ETH") and not c.endswith(("UP", "DOWN", "BULL", "BEAR"))]
    vol30 = q[alt_cols].iloc[-31:-1].mean().dropna()
    top30 = vol30.nlargest(30).index.tolist()

    abv_cnt, in_cnt, bel_cnt = 0, 0, 0
    abv_list, in_list, bel_list = [], [], []
    for c in top30:
        cw = compute_weekly_bmsb(p[c])
        last_r = cw.iloc[-1]
        if pd.isna(last_r["sma20"]):
            continue
        if last_r["above_band"]:
            abv_cnt += 1
            abv_list.append(c)
        elif last_r["in_band"]:
            in_cnt += 1
            in_list.append(c)
        else:
            bel_cnt += 1
            bel_list.append(c)

    tot_valid = abv_cnt + in_cnt + bel_cnt
    log(f"\n--- Top-30 Liquid Altcoins vs BMSB on 2026-11-08 (n={tot_valid}) ---")
    log(f"  * ABOVE BMSB ({abv_cnt}/{tot_valid} = {abv_cnt/tot_valid*100:.1f}%): {abv_list}")
    log(f"  * IN BMSB    ({in_cnt}/{tot_valid} = {in_cnt/tot_valid*100:.1f}%): {in_list}")
    log(f"  * BELOW BMSB ({bel_cnt}/{tot_valid} = {bel_cnt/tot_valid*100:.1f}%): {bel_list}")

    log("\n--- Key Focus Coins vs BMSB (20W SMA & 21W EMA) & Daily MAs on 2026-11-08 ---")
    for c in ["BTC", "ETH", "ZEC", "UNI", "AAVE", "NEAR", "BNB", "SOL", "LINK", "ENA"]:
        s = p[c].dropna()
        cw = compute_weekly_bmsb(s)
        lr = cw.iloc[-1]
        ma50 = s.iloc[-50:].mean()
        ma100 = s.iloc[-100:].mean()
        ma200 = s.iloc[-200:].mean()
        st = "ABOVE BMSB" if lr["above_band"] else ("IN BMSB" if lr["in_band"] else "BELOW BMSB")
        log(
            f"  * {c:<5}: Close=${lr['close']:>9.2f} | "
            f"BMSB=[${lr['bmsb_bot']:>8.2f} (20W/21W) .. ${lr['bmsb_top']:>8.2f}] -> {st:<10} "
            f"(vs Bot {(lr['close']/lr['bmsb_bot']-1)*100:+5.1f}%, vs Top {(lr['close']/lr['bmsb_top']-1)*100:+5.1f}%) | "
            f"Daily: 50dMA=${ma50:.2f} ({(lr['close']/ma50-1)*100:+5.1f}%), 100dMA=${ma100:.2f} ({(lr['close']/ma100-1)*100:+5.1f}%), 200dMA=${ma200:.2f} ({(lr['close']/ma200-1)*100:+5.1f}%)"
        )

    # =========================================================================
    # PART 2: DOES BMSB HOLD BETTER FOR BTC THAN FOR ETH & ALTCOINS?
    #         AND WHAT HAPPENED IN 2023 WHEN ETH BROKE BMSB FIRST AND BTC FOLLOWED?
    # =========================================================================
    log("\n" + "=" * 100)
    log("PART 2: HISTORICAL TEST OF BMSB FOR BTC vs ETH vs ALTS & THE 2023 BENJAMIN COWEN ANALOG")
    log("=" * 100)

    # 1. Proportion of weeks spent ABOVE BMSB in recovery/bull years (2019-2020, 2023-2024, 2025-2026)
    for period_name, w_start, w_end in [
        ("2019-2020 Recovery/Pre-Halving", "2019-04-01", "2020-10-01"),
        ("2023-2024 Recovery/Bull", "2023-01-15", "2024-11-01"),
        ("2025-2026 Current Cycle", "2025-01-01", "2026-11-08"),
    ]:
        b_sub = btc_w.loc[w_start:w_end]
        e_sub = eth_w.loc[w_start:w_end]
        log(
            f"  * [{period_name}]: "
            f"BTC Above/In BMSB = {((b_sub['above_band'] | b_sub['in_band']).mean()*100):.1f}% of weeks "
            f"(Below = {(b_sub['below_band'].mean()*100):.1f}%) | "
            f"ETH Above/In BMSB = {((e_sub['above_band'] | e_sub['in_band']).mean()*100):.1f}% of weeks "
            f"(Below = {(e_sub['below_band'].mean()*100):.1f}%)"
        )

    # 2A. All 10 historical events where BTC first closed below BMSB bottom after >=4 weeks in/above band (2018-2026)
    log("\n--- All Historical Events Where BTC First Closed Below BMSB Bottom After >=4w In/Above Band (2018-2026, n=10) ---")
    above_streak = 0
    for loc_i, dt in enumerate(btc_w.index):
        b_r = btc_w.loc[dt]
        if pd.isna(b_r["bmsb_bot"]):
            continue
        is_above_or_in = not b_r["below_band"]
        if (not is_above_or_in) and above_streak >= 4 and dt >= pd.Timestamp("2018-01-01"):
            e_r = eth_w.loc[dt]
            b_vs_bot = (b_r["close"] / b_r["bmsb_bot"] - 1) * 100
            e_vs_bot = (e_r["close"] / e_r["bmsb_bot"] - 1) * 100
            e_tag = "ABOVE" if e_vs_bot >= 0 else "BELOW"
            win_13w = btc_w.iloc[loc_i + 1 : min(loc_i + 14, len(btc_w))]
            min_13w = (win_13w["close"].min() / b_r["close"] - 1) * 100 if len(win_13w) else 0.0
            ret_26w = (btc_w["close"].iloc[loc_i + 26] / b_r["close"] - 1) * 100 if loc_i + 26 < len(btc_w) else float("nan")
            ret_52w = (btc_w["close"].iloc[loc_i + 52] / b_r["close"] - 1) * 100 if loc_i + 52 < len(btc_w) else float("nan")
            log(
                f"  Week {dt.strftime('%Y-%m-%d')} (streak={above_streak:2d}w) | BTC=${b_r['close']:>9,.1f} ({b_vs_bot:+5.1f}% vs Bot) | "
                f"ETH vs Bot={e_vs_bot:+6.1f}% ({e_tag}) | 13w MinClose={min_13w:+5.1f}% | +26w={ret_26w:+6.1f}% | +52w={ret_52w:+6.1f}%"
            )
        if is_above_or_in:
            above_streak += 1
        else:
            above_streak = 0

    # 2B. Every time BTC was ABOVE BMSB for >=5 of 6 weeks and then entered/touched or dipped slightly below BMSB
    #    WHILE ETH was ALREADY BELOW its BMSB!
    log("\n--- Every Historical Episode Where BTC Tested/Entered/Dipped Into BMSB While ETH Was ALREADY Below BMSB ---")
    btc_abv_streak = btc_w["above_band"].rolling(6).sum().shift(1) >= 5
    for dt in btc_w.index:
        if dt < pd.Timestamp("2018-01-01"):
            continue
        if not btc_abv_streak.loc[dt]:
            continue
        # BTC touches band or dips within -8% of band top, while ETH is below its band
        b_r = btc_w.loc[dt]
        e_r = eth_w.loc[dt]
        if (b_r["close"] <= b_r["bmsb_top"] * 1.01) and e_r["below_band"]:
            loc_i = btc_w.index.get_loc(dt)
            # Check forward 4w, 8w, 13w, 26w BTC returns & max drawdown
            f_str = ""
            for w_h, lbl in [(4, "4w"), (8, "8w"), (13, "13w"), (26, "26w")]:
                if loc_i + w_h < len(btc_w.index):
                    b_ret = (btc_w["close"].iloc[loc_i + w_h] / b_r["close"] - 1) * 100
                    f_str += f" | +{lbl}: {b_ret:+6.1f}%"
            # Also check how many weeks BTC spent below BMSB before reclaiming (or if it entered bear market)
            win_13w = btc_w.iloc[loc_i : min(loc_i + 14, len(btc_w))]
            min_13w = (win_13w["close"].min() / b_r["close"] - 1) * 100
            log(
                f"  Week {dt.strftime('%Y-%m-%d')}: BTC=${b_r['close']:>8,.0f} (vs BMSB_Bot {(b_r['close']/b_r['bmsb_bot']-1)*100:+5.1f}%), "
                f"ETH vs BMSB_Bot {(e_r['close']/e_r['bmsb_bot']-1)*100:+5.1f}% | Next 13w MaxDD={min_13w:+5.1f}%{f_str}"
            )

    # 3. Zoom in on 2023 Summer-Autumn (July - Oct 2023) week-by-week for BTC & ETH vs BMSB!
    log("\n--- 2023 Summer-Autumn Week-by-Week Replay (When Cowen Called for Bear Return as ETH & BTC Broke BMSB) ---")
    for dt in btc_w.loc["2023-07-09":"2023-11-05"].index:
        br = btc_w.loc[dt]
        er = eth_w.loc[dt]
        b_st = "ABOVE" if br["above_band"] else ("IN_BAND" if br["in_band"] else "BELOW")
        e_st = "ABOVE" if er["above_band"] else ("IN_BAND" if er["in_band"] else "BELOW")
        log(
            f"  {dt.strftime('%Y-%m-%d')} | BTC=${br['close']:>7,.0f} [{b_st:<7} BMSB=${br['bmsb_bot']:,.0f}-${br['bmsb_top']:,.0f}] | "
            f"ETH=${er['close']:>6,.0f} [{e_st:<7} BMSB=${er['bmsb_bot']:,.0f}-${er['bmsb_top']:,.0f}]"
        )

    # Also check 2024 Summer (July - Oct 2024) week-by-week for BTC & ETH vs BMSB!
    log("\n--- 2024 Summer-Autumn Week-by-Week Replay (When ETH Broke BMSB in June 2024 and BTC Dipped Below in July-Sep 2024) ---")
    for dt in btc_w.loc["2024-06-16":"2024-10-27"].index:
        br = btc_w.loc[dt]
        er = eth_w.loc[dt]
        b_st = "ABOVE" if br["above_band"] else ("IN_BAND" if br["in_band"] else "BELOW")
        e_st = "ABOVE" if er["above_band"] else ("IN_BAND" if er["in_band"] else "BELOW")
        log(
            f"  {dt.strftime('%Y-%m-%d')} | BTC=${br['close']:>7,.0f} [{b_st:<7} BMSB=${br['bmsb_bot']:,.0f}-${br['bmsb_top']:,.0f}] | "
            f"ETH=${er['close']:>6,.0f} [{e_st:<7} BMSB=${er['bmsb_bot']:,.0f}-${er['bmsb_top']:,.0f}]"
        )

    # =========================================================================
    # PART 3: ZEC DEEP DIVE — CAN YOU BUY ZEC NOW OR HAS IT NOT WASHED OUT ENOUGH?
    # =========================================================================
    log("\n" + "=" * 100)
    log("PART 3: ZEC DEEP DIVE — HAS ZEC WASHED OUT ENOUGH TO BUY NOW?")
    log("=" * 100)

    zec = p["ZEC"].dropna()
    zec_w = compute_weekly_bmsb(zec)
    zec_k = k1d[k1d["symbol"] == "ZEC"].set_index("date").loc["2025-08-01":]

    log("\n--- ZEC Last 8 Weekly Closes vs Weekly BMSB (20W SMA & 21W EMA) ---")
    for dt, r in zec_w.iloc[-8:].iterrows():
        log(
            f"  Week {dt.strftime('%Y-%m-%d')}: Close=${r['close']:>8.2f} | "
            f"20W SMA=${r['sma20']:>7.2f} | 21W EMA=${r['ema21']:>7.2f} | "
            f"vs BMSB Top={(r['close']/r['bmsb_top']-1)*100:+5.1f}%"
        )

    # Compare the 3 major corrections of ZEC during its 2025-2026 bull run:
    # Wave 1 peak -> trough (Nov 2025 -> Feb/Mar 2026)
    # Wave 2 peak -> trough (May 2026 -> Jul 2026)
    # Wave 3 peak -> current (Sep 26, 2026 -> Nov 9, 2026)
    z_ma50 = zec.rolling(50).mean()
    z_ma100 = zec.rolling(100).mean()
    z_ma200 = zec.rolling(200).mean()

    log("\n--- Comparing ZEC's 3 Major Pullbacks in This Bull Cycle (2025.10 – 2026.11) ---")
    waves = [
        ("Wave 1 Correction (2025-11-17 Peak to 2026-03-07 Trough)", "2025-10-01", "2025-12-01", "2026-03-20"),
        ("Wave 2 Correction (2026-05-11 Peak to 2026-06-04 / 07-04 Trough)", "2026-04-01", "2026-05-25", "2026-08-05"),
        ("Wave 3 Current Correction (2026-09-26 Peak to Today 2026-11-08)", "2026-08-15", "2026-10-05", "2026-11-08"),
    ]
    for title, w_s, pk_end, tr_end in waves:
        pk_sub = zec.loc[w_s:pk_end]
        pk_val = pk_sub.max()
        pk_dt = pk_sub.idxmax()
        tr_sub = zec.loc[pk_dt:tr_end]
        tr_val = tr_sub.min()
        tr_dt = tr_sub.idxmin()
        dur = (tr_dt - pk_dt).days
        dd = (tr_val / pk_val - 1) * 100

        # Also check the lowest daily LOW (wick) in k1d
        k_sub = zec_k.loc[pk_dt:tr_end]
        pk_high = zec_k.loc[w_s:pk_end, "high"].max()
        tr_low = k_sub["low"].min()
        tr_low_dt = k_sub["low"].idxmin()
        wick_dd = (tr_low / pk_high - 1) * 100

        # At trough date, where was ZEC relative to 50dMA, 100dMA, 200dMA, and Weekly 20W SMA / 21W EMA?
        d50 = (tr_val / z_ma50.loc[tr_dt] - 1) * 100
        d100 = (tr_val / z_ma100.loc[tr_dt] - 1) * 100
        d200 = (tr_val / z_ma200.loc[tr_dt] - 1) * 100
        w_idx = zec_w.index.asof(tr_dt)
        d_bmsb = (tr_val / zec_w.loc[w_idx, "bmsb_top"] - 1) * 100 if pd.notna(w_idx) else np.nan

        log(f"  * {title}:")
        log(f"    - Close Peak: ${pk_val:.2f} ({pk_dt.strftime('%Y-%m-%d')}) -> Close Trough: ${tr_val:.2f} ({tr_dt.strftime('%Y-%m-%d')}, {dur} days, Close DD={dd:+.1f}%)")
        log(f"    - Intraday High: ${pk_high:.2f} -> Intraday Low: ${tr_low:.2f} ({tr_low_dt.strftime('%Y-%m-%d')}, Wick DD={wick_dd:+.1f}%)")
        log(f"    - Trough vs MAs: vs 50dMA={d50:+.1f}% | vs 100dMA={d100:+.1f}% | vs BMSB(20W/21W)={d_bmsb:+.1f}% | vs 200dMA={d200:+.1f}%")

    # Also check today's (2026-11-08 & 2026-11-09) exact ZEC support levels:
    cur_z = zec.iloc[-1]
    cur_dt = zec.index[-1]
    w_now = zec_w.iloc[-1]
    log(f"\n--- ZEC Exact Support Levels Today ({cur_dt.strftime('%Y-%m-%d')}, Close=${cur_z:.2f}, 11-09 Live=${zec_k['close'].iloc[-1]:.2f}) ---")
    log(f"  * 50-Day MA         : ${z_ma50.iloc[-1]:.2f} (ZEC is {(cur_z/z_ma50.iloc[-1]-1)*100:+.1f}%)")
    log(f"  * 100-Day MA        : ${z_ma100.iloc[-1]:.2f} (ZEC is {(cur_z/z_ma100.iloc[-1]-1)*100:+.1f}%)")
    log(f"  * 21-Week EMA (BMSB): ${w_now['ema21']:.2f} (ZEC is {(cur_z/w_now['ema21']-1)*100:+.1f}%)")
    log(f"  * 20-Week SMA (BMSB): ${w_now['sma20']:.2f} (ZEC is {(cur_z/w_now['sma20']-1)*100:+.1f}%)")
    log(f"  * 200-Day MA        : ${z_ma200.iloc[-1]:.2f} (ZEC is {(cur_z/z_ma200.iloc[-1]-1)*100:+.1f}%)")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"\nSaved full output to {OUT_PATH}")


if __name__ == "__main__":
    main()
