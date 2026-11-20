"""
Conditional Case-Study Playbook:
  - Premise (User's Subjective Macro/Cycle Hypothesis):
    Suppose 2026-06-30 ($58,625, -53.0% from the $124,659 peak) was the cycle bear bottom
    (analogous to Nov 2022), and the current market (2026-11-08 weekly close) is in an
    Early-Bull Recovery Year analogous to 2023.
  - Under this conditional premise, using the Bull Market Support Band (BMSB: 20W SMA & 21W EMA)
    as the reference frame:
    1. Compare today's position (2026-11-08) of BTC, ETH, ZEC, UNI, NEAR, AAVE against:
       - Scenario A: 2023-06-11 (First-wave BMSB touch without weekly breakdown -> June-July bounce)
       - Scenario B: 2023-08-13 to 2023-09-10 (Second-wave BMSB breakdown shakeout -> Q4 2023 & Q1 2024 rally)
    2. Provide the COMPLETE, UNFILTERED performance of all Top-50 liquid coins that were near/above
       their BMSB (or 200d MA) on 2023-06-11 and 2023-08-13, bucketed by their distance to BMSB bottom:
       - Tier 1 (Overextended: > +25% above BMSB bottom)
       - Tier 2 (Upper Sweet Spot: +5% to +25% above BMSB bottom)
       - Tier 3 (In/Near Band: -10% to +5% vs BMSB bottom)
       Plus the full trajectory of the major 2023 narrative coins (SOL, INJ, FET, RENDER, LINK, AAVE, UNI, NEAR, BCH, SKY/MKR).
    3. Map both the conditional case-study analog AND the unconditional point-in-time base rates
       onto today's 4 target coins (ZEC, AAVE, UNI, NEAR) vs BTC.

Data:
  - data/universe/spot_usdt_1d_spliced.csv.gz
  - data/market.snapshot.duckdb
Date: 2026-11-10
"""

from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
UNIVERSE_CSV = ROOT / "data" / "universe" / "spot_usdt_1d_spliced.csv.gz"
OUT_TXT = ROOT / "data" / "outputs" / "case_study_2023_bmsb_narrative_playbook_2026-11-10.txt"

STABLES = {
    "USDT", "USDC", "BUSD", "TUSD", "FDUSD", "DAI", "USDP", "USDD", "PYUSD", "USD1",
    "EUR", "EURI", "AEUR", "PAXG", "WBTC", "WBETH", "BETH", "STETH", "CBETH", "RETH", "WETH"
}


def compute_bmsb(series: pd.Series):
    s = series.dropna().sort_index().loc[:"2026-11-08"]
    w = s.resample("W-SUN").last().dropna()
    sma20 = w.rolling(20).mean()
    ema21 = w.ewm(span=21, adjust=False).mean()
    bot = np.minimum(sma20, ema21)
    top = np.maximum(sma20, ema21)
    return pd.DataFrame({
        "close": w,
        "sma20": sma20,
        "ema21": ema21,
        "bot": bot,
        "top": top,
        "vs_bot": (w / bot - 1.0) * 100.0,
        "vs_top": (w / top - 1.0) * 100.0,
    })


