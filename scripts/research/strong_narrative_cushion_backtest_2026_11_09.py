"""
Backtest: "Thick Cushion / Strong Narrative" Pullback Coins (e.g. AAVE, UNI, ZEC) vs BTC and vs Laggards
========================================================================================================
Question:
  1. What are the exact current metrics of AAVE, UNI, ZEC (and other strong-cushion narrative coins)
     relative to their 1-year / cycle lows, 90d highs, 50d MA, and 200d MA (both in USD and vs BTC)?
  2. When the market pulls back, do coins with a "thick cushion" above their 1-year low (or above 200d MA /
     top relative strength over 180d-365d) that have pulled back -20%..-45% from their 90d high
     outperform:
       (a) Holding BTC?
       (b) Buying beaten-down laggards (near 1-year lows)?
     Or do "thick cushion" coins suffer catch-down / profit-taking liquidation (补跌) in weak alt regimes?

Data:
  - data/universe/spot_usdt_1d_spliced.csv.gz (573 coins including 177 delisted, no survivorship bias)
  - data/market.snapshot.duckdb (bn_kline_1d through 2026-11-09)

Date: 2026-11-09
"""

from pathlib import Path
import numpy as np
import pandas as pd
import duckdb

ROOT = Path(__file__).resolve().parents[2]
UNIVERSE_CSV = ROOT / "data" / "universe" / "spot_usdt_1d_spliced.csv.gz"
DB_PATH = ROOT / "data" / "market.snapshot.duckdb"
OUT_PATH = ROOT / "data" / "outputs" / "strong_narrative_cushion_backtest_2026-11-09.txt"


def load_data():
    df = pd.read_csv(UNIVERSE_CSV, parse_dates=["t"])
    df["date"] = pd.to_datetime(df["t"]).dt.tz_localize(None)
    df["base"] = df["symbol"].str.replace("USDT$", "", regex=True)
    df["close"] = df["c"]
    df["qvol"] = df["qv"]

    df2 = df[["base", "date", "close", "qvol"]].copy()
    comb = df2.sort_values(["base", "date"]).drop_duplicates(["base", "date"], keep="last")

    p = comb.pivot(index="date", columns="base", values="close").sort_index()
    q = comb.pivot(index="date", columns="base", values="qvol").sort_index()
    return p, q


def decluster(dates, min_gap_days=15):
    out = []
    last = None
    for d in sorted(dates):
        if last is None or (d - last).days >= min_gap_days:
            out.append(d)
            last = d
    return out