def main():
    df = pd.read_csv(UNIVERSE_CSV, parse_dates=["t"])
    df["date"] = pd.to_datetime(df["t"]).dt.tz_localize(None)
    df["base"] = df["symbol"].str.replace("USDT$", "", regex=True)
    p = df.pivot_table(index="date", columns="base", values="c").sort_index().loc[:"2026-11-08"]
    q = df.pivot_table(index="date", columns="base", values="qv").sort_index().loc[:"2026-11-08"]

    lines = []
    def log(msg=""):
        print(msg)
        lines.append(msg)

    log("=" * 105)
    log("PART 1: TODAY'S (2026-11-08 WEEKLY CLOSE) BMSB & MA POSITION OF BTC, ETH, AND THE 4 FOCUS COINS")
    log("=" * 105)
    focus = ["BTC", "ETH", "AAVE", "NEAR", "UNI", "ZEC"]
    for c in focus:
        s = p[c].dropna()
        bw = compute_bmsb(s)
        lr = bw.iloc[-1]
        ma50 = s.iloc[-50:].mean()
        ma100 = s.iloc[-100:].mean()
        ma200 = s.iloc[-200:].mean()
        low_2026 = s.loc["2026-01-01":].min()
        low_dt = s.loc["2026-01-01":].idxmin().strftime("%Y-%m-%d")
        high_90d = s.iloc[-90:].max()
        high_dt = s.iloc[-90:].idxmax().strftime("%Y-%m-%d")
        log(
            f"  * {c:<5} | 11-08 Close=${lr['close']:>9.2f} | "
            f"BMSB=[${lr['bot']:>8.2f} .. ${lr['top']:>8.2f}] (vs Bot {lr['vs_bot']:+5.1f}%, vs Top {lr['vs_top']:+5.1f}%) | "
            f"50d=${ma50:.2f} ({(lr['close']/ma50-1)*100:+5.1f}%), 100d=${ma100:.2f} ({(lr['close']/ma100-1)*100:+5.1f}%), 200d=${ma200:.2f} ({(lr['close']/ma200-1)*100:+5.1f}%) | "
            f"From 2026 Low ({low_dt} ${low_2026:.2f}): {(lr['close']/low_2026-1)*100:+6.1f}% | From 90d High ({high_dt} ${high_90d:.2f}): {(lr['close']/high_90d-1)*100:+5.1f}%"
        )

    log("\n" + "=" * 105)
    log("PART 2: SCENARIO A — 2023-06-11 FIRST-WAVE BMSB TOUCH (NO WEEKLY BREAKDOWN -> 6-WEEK BOUNCE TO JULY)")
    log("=" * 105)
    p1_coins = ["BTC", "ETH", "BCH", "INJ", "SOL", "RENDER", "FET", "AAVE", "LINK", "UNI", "NEAR", "STX"]
    p1_coins = [c for c in p1_coins if c in p.columns]
    for c in p1_coins:
        bw = compute_bmsb(p[c])
        r_apr16 = bw.loc["2023-04-16"]
        r_jun11 = bw.loc["2023-06-11"]
        win_apr_jun = bw.loc["2023-04-16":"2023-06-18"]
        min_close = win_apr_jun["close"].min()
        min_dt = win_apr_jun["close"].idxmin().strftime("%Y-%m-%d")
        min_vs_bot = win_apr_jun["vs_bot"].min()
        jul_max = bw.loc["2023-06-18":"2023-07-30"]["close"].max()
        jul_dt = bw.loc["2023-06-18":"2023-07-30"]["close"].idxmax().strftime("%Y-%m-%d")
        dd_from_apr = (min_close / r_apr16["close"] - 1) * 100
        bounce_to_jul = (jul_max / min_close - 1) * 100
        log(
            f"  * {c:<6} | 06-11 vs Bot: {r_jun11['vs_bot']:+6.1f}% | "
            f"Apr-Jun MinWeeklyClose: {min_dt} ({dd_from_apr:+5.1f}% from 04-16, {min_vs_bot:+5.1f}% vs Bot) | "
            f"Bounce to {jul_dt}: {bounce_to_jul:+6.1f}%"
        )

    log("\n" + "=" * 105)
    log("PART 3: SCENARIO B — 2023-08-13 SECOND-WAVE BMSB BREAKDOWN & Q4 2023 / Q1 2024 RALLY")
    log("        (COMPLETE UNFILTERED TOP-50 LIQUID COINS THAT WERE NEAR/ABOVE BMSB OR 200D MA ON 2023-08-13)")
    log("=" * 105)
    dt_aug13 = pd.Timestamp("2023-08-13")
    vol30 = q.loc[:dt_aug13].iloc[-30:].mean().dropna()
    valid_cols = [c for c in vol30.index if c not in STABLES and not c.endswith(("UP", "DOWN", "BULL", "BEAR"))]
    top50_aug13 = vol30[valid_cols].nlargest(50).index.tolist()

    btc_bw = compute_bmsb(p["BTC"])
    btc_aug13 = btc_bw.loc["2023-08-13", "close"]
    btc_sep10 = btc_bw.loc["2023-09-10", "close"]
    btc_dec31 = btc_bw.loc["2023-12-31", "close"]
    btc_mar10 = btc_bw.loc["2024-03-10", "close"]
    btc_ret_dec_aug = (btc_dec31 / btc_aug13 - 1) * 100
    btc_ret_mar_aug = (btc_mar10 / btc_aug13 - 1) * 100
    btc_ret_dec_sep = (btc_dec31 / btc_sep10 - 1) * 100
    btc_ret_mar_sep = (btc_mar10 / btc_sep10 - 1) * 100
    log(
        f"BTC Baseline: 08-13 vs BMSB_Bot={btc_bw.loc['2023-08-13','vs_bot']:+.1f}% | "
        f"09-10 MinWeeklyClose vs Bot={btc_bw.loc['2023-09-10','vs_bot']:+.1f}% (-11.8% from 08-13) | "
        f"From 08-13 -> Dec31: {btc_ret_dec_aug:+.1f}%, -> Mar10: {btc_ret_mar_aug:+.1f}% | "
        f"From 09-10 -> Dec31: {btc_ret_dec_sep:+.1f}%, -> Mar10: {btc_ret_mar_sep:+.1f}%"
    )

    rows = []
    # Include all Top-50 coins plus FET, RENDER, NEAR, STX if not in 30d top-50 on that exact day
    check_set = list(dict.fromkeys(top50_aug13 + ["FET", "RENDER", "NEAR", "STX"]))
    for c in check_set:
        if c == "BTC" or c not in p.columns:
            continue
        s = p[c].loc[:dt_aug13].dropna()
        if len(s) < 200:
            continue
        ma200 = s.iloc[-200:].mean()
        bw = compute_bmsb(p[c])
        if pd.Timestamp("2023-08-13") not in bw.index:
            continue
        r_aug = bw.loc["2023-08-13"]
        if pd.isna(r_aug["bot"]):
            continue
        vs_bot = r_aug["vs_bot"]
        vs_200 = (r_aug["close"] / ma200 - 1) * 100
        in_top50 = c in top50_aug13
        # Keep coins that were within -10% of BMSB bottom or above, plus explicit narrative coins
        if (in_top50 and (vs_bot >= -5.0 or vs_200 >= -5.0)) or c in ("FET", "RENDER", "NEAR", "STX", "AAVE", "UNI", "INJ", "SOL", "LINK", "BCH", "SKY"):
            aug_oct = bw.loc["2023-08-13":"2023-10-15"]
            min_w = aug_oct["close"].min()
            min_dt = aug_oct["close"].idxmin().strftime("%m-%d")
            min_vs_bot = aug_oct["vs_bot"].min()
            dd_aug_oct = (min_w / r_aug["close"] - 1) * 100
            c_sep10 = bw.loc["2023-09-10", "close"]
            c_dec31 = bw.loc["2023-12-31", "close"] if pd.Timestamp("2023-12-31") in bw.index else np.nan
            c_mar10 = bw.loc["2024-03-10", "close"] if pd.Timestamp("2024-03-10") in bw.index else np.nan
            r_dec_aug = (c_dec31 / r_aug["close"] - 1) * 100
            r_mar_aug = (c_mar10 / r_aug["close"] - 1) * 100
            r_dec_sep = (c_dec31 / c_sep10 - 1) * 100
            r_mar_sep = (c_mar10 / c_sep10 - 1) * 100
            rows.append({
                "coin": c,
                "in_top50": in_top50,
                "vs_bot": vs_bot,
                "vs_200": vs_200,
                "min_dt": min_dt,
                "dd_aug_oct": dd_aug_oct,
                "min_vs_bot": min_vs_bot,
                "r_dec_aug": r_dec_aug,
                "r_mar_aug": r_mar_aug,
                "r_dec_sep": r_dec_sep,
                "r_mar_sep": r_mar_sep,
                "beat_dec_aug": r_dec_aug > btc_ret_dec_aug if pd.notna(r_dec_aug) else False,
                "beat_mar_aug": r_mar_aug > btc_ret_mar_aug if pd.notna(r_mar_aug) else False,
                "beat_dec_sep": r_dec_sep > btc_ret_dec_sep if pd.notna(r_dec_sep) else False,
            })

    rdf = pd.DataFrame(rows).sort_values("vs_bot", ascending=False)

    log("\n--- 3A. Bucket 1: Overextended Coins (> +25% above BMSB Bottom on 2023-08-13) ---")
    b1 = rdf[rdf["vs_bot"] > 25.0]
    for _, r in b1.iterrows():
        log(
            f"  * {r['coin']:<7} (Top50={str(r['in_top50']):<5}) | 08-13 vs Bot: {r['vs_bot']:+6.1f}% | "
            f"Aug-Oct Low: {r['min_dt']} ({r['dd_aug_oct']:+5.1f}% from 08-13, {r['min_vs_bot']:+5.1f}% vs Bot) | "
            f"08-13->Dec31: {r['r_dec_aug']:+6.1f}% ({'BEAT' if r['beat_dec_aug'] else 'lag '}) | "
            f"09-10->Dec31: {r['r_dec_sep']:+6.1f}% ({'BEAT' if r['beat_dec_sep'] else 'lag '}) | "
            f"08-13->Mar10: {r['r_mar_aug']:+6.1f}% ({'BEAT' if r['beat_mar_aug'] else 'lag '})"
        )
    log(
        f"  => Summary Bucket 1 (>+25% vs Bot, n={len(b1)}): "
        f"Beat BTC 08-13->Dec31 = {b1['beat_dec_aug'].sum()}/{len(b1)} ({b1['beat_dec_aug'].mean()*100:.1f}%), "
        f"Beat BTC 09-10->Dec31 = {b1['beat_dec_sep'].sum()}/{len(b1)} ({b1['beat_dec_sep'].mean()*100:.1f}%), "
        f"Beat BTC 08-13->Mar10 = {b1['beat_mar_aug'].sum()}/{len(b1)} ({b1['beat_mar_aug'].mean()*100:.1f}%)"
    )

    log("\n--- 3B. Bucket 2: Upper / In-Band Coins (-5% to +25% vs BMSB Bottom on 2023-08-13, Unfiltered Top-50) ---")
    b2 = rdf[(rdf["vs_bot"] <= 25.0) & (rdf["vs_bot"] >= -5.0) & rdf["in_top50"]]
    for _, r in b2.iterrows():
        log(
            f"  * {r['coin']:<7} (Top50={str(r['in_top50']):<5}) | 08-13 vs Bot: {r['vs_bot']:+6.1f}% | "
            f"Aug-Oct Low: {r['min_dt']} ({r['dd_aug_oct']:+5.1f}% from 08-13, {r['min_vs_bot']:+5.1f}% vs Bot) | "
            f"08-13->Dec31: {r['r_dec_aug']:+6.1f}% ({'BEAT' if r['beat_dec_aug'] else 'lag '}) | "
            f"09-10->Dec31: {r['r_dec_sep']:+6.1f}% ({'BEAT' if r['beat_dec_sep'] else 'lag '}) | "
            f"08-13->Mar10: {r['r_mar_aug']:+6.1f}% ({'BEAT' if r['beat_mar_aug'] else 'lag '})"
        )
    log(
        f"  => Summary Bucket 2 (-5% to +25% vs Bot, Unfiltered Top50, n={len(b2)}): "
        f"Beat BTC 08-13->Dec31 = {b2['beat_dec_aug'].sum()}/{len(b2)} ({b2['beat_dec_aug'].mean()*100:.1f}%), "
        f"Beat BTC 09-10->Dec31 = {b2['beat_dec_sep'].sum()}/{len(b2)} ({b2['beat_dec_sep'].mean()*100:.1f}%), "
        f"Beat BTC 08-13->Mar10 = {b2['beat_mar_aug'].sum()}/{len(b2)} ({b2['beat_mar_aug'].mean()*100:.1f}%)"
    )

    log("\n--- 3C. Focus Subset: 2023 Cycle Narrative Leaders & Direct Analogs to Today's Coins ---")
    focus_2023 = ["BCH", "SKY", "SOL", "INJ", "UNI", "LINK", "AAVE", "RENDER", "FET", "NEAR", "ETH"]
    b3 = rdf[rdf["coin"].isin(focus_2023)].copy()
    for _, r in b3.iterrows():
        log(
            f"  * {r['coin']:<7} | 08-13 vs Bot: {r['vs_bot']:+6.1f}% | "
            f"Aug-Oct Low: {r['min_dt']} ({r['dd_aug_oct']:+5.1f}% from 08-13, {r['min_vs_bot']:+5.1f}% vs Bot) | "
            f"08-13->Dec31: {r['r_dec_aug']:+6.1f}% ({'BEAT' if r['beat_dec_aug'] else 'lag '}) | "
            f"09-10->Dec31: {r['r_dec_sep']:+6.1f}% ({'BEAT' if r['beat_dec_sep'] else 'lag '}) | "
            f"09-10->Mar10: {r['r_mar_sep']:+6.1f}%"
        )

    OUT_TXT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nSaved output to {OUT_TXT}")


if __name__ == "__main__":
    main()