def main():
    p_all, q_all = load_data()
    p = p_all.loc[: "2026-11-08"].copy()
    q = q_all.loc[: "2026-11-08"].copy()

    STABLES = {
        "USDT", "USDC", "BUSD", "TUSD", "FDUSD", "DAI", "USDP", "USDD", "PYUSD", "USD1",
        "EUR", "EURI", "AEUR", "GBP", "AUD", "TRY", "BRL", "BIDR", "IDRT", "UAH", "RUB", "NGN", "PLN", "RON", "ZAR", "JPY",
        "PAXG", "WBTC", "WBETH", "BETH", "STETH", "CBETH", "RETH",
        "BTCUP", "BTCDOWN", "ETHUP", "ETHDOWN", "BNBUP", "BNBDOWN", "ADAUP", "ADADOWN", "LINKUP", "LINKDOWN",
        "XRPUP", "XRPDOWN", "DOTUP", "DOTDOWN", "LTCUP", "LTCDOWN", "EOSUP", "EOSDOWN", "TRXUP", "TRXDOWN",
        "UNIUP", "UNIDOWN", "SXPUP", "SXPDOWN", "FILUP", "FILDOWN", "YFIUP", "YFIDOWN", "XTZUP", "XTZDOWN",
        "AAVEUP", "AAVEDOWN", "SUSHIUP", "SUSHIDOWN", "1INCHUP", "1INCHDOWN", "BCHUP", "BCHDOWN", "XLMDOWN", "XLMUP"
    }
    alt_cols = [c for c in p.columns if c not in STABLES and c not in ("BTC", "ETH") and not c.endswith(("UP", "DOWN", "BULL", "BEAR"))]

    lines = []
    def log(s=""):
        print(s)
        lines.append(s)

    log("=" * 95)
    log("PART 1: CURRENT SNAPSHOT OF AAVE, UNI, ZEC & NARRATIVE / BLUE-CHIP COINS (AS OF 2026-11-08)")
    log("=" * 95)

    focus_coins = ["ZEC", "AAVE", "UNI", "BNB", "NEAR", "LINK", "SOL", "XRP", "DOGE", "SUI", "ADA", "AVAX", "PENDLE", "TAO", "ENA", "PEPE"]
    focus_coins = [c for c in focus_coins if c in p.columns]

    btc_s = p["BTC"]
    last_dt = p.index[-1]
    log(f"Snapshot Date: {last_dt.strftime('%Y-%m-%d')} | BTC = ${btc_s.iloc[-1]:,.2f}")
    log("")
    header = (
        f"{'Coin':<6} | {'Price':>9} | {'365d Low':>9} {'fr 1yLow':>9} | "
        f"{'180dLo':>8} {'fr 180dLo':>9} | {'90d DD':>7} {'30dvsBTC':>9} {'180dvsBTC':>10} | "
        f"{'vs 50dMA':>8} {'vs 200dMA':>9} | {'BTC-Pair fr 1yLo':>16}"
    )
    log(header)
    log("-" * len(header))

    for c in focus_coins:
        s = p[c].dropna()
        if len(s) < 200:
            continue
        cur = s.iloc[-1]
        lo365 = s.iloc[-365:].min()
        lo365_dt = s.iloc[-365:].idxmin().strftime("%Y-%m-%d")
        up365 = (cur / lo365 - 1) * 100

        lo180 = s.iloc[-180:].min()
        up180 = (cur / lo180 - 1) * 100

        hi90 = s.iloc[-90:].max()
        dd90 = (cur / hi90 - 1) * 100

        rel_btc = (s / btc_s.reindex(s.index)).dropna()
        rel30 = (rel_btc.iloc[-1] / rel_btc.iloc[-31] - 1) * 100
        rel180 = (rel_btc.iloc[-1] / rel_btc.iloc[-181] - 1) * 100
        rel_lo365 = rel_btc.iloc[-365:].min()
        rel_up365 = (rel_btc.iloc[-1] / rel_lo365 - 1) * 100

        ma50 = s.iloc[-50:].mean()
        ma200 = s.iloc[-200:].mean()
        v50 = (cur / ma50 - 1) * 100
        v200 = (cur / ma200 - 1) * 100

        log(
            f"{c:<6} | {cur:>9.3f} | {lo365:>9.3f} {up365:>+8.1f}% | "
            f"{lo180:>8.3f} {up180:>+8.1f}% | {dd90:>+6.1f}% {rel30:>+8.1f}% {rel180:>+9.1f}% | "
            f"{v50:>+7.1f}% {v200:>+8.1f}% | {rel_up365:>+15.1f}% (lo {lo365_dt})"
        )

    # Detailed trajectory of AAVE, UNI, ZEC
    log("\n--- Detailed Cycle & 2025-2026 Low/High Anatomy for AAVE, UNI, ZEC ---")
    for c in ["AAVE", "UNI", "ZEC"]:
        s = p[c].loc["2024-01-01":].dropna()
        rb = (s / btc_s.reindex(s.index)).dropna()
        cur_u = s.iloc[-1]
        cur_b = rb.iloc[-1]

        lo_25_26 = s.loc["2025-01-01":].min()
        lo_25_26_dt = s.loc["2025-01-01":].idxmin().strftime("%Y-%m-%d")
        hi_25_26 = s.loc["2025-01-01":].max()
        hi_25_26_dt = s.loc["2025-01-01":].idxmax().strftime("%Y-%m-%d")

        lo_26 = s.loc["2026-01-01":].min()
        lo_26_dt = s.loc["2026-01-01":].idxmin().strftime("%Y-%m-%d")
        hi_26 = s.loc["2026-01-01":].max()
        hi_26_dt = s.loc["2026-01-01":].idxmax().strftime("%Y-%m-%d")

        rb_lo_26 = rb.loc["2026-01-01":].min()
        rb_lo_26_dt = rb.loc["2026-01-01":].idxmin().strftime("%Y-%m-%d")
        rb_hi_26 = rb.loc["2026-01-01":].max()
        rb_hi_26_dt = rb.loc["2026-01-01":].idxmax().strftime("%Y-%m-%d")

        log(
            f"  * {c}: Current=${cur_u:.2f} | "
            f"2025-26 USD Low=${lo_25_26:.2f} ({lo_25_26_dt}, cur is {(cur_u/lo_25_26-1)*100:+.1f}%), "
            f"2026 USD Low=${lo_26:.2f} ({lo_26_dt}, cur is {(cur_u/lo_26-1)*100:+.1f}%), "
            f"2026 USD High=${hi_26:.2f} ({hi_26_dt}, cur is {(cur_u/hi_26-1)*100:+.1f}%) | "
            f"vs BTC 2026 Low on {rb_lo_26_dt} (cur is {(cur_b/rb_lo_26-1)*100:+.1f}%), "
            f"vs BTC 2026 High on {rb_hi_26_dt} (cur is {(cur_b/rb_hi_26-1)*100:+.1f}%)"
        )

    # =========================================================================
    # PART 2: POINT-IN-TIME BACKTEST OF "THICK CUSHION / STRONG NARRATIVE" COINS
    # =========================================================================
    log("\n" + "=" * 95)
    log("PART 2: POINT-IN-TIME BACKTEST (2020-2026) — DO 'THICK CUSHION' COINS BEAT BTC OR LAGGARDS?")
    log("=" * 95)
    log("Methodology (Zero Lookahead, Zero Survivorship Bias):")
    log("  - Universe at date t: Top-30 liquid altcoins (ex BTC, ETH, stables) by trailing 30d USD volume shifted by 1 day.")
    log("  - Delisted coins: Included up to delisting date; forward price uses .ffill() across the full index")
    log("    (capturing the terminal crash into delisting).")

    p_ff = p.ffill()
    vol30 = q[alt_cols].rolling(30, min_periods=15).mean().shift(1)

    ma50_df = p[alt_cols].rolling(50, min_periods=40).mean()
    ma200_df = p[alt_cols].rolling(200, min_periods=150).mean()
    lo365_df = p[alt_cols].rolling(365, min_periods=200).min()
    lo180_df = p[alt_cols].rolling(180, min_periods=120).min()
    hi90_df = p[alt_cols].rolling(90, min_periods=60).max()

    # Relative to BTC
    rel_p = p[alt_cols].div(p["BTC"], axis=0)
    rel_lo365_df = rel_p.rolling(365, min_periods=200).min()
    rel_180_ret = rel_p / rel_p.shift(180) - 1.0
    rel_30_ret = rel_p / rel_p.shift(30) - 1.0

    usd_cushion_365 = p[alt_cols] / lo365_df - 1.0
    usd_cushion_180 = p[alt_cols] / lo180_df - 1.0
    btc_cushion_365 = rel_p / rel_lo365_df - 1.0
    dd90_df = p[alt_cols] / hi90_df - 1.0
    abv50_df = p[alt_cols] > ma50_df
    abv200_df = p[alt_cols] > ma200_df

    # Build daily cross-sectional groups inside Top-30 liquid alts (2020-01-01 to 2026-11-08)
    eval_dates = [d for d in p.index if d >= pd.Timestamp("2020-01-01")]

    records = []
    for d in eval_dates:
        v_row = vol30.loc[d].dropna()
        # Require valid 200d MA and 365d low
        valid_c = [c for c in v_row.index if pd.notna(ma200_df.loc[d, c]) and pd.notna(lo365_df.loc[d, c]) and pd.notna(p.loc[d, c])]
        if len(valid_c) < 20:
            continue
        top30 = v_row[valid_c].nlargest(30).index.tolist()

        # Market-wide alt state on day d (among Top-30)
        pct_abv50 = abv50_df.loc[d, top30].mean() * 100
        pct_abv200 = abv200_df.loc[d, top30].mean() * 100
        med_rel30 = rel_30_ret.loc[d, top30].median() * 100
        med_dd90 = dd90_df.loc[d, top30].median() * 100

        # Group 1: "Thick-Cushion Pullback" (like AAVE, UNI, ZEC today):
        #   - Up >= +40% from 365d USD low (AND up >= +20% vs BTC 365d low)
        #   - Pulled back in 90d USD terms (-15% to -45% from 90d high) AND below 50d MA
        g_thick_all = [
            c for c in top30
            if usd_cushion_365.loc[d, c] >= 0.40
            and btc_cushion_365.loc[d, c] >= 0.20
            and -0.45 <= dd90_df.loc[d, c] <= -0.15
            and (not abv50_df.loc[d, c])
        ]

        # Group 1A: "Thick-Cushion + Still Above 200d MA + Below 50d MA" (Exact UNI / ZEC / BNB / NEAR / LINK template)
        g_thick_abv200 = [
            c for c in top30
            if usd_cushion_365.loc[d, c] >= 0.35
            and abv200_df.loc[d, c]
            and (not abv50_df.loc[d, c])
            and dd90_df.loc[d, c] <= -0.15
        ]

        # Group 1B: "Top-5 Strongest 180d Relative-to-BTC Winners in Top-30" (Pure Narrative / Momentum Leaders)
        r180_valid = rel_180_ret.loc[d, top30].dropna()
        g_top5_mom = r180_valid.nlargest(5).index.tolist() if len(r180_valid) >= 15 else []

        # Group 2: "Thin-Cushion Beaten-Down Laggards" (Close to 365d low: < +20% above 365d USD low, below 200d MA & 50d MA)
        g_thin_laggards = [
            c for c in top30
            if usd_cushion_365.loc[d, c] < 0.20
            and (not abv200_df.loc[d, c])
            and (not abv50_df.loc[d, c])
        ]

        records.append({
            "date": d,
            "top30": top30,
            "pct_abv50": pct_abv50,
            "pct_abv200": pct_abv200,
            "med_rel30": med_rel30,
            "med_dd90": med_dd90,
            "g_thick_all": g_thick_all,
            "g_thick_abv200": g_thick_abv200,
            "g_top5_mom": g_top5_mom,
            "g_thin_laggards": g_thin_laggards,
        })

    rec_df = pd.DataFrame(records).set_index("date")

    # Inspect what coins are in each bucket TODAY (2026-11-08)
    today_row = rec_df.iloc[-1]
    log(f"\nToday's (2026-11-08) Top-30 Classification:")
    log(f"  * Top-30 State: pct_abv50={today_row['pct_abv50']:.1f}%, pct_abv200={today_row['pct_abv200']:.1f}%, med_rel30={today_row['med_rel30']:+.1f}%, med_dd90={today_row['med_dd90']:+.1f}%")
    log(f"  * Group 1 (Thick Cushion >=+40% fr 1y USD low & >=+20% fr 1y BTC low, <50dMA, 90d DD -15%..-45%): {today_row['g_thick_all']}")
    log(f"  * Group 1A (Thick Cushion >=+35% fr 1y low + >200dMA + <50dMA + 90d DD <=-15%): {today_row['g_thick_abv200']}")
    log(f"  * Group 1B (Top-5 180d vs BTC Momentum Leaders in Top-30): {today_row['g_top5_mom']}")
    log(f"  * Group 2 (Thin-Cushion Laggards <+20% fr 1y low & <200dMA): {today_row['g_thin_laggards']}")

    def eval_group_returns(event_dates, group_col, min_coins=2):
        horizons = [30, 60, 90, 180]
        res = {h: {"btc": [], "grp_ew_rel": [], "grp_med_rel": [], "grp_usd": [], "lag_ew_rel": [], "grp_vs_lag": []} for h in horizons}
        used_dates = []
        for d in event_dates:
            if d not in rec_df.index:
                continue
            grp = rec_df.loc[d, group_col]
            lag = rec_df.loc[d, "g_thin_laggards"]
            if len(grp) < min_coins:
                continue
            used_dates.append((d, grp))
            loc_i = p_ff.index.get_loc(d)
            for h in horizons:
                if loc_i + h >= len(p_ff.index):
                    continue
                d_fwd = p_ff.index[loc_i + h]
                b_f = p_ff.loc[d_fwd, "BTC"] / p_ff.loc[d, "BTC"] - 1.0
                c_f = p_ff.loc[d_fwd, grp] / p_ff.loc[d, grp] - 1.0
                btc_ret = b_f * 100.0
                c_rets = c_f * 100.0
                c_rels = ((1.0 + c_f) / (1.0 + b_f) - 1.0) * 100.0
                res[h]["btc"].append(btc_ret)
                res[h]["grp_usd"].append(c_rets.mean())
                res[h]["grp_ew_rel"].append(c_rels.mean())
                res[h]["grp_med_rel"].append(c_rels.median())
                if len(lag) >= 2:
                    l_f = p_ff.loc[d_fwd, lag] / p_ff.loc[d, lag] - 1.0
                    l_rels = ((1.0 + l_f) / (1.0 + b_f) - 1.0) * 100.0
                    res[h]["lag_ew_rel"].append(l_rels.mean())
                    res[h]["grp_vs_lag"].append(((1.0 + c_f.mean()) / (1.0 + l_f.mean()) - 1.0) * 100.0)
        return res, used_dates

    def print_eval_block(title, res, used_dates):
        log(f"\n>>> {title} (n={len(used_dates)} declustered events)")
        for h in [30, 60, 90, 180]:
            b = np.array(res[h]["btc"])
            ew = np.array(res[h]["grp_ew_rel"])
            md = np.array(res[h]["grp_med_rel"])
            vs_l = np.array(res[h]["grp_vs_lag"])
            if len(ew) == 0:
                continue
            lag_str = ""
            if len(vs_l) > 0:
                lag_str = f" | Beat Laggards: {(vs_l > 0).mean()*100:4.0f}% (med {np.median(vs_l):+5.1f}%)"
            log(
                f"  +{h:>3}d (n={len(ew):>2}): "
                f"BTC Win {(b>0).mean()*100:4.0f}% (med {np.median(b):+5.1f}%) | "
                f"EW Beat BTC: {(ew>0).mean()*100:4.0f}% (med {np.median(ew):+6.1f}%, mean {np.mean(ew):+6.1f}%) | "
                f"MedCoin Beat BTC: {(md>0).mean()*100:4.0f}% (med {np.median(md):+6.1f}%)"
                f"{lag_str}"
            )

    # Regime A: Current Weak Alt / Correction Regime (pct_abv50 <= 25% AND med_rel30 <= -5% AND pct_abv200 >= 15%)
    cond_weak_alt = rec_df[
        (rec_df["pct_abv50"] <= 25.0)
        & (rec_df["med_rel30"] <= -5.0)
        & (rec_df["pct_abv200"] >= 15.0)
    ].index.tolist()

    # Filter to dates where the group has >=2 coins, then decluster by 15 days
    cand_g1_weak = [d for d in cond_weak_alt if len(rec_df.loc[d, "g_thick_all"]) >= 2]
    ev_g1_weak = decluster(cand_g1_weak, 15)
    res_g1_weak, used_g1_weak = eval_group_returns(ev_g1_weak, "g_thick_all", min_coins=2)
    print_eval_block(
        "TEST 1A: In Weak Alt Regimes (like today: <=25% >50dMA, med_rel30<=-5%), buying 'Thick-Cushion Pullback' (>=+40% fr 1y USD low & >=+20% fr 1y BTC low, <50dMA)",
        res_g1_weak, used_g1_weak
    )

    cand_g1a_weak = [d for d in cond_weak_alt if len(rec_df.loc[d, "g_thick_abv200"]) >= 2]
    ev_g1a_weak = decluster(cand_g1a_weak, 15)
    res_g1a_weak, used_g1a_weak = eval_group_returns(ev_g1a_weak, "g_thick_abv200", min_coins=2)
    print_eval_block(
        "TEST 1B: In Weak Alt Regimes (like today), buying 'Above 200dMA + Below 50dMA + Cushion >=+35%' (Exact UNI/ZEC/BNB/NEAR setup)",
        res_g1a_weak, used_g1a_weak
    )

    cand_mom_weak = [d for d in cond_weak_alt if len(rec_df.loc[d, "g_top5_mom"]) >= 5]
    ev_mom_weak = decluster(cand_mom_weak, 15)
    res_mom_weak, used_mom_weak = eval_group_returns(ev_mom_weak, "g_top5_mom", min_coins=5)
    print_eval_block(
        "TEST 1C: In Weak Alt Regimes (like today), buying Top-5 180d Relative-Strength Leaders",
        res_mom_weak, used_mom_weak
    )

    cand_lag_weak = [d for d in cond_weak_alt if len(rec_df.loc[d, "g_thin_laggards"]) >= 2]
    ev_lag_weak = decluster(cand_lag_weak, 15)
    res_lag_weak, used_lag_weak = eval_group_returns(ev_lag_weak, "g_thin_laggards", min_coins=2)
    print_eval_block(
        "TEST 1D (Benchmark): In Weak Alt Regimes (like today), buying 'Thin-Cushion Beaten-Down Laggards' (<+20% fr 1y low & <200dMA)",
        res_lag_weak, used_lag_weak
    )

    # Regime B: ALL Market Pullbacks (any time >=3 Top-30 coins are in g_thick_abv200, regardless of macro alt bleed)
    cand_g1a_all = [d for d in rec_df.index if len(rec_df.loc[d, "g_thick_abv200"]) >= 3]
    ev_g1a_all = decluster(cand_g1a_all, 15)
    res_g1a_all, used_g1a_all = eval_group_returns(ev_g1a_all, "g_thick_abv200", min_coins=3)
    print_eval_block(
        "TEST 2A: Across ALL Market Conditions (2020-2026), buying 'Above 200dMA + Below 50dMA + Cushion >=+35%' (>=3 coins)",
        res_g1a_all, used_g1a_all
    )

    # Regime C: Bullish BTC Trend (BTC > 200dMA) vs Bearish BTC Trend (BTC < 200dMA) for Thick-Cushion Pullbacks
    btc_ma200 = p["BTC"].rolling(200).mean()
    cand_bull_btc = [d for d in cond_weak_alt if len(rec_df.loc[d, "g_thick_abv200"]) >= 2 and p.loc[d, "BTC"] > btc_ma200.loc[d]]
    ev_bull_btc = decluster(cand_bull_btc, 15)
    res_bull_btc, used_bull_btc = eval_group_returns(ev_bull_btc, "g_thick_abv200", min_coins=2)
    print_eval_block(
        "TEST 2B: When BTC > 200dMA (Bull Trend Pullback), buying 'Above 200dMA + Below 50dMA + Cushion >=+35%'",
        res_bull_btc, used_bull_btc
    )

    cand_bear_btc = [d for d in cond_weak_alt if len(rec_df.loc[d, "g_thick_abv200"]) >= 2 and p.loc[d, "BTC"] <= btc_ma200.loc[d]]
    ev_bear_btc = decluster(cand_bear_btc, 15)
    res_bear_btc, used_bear_btc = eval_group_returns(ev_bear_btc, "g_thick_abv200", min_coins=2)
    print_eval_block(
        "TEST 2C: When BTC <= 200dMA (Like TODAY: BTC below 200dMA), buying 'Above 200dMA + Below 50dMA + Cushion >=+35%'",
        res_bear_btc, used_bear_btc
    )

    # Print individual events for TEST 2C (BTC <= 200dMA & Weak Alt Regime & Thick-Cushion >200dMA coins)
    log("\n--- Event-by-Event Breakdown for TEST 2C (BTC <= 200dMA + Weak Alt Regime + Thick-Cushion >200dMA Coins) ---")
    for d, grp in used_bear_btc:
        loc_i = p_ff.index.get_loc(d)
        row_str = f"  {d.strftime('%Y-%m-%d')} | Coins={grp[:6]} (n={len(grp)})"
        for h in [30, 60, 90, 180]:
            if loc_i + h < len(p_ff.index):
                d_fwd = p_ff.index[loc_i + h]
                b_f = p_ff.loc[d_fwd, "BTC"] / p_ff.loc[d, "BTC"] - 1.0
                c_f = p_ff.loc[d_fwd, grp] / p_ff.loc[d, grp] - 1.0
                c_r = ((1.0 + c_f) / (1.0 + b_f) - 1.0) * 100.0
                row_str += f" | +{h}d vsBTC:{c_r.mean():+6.1f}%"
        log(row_str)

    # =========================================================================
    # PART 3: SINGLE-COIN HISTORICAL BACKTEST FOR AAVE, UNI, ZEC SPECIFICALLY
    # =========================================================================
    log("\n" + "=" * 95)
    log("PART 3: SINGLE-COIN HISTORICAL BACKTEST FOR AAVE, UNI, ZEC IN THEIR CURRENT SETUP (2019-2026)")
    log("=" * 95)
    log("Setup: Coin is >= +40% above its 365d USD low (thick cushion), has pulled back >= -15% from 90d high, and is below 50d MA.")

    for c in ["AAVE", "UNI", "ZEC"]:
        s = p[c].dropna()
        b = p["BTC"].reindex(s.index)
        c_ma50 = s.rolling(50).mean()
        c_ma200 = s.rolling(200).mean()
        c_lo365 = s.rolling(365, min_periods=200).min()
        c_hi90 = s.rolling(90, min_periods=60).max()

        cush = s / c_lo365 - 1.0
        dd90 = s / c_hi90 - 1.0

        # Match each coin's current state: thick cushion (>=40% above 1y low), pulled back (<=-15% from 90d high), <50d MA
        cond = (cush >= 0.40) & (dd90 <= -0.15) & (s < c_ma50)
        ev_dates = decluster(s.index[cond].tolist(), 15)

        log(f"\n--- {c}: Thick Cushion (>=+40% fr 1y low) + Pullback (<=-15% fr 90d high & <50dMA) | n={len(ev_dates)} events ---")
        for h in [30, 60, 90, 180]:
            rels = []
            usds = []
            rels_abv200 = []
            for d in ev_dates:
                loc_i = s.index.get_loc(d)
                if loc_i + h >= len(s.index):
                    continue
                d_fwd = s.index[loc_i + h]
                u_f = s.loc[d_fwd] / s.loc[d] - 1.0
                b_f = b.loc[d_fwd] / b.loc[d] - 1.0
                u_ret = u_f * 100.0
                rel_ret = ((1.0 + u_f) / (1.0 + b_f) - 1.0) * 100.0
                rels.append(rel_ret)
                usds.append(u_ret)
                if pd.notna(c_ma200.loc[d]) and s.loc[d] > c_ma200.loc[d]:
                    rels_abv200.append(rel_ret)
            rels = np.array(rels)
            usds = np.array(usds)
            rels_abv200 = np.array(rels_abv200)
            abv_str = ""
            if len(rels_abv200) > 0:
                abv_str = f" | Subset >200dMA (n={len(rels_abv200)}): Beat BTC {(rels_abv200>0).mean()*100:4.0f}% (med {np.median(rels_abv200):+6.1f}%)"
            log(
                f"  +{h:>3}d (n={len(rels):>2}): "
                f"USD Win {(usds>0).mean()*100:4.0f}% (med {np.median(usds):+6.1f}%) | "
                f"Beat BTC: {(rels>0).mean()*100:4.0f}% (med {np.median(rels):+6.1f}%, mean {np.mean(rels):+6.1f}%)"
                f"{abv_str}"
            )

        # Also test max drawdown in the next 90 days when buying this "thick cushion" setup!
        mdds_usd = []
        mdds_btc = []
        for d in ev_dates:
            loc_i = s.index.get_loc(d)
            if loc_i + 90 >= len(s.index):
                continue
            win_s = s.iloc[loc_i : loc_i + 91]
            win_b = (s.iloc[loc_i : loc_i + 91] / b.iloc[loc_i : loc_i + 91])
            mdds_usd.append((win_s.min() / win_s.iloc[0] - 1.0) * 100.0)
            mdds_btc.append((win_b.min() / win_b.iloc[0] - 1.0) * 100.0)
        mdds_usd = np.array(mdds_usd)
        mdds_btc = np.array(mdds_btc)
        log(
            f"  Risk check (Next 90d Max Adverse Excursion / Drawdown from entry): "
            f"USD med max-DD = {np.median(mdds_usd):+.1f}% (worst 25% <= {np.percentile(mdds_usd, 25):+.1f}%) | "
            f"vs BTC med max-DD = {np.median(mdds_btc):+.1f}% (worst 25% <= {np.percentile(mdds_btc, 25):+.1f}%)"
        )

        # Era breakdown (2019-2024 vs 2025-2026) to check if ZEC's win rate is purely from its 2025-2026 50x super-cycle
        for era_name, t_start, t_end in [("2019-2024", "2019-01-01", "2024-12-31"), ("2025-2026", "2025-01-01", "2026-11-08")]:
            era_dates = [d for d in ev_dates if pd.Timestamp(t_start) <= d <= pd.Timestamp(t_end)]
            rels_90 = []
            for d in era_dates:
                loc_i = s.index.get_loc(d)
                if loc_i + 90 < len(s.index):
                    d_fwd = s.index[loc_i + 90]
                    u_f = s.loc[d_fwd] / s.loc[d] - 1.0
                    b_f = b.loc[d_fwd] / b.loc[d] - 1.0
                    rels_90.append(((1.0 + u_f) / (1.0 + b_f) - 1.0) * 100.0)
            if len(rels_90) > 0:
                r90_arr = np.array(rels_90)
                log(f"  Era split [{era_name}] +90d (n={len(r90_arr)}): Beat BTC {(r90_arr>0).mean()*100:4.0f}% (med {np.median(r90_arr):+6.1f}%)")

    # =========================================================================
    # PART 4: WHEN *DOES* BUYING STRONG-NARRATIVE / THICK-CUSHION COINS WORK?
    # =========================================================================
    log("\n" + "=" * 95)
    log("PART 4: ENTRY TIMING COMPARISON FOR THICK-CUSHION COINS (>=+40% FR 1Y LOW & >200D MA)")
    log("=" * 95)

    # Calculate RSI14 for all alt_cols
    delta = p[alt_cols].diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    rsi14_df = 100 - (100 / (1 + gain / loss.replace(0, np.nan)))
    dist200_df = p[alt_cols] / ma200_df - 1.0

    # Today's exact RSI14 and distance to 200d MA for AAVE, UNI, ZEC, NEAR, BNB, LINK
    log("Current Distance to 200d MA & RSI14 (2026-11-08):")
    for c in ["ZEC", "UNI", "NEAR", "AAVE", "BNB", "LINK", "ENA"]:
        if c in rsi14_df.columns:
            log(
                f"  * {c:<5}: Price=${p.loc[last_dt, c]:.3f} | "
                f"200dMA=${ma200_df.loc[last_dt, c]:.3f} (dist {dist200_df.loc[last_dt, c]*100:+.1f}%) | "
                f"50dMA=${ma50_df.loc[last_dt, c]:.3f} (dist {(p.loc[last_dt, c]/ma50_df.loc[last_dt, c]-1)*100:+.1f}%) | "
                f"RSI14={rsi14_df.loc[last_dt, c]:.1f}"
            )

    # Pool all coin-day observations in Top-30 liquid universe (2020-2026) with >=15d declustering per coin
    def eval_pooled_coin_events(name, cond_fn):
        coin_events = []
        for d in eval_dates:
            if d not in rec_df.index:
                continue
            top30 = rec_df.loc[d, "top30"]
            for c in top30:
                if cond_fn(d, c):
                    coin_events.append((c, d))
        # Decluster per coin with 15-day gap
        by_coin = {}
        for c, d in coin_events:
            by_coin.setdefault(c, []).append(d)
        declustered = []
        for c, d_list in by_coin.items():
            for d in decluster(d_list, 15):
                declustered.append((c, d))

        log(f"\n>>> {name} (Pooled Top-30 coin-events, declustered 15d per coin: n={len(declustered)})")
        for h in [30, 60, 90, 180]:
            rels = []
            usds = []
            for c, d in declustered:
                loc_i = p_ff.index.get_loc(d)
                if loc_i + h >= len(p_ff.index):
                    continue
                d_fwd = p_ff.index[loc_i + h]
                u_f = p_ff.loc[d_fwd, c] / p_ff.loc[d, c] - 1.0
                b_f = p_ff.loc[d_fwd, "BTC"] / p_ff.loc[d, "BTC"] - 1.0
                usds.append(u_f * 100.0)
                rels.append(((1.0 + u_f) / (1.0 + b_f) - 1.0) * 100.0)
            rels = np.array(rels)
            usds = np.array(usds)
            log(
                f"  +{h:>3}d (n={len(rels):>3}): "
                f"USD Win {(usds>0).mean()*100:4.0f}% (med {np.median(usds):+6.1f}%) | "
                f"Beat BTC: {(rels>0).mean()*100:4.0f}% (med {np.median(rels):+6.1f}%, mean {np.mean(rels):+6.1f}%)"
            )

    # Timing A: High in the air (>+30% above 200d MA) but broke below 50d MA (like ZEC +56.5%, UNI +39.5%, NEAR +36.9% today)
    eval_pooled_coin_events(
        "TIMING A (Mid-Air Breakdown — Like ZEC/UNI/NEAR today): Cushion >=+40%, <50dMA, but still >+30% above 200dMA",
        lambda d, c: (
            usd_cushion_365.loc[d, c] >= 0.40
            and (not abv50_df.loc[d, c])
            and dist200_df.loc[d, c] > 0.30
            and dd90_df.loc[d, c] <= -0.15
        )
    )

    # Timing B: Pulling back close to 200d MA (0% to +15% above 200d MA) AND oversold RSI14 <= 40 (Closer to AAVE/LINK/BNB zone)
    eval_pooled_coin_events(
        "TIMING B (200dMA Support Test + Oversold): Cushion >=+40%, <50dMA, 0%..+15% above 200dMA, RSI14 <= 40",
        lambda d, c: (
            usd_cushion_365.loc[d, c] >= 0.40
            and (not abv50_df.loc[d, c])
            and 0.0 <= dist200_df.loc[d, c] <= 0.15
            and pd.notna(rsi14_df.loc[d, c])
            and rsi14_df.loc[d, c] <= 40.0
        )
    )

    # Timing C: Right-side 50dMA Reclaim (Coin has Cushion >=+40%, >200dMA, was <50dMA yesterday and crosses >50dMA today while beating BTC over 7d)
    abv50_prev = abv50_df.shift(1)
    rel7_df = rel_p / rel_p.shift(7) - 1.0
    eval_pooled_coin_events(
        "TIMING C (Right-Side 50dMA Reclaim): Cushion >=+40%, >200dMA, crosses back ABOVE 50dMA + 7d vs BTC > 0",
        lambda d, c: (
            usd_cushion_365.loc[d, c] >= 0.40
            and abv200_df.loc[d, c]
            and abv50_df.loc[d, c]
            and (abv50_prev.loc[d, c] == False)
            and pd.notna(rel7_df.loc[d, c])
            and rel7_df.loc[d, c] > 0.0
        )
    )

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"\nSaved full output to {OUT_PATH}")


if __name__ == "__main__":
    main()
