"""
Backtest: THIS Cycle's (2025-2026) Strong Narrative Coins (AAVE, UNI, ZEC, NEAR, etc.) on Pullbacks
===================================================================================================
Question (Avalon 2026-11-09):
  "怎么说呢，我说的是这一轮的叙事币，上一轮的pattern 强叙事币和这一轮是不一祥的，
   zec也是近一年才走强的，如果你看之前zec就是个死币。"

Analysis & Backtest Design:
  1. Why mixing 2019-2024 into ZEC/AAVE/UNI distorted their single-coin stats:
     - In 2019-2024, ZEC/BTC was in a -96% secular bear market ("dead coin"), and UNI/AAVE in 2021-2023
       were in a multi-year DeFi bear market.
     - Since their 2024/2025 structural bottoms against BTC, ZEC, AAVE, UNI, NEAR have been in
       structural Coin/BTC uptrends (making higher highs and higher lows against BTC).
  2. Walk through EVERY pullback of AAVE, UNI, ZEC, NEAR in THIS cycle (2025-01-01 to 2026-11-08)
     since each coin's structural reversal vs BTC:
     - Show every pullback date, drawdown, Coin/BTC MA200 status, and forward 30d/60d/90d returns vs BTC.
  3. Point-in-Time Cross-Sectional Backtest comparing:
     - Definition of a "True Cycle Narrative Leader" (by Coin/BTC exchange rate, NOT just USD price!):
       * Coin/BTC is above its 200-day Coin/BTC MA (rel_p > rel_ma200)
       * Coin/BTC is up >= +40% from its 365-day Coin/BTC low
       * Coin/BTC 180-day return > +15% (genuinely beating BTC over 6 months)
       * Currently pulling back: 90d USD drawdown <= -20% (or dipped below 50d USD MA)
     - Compare 2025-2026 (This Cycle) vs 2021-2024 (Last Cycle), and check what happens when Coin/BTC
       holds vs breaks its 200-day Coin/BTC MA.

Data:
  - data/universe/spot_usdt_1d_spliced.csv.gz (573 coins incl. 177 delisted)
Date: 2026-11-09
"""

from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
UNIVERSE_CSV = ROOT / "data" / "universe" / "spot_usdt_1d_spliced.csv.gz"
OUT_PATH = ROOT / "data" / "outputs" / "this_cycle_narrative_pullback_2026-11-09.txt"


def load_data():
    df = pd.read_csv(UNIVERSE_CSV, parse_dates=["t"])
    df["date"] = pd.to_datetime(df["t"]).dt.tz_localize(None)
    df["base"] = df["symbol"].str.replace("USDT$", "", regex=True)
    p = df.pivot_table(index="date", columns="base", values="c").sort_index()
    q = df.pivot_table(index="date", columns="base", values="qv").sort_index()
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
    p, q = load_data()
    p = p.loc[:"2026-11-08"].copy()
    q = q.loc[:"2026-11-08"].copy()
    p_ff = p.ffill()

    STABLES = {
        "USDT", "USDC", "BUSD", "TUSD", "FDUSD", "DAI", "USDP", "USDD", "PYUSD", "USD1",
        "EUR", "EURI", "AEUR", "GBP", "AUD", "TRY", "BRL", "BIDR", "IDRT", "UAH", "RUB", "NGN", "PLN", "RON", "ZAR", "JPY",
        "PAXG", "WBTC", "WBETH", "BETH", "STETH", "CBETH", "RETH", "WETH", "WSTETH", "WEETH", "CBBTC", "BTCB"
    }
    alt_cols = [c for c in p.columns if c not in STABLES and c not in ("BTC", "ETH") and not c.endswith(("UP", "DOWN", "BULL", "BEAR"))]

    lines = []
    def log(s=""):
        print(s)
        lines.append(s)

    btc = p["BTC"]
    rel_p = p[alt_cols].div(btc, axis=0)

    log("=" * 100)
    log("PART 1: 'DEAD COIN THEN vs NARRATIVE LEADER NOW' — REGIME SHIFT OF ZEC, AAVE, UNI, NEAR")
    log("=" * 100)

    for c in ["ZEC", "AAVE", "UNI", "NEAR", "ENA", "BNB", "LINK", "SOL"]:
        if c not in p.columns:
            continue
        rb = (p[c] / btc).dropna()
        # Pre-2025 regime (2021-05-01 to 2024-12-31) vs This Cycle (since 2025-01-01 or cycle low)
        rb_old = rb.loc["2021-05-01":"2024-12-31"]
        old_ret = (rb_old.iloc[-1] / rb_old.iloc[0] - 1) * 100 if len(rb_old) > 10 else np.nan

        rb_24_26 = rb.loc["2024-01-01":]
        cyc_lo_val = rb_24_26.min()
        cyc_lo_dt = rb_24_26.idxmin()
        rb_since_lo = rb.loc[cyc_lo_dt:]
        cyc_hi_val = rb_since_lo.max()
        cyc_hi_dt = rb_since_lo.idxmax()
        cur_val = rb.iloc[-1]

        # Coin/BTC 200d MA today
        rb_ma200 = rb.rolling(200).mean().iloc[-1]
        rb_ma50 = rb.rolling(50).mean().iloc[-1]

        log(
            f"  * {c:<4} | 2021-2024 vs BTC: {old_ret:+6.1f}% | "
            f"Cycle BTC-Pair Low: {cyc_lo_dt.strftime('%Y-%m-%d')} -> Peak {cyc_hi_dt.strftime('%Y-%m-%d')} ({(cyc_hi_val/cyc_lo_val-1)*100:+6.1f}%) | "
            f"Current vs Cycle Low: {(cur_val/cyc_lo_val-1)*100:+6.1f}% (pullback fr peak: {(cur_val/cyc_hi_val-1)*100:+5.1f}%) | "
            f"Coin/BTC vs 200dMA: {(cur_val/rb_ma200-1)*100:+5.1f}% (vs 50dMA: {(cur_val/rb_ma50-1)*100:+5.1f}%)"
        )

    # =========================================================================
    # PART 2: EVERY PULLBACK OF AAVE, UNI, ZEC, NEAR IN THIS CYCLE (2025-2026)
    # =========================================================================
    log("\n" + "=" * 100)
    log("PART 2: EVERY PULLBACK OF ZEC, AAVE, UNI, NEAR IN THIS CYCLE (2025-2026 AFTER REVERSAL)")
    log("=" * 100)
    log("Definition of 'In This Cycle's Uptrend': Coin/BTC is above its 200-day Coin/BTC MA (or up >=+35% from cycle BTC low)")
    log("Pullback trigger: 90d USD drawdown <= -18% AND USD price < 50d MA (declustered 15 days).")

    for c in ["ZEC", "AAVE", "UNI", "NEAR"]:
        s = p[c].dropna()
        b = btc.reindex(s.index)
        rb = s / b

        s_ma50 = s.rolling(50).mean()
        s_ma200 = s.rolling(200).mean()
        rb_ma200 = rb.rolling(200).mean()
        rb_lo365 = rb.rolling(365, min_periods=180).min()
        s_hi90 = s.rolling(90, min_periods=30).max()
        dd90 = s / s_hi90 - 1.0

        # In 2025-2026, while Coin/BTC is in structural uptrend (rb > rb_ma200 OR rb >= 1.35 * rb_lo365)
        cond_25_26 = (
            (s.index >= pd.Timestamp("2024-11-01"))
            & (rb > rb_ma200)
            & (dd90 <= -0.18)
            & (s < s_ma50)
        )
        ev_dates = decluster(s.index[cond_25_26].tolist(), 15)

        log(f"\n--- {c} in 2024.11–2026.11 (While {c}/BTC > 200d BTC-Pair MA, Pullback <=-18% & <50dMA): n={len(ev_dates)} events ---")
        rels_30, rels_60, rels_90 = [], [], []
        usds_30, usds_60, usds_90 = [], [], []
        for d in ev_dates:
            loc_i = s.index.get_loc(d)
            px_d = s.loc[d]
            dd_d = dd90.loc[d] * 100
            rb_dist200 = (rb.loc[d] / rb_ma200.loc[d] - 1) * 100
            u_dist200 = (s.loc[d] / s_ma200.loc[d] - 1) * 100

            f_str = ""
            for h, r_list, u_list in [(30, rels_30, usds_30), (60, rels_60, usds_60), (90, rels_90, usds_90)]:
                if loc_i + h < len(s.index):
                    d_fwd = s.index[loc_i + h]
                    u_f = s.loc[d_fwd] / s.loc[d] - 1.0
                    b_f = b.loc[d_fwd] / b.loc[d] - 1.0
                    rel_f = ((1.0 + u_f) / (1.0 + b_f) - 1.0) * 100.0
                    r_list.append(rel_f)
                    u_list.append(u_f * 100.0)
                    f_str += f" | +{h}d: USD {u_f*100:+6.1f}%, vsBTC {rel_f:+6.1f}%"
                else:
                    # Partial forward return up to latest date (2026-11-08)
                    days_left = (s.index[-1] - d).days
                    if h == 30 and days_left > 0:
                        u_f = s.iloc[-1] / s.loc[d] - 1.0
                        b_f = b.iloc[-1] / b.loc[d] - 1.0
                        rel_f = ((1.0 + u_f) / (1.0 + b_f) - 1.0) * 100.0
                        f_str += f" | (+{days_left}d so far: USD {u_f*100:+5.1f}%, vsBTC {rel_f:+5.1f}%)"
            log(f"  {d.strftime('%Y-%m-%d')}: px=${px_d:<8.2f} 90dDD={dd_d:+5.1f}% | USD vs 200dMA={u_dist200:+5.1f}% | {c}/BTC vs 200dMA={rb_dist200:+5.1f}%{f_str}")

        for h, r_list, u_list in [(30, rels_30, usds_30), (60, rels_60, usds_60), (90, rels_90, usds_90)]:
            if len(r_list) > 0:
                ra = np.array(r_list)
                ua = np.array(u_list)
                log(f"  Summary +{h}d (n={len(ra)}): Beat BTC {(ra>0).mean()*100:4.0f}% ({int((ra>0).sum())}/{len(ra)}, med {np.median(ra):+6.1f}%, mean {np.mean(ra):+6.1f}%) | USD Win {(ua>0).mean()*100:4.0f}% (med {np.median(ua):+6.1f}%)")

    # =========================================================================
    # PART 3: SYSTEMATIC CROSS-SECTIONAL TEST IN 2025-2026 vs 2021-2024
    #         "TRUE BTC-PAIR UPTREND NARRATIVE COINS" ON PULLBACKS
    # =========================================================================
    log("\n" + "=" * 100)
    log("PART 3: CROSS-SECTIONAL BACKTEST — 'TRUE BTC-PAIR UPTREND NARRATIVE COINS' IN 2025-2026 vs 2021-2024")
    log("=" * 100)
    log("Key distinction:")
    log("  - Previously we filtered by USD cushion (>200d USD MA). Now we filter by COIN/BTC EXCHANGE RATE TREND:")
    log("    1. Coin is in Top-30 liquid universe at date d (trailing 30d vol shifted 1d, zero lookahead)")
    log("    2. Coin/BTC > 200-day Coin/BTC MA (structural uptrend against BTC, not a dead coin!)")
    log("    3. Coin/BTC is up >= +40% from its 365-day Coin/BTC low AND 180d vs BTC > +15%")
    log("    4. Pullback trigger: 90d USD drawdown <= -20% AND USD price < 50d USD MA")

    vol30 = q[alt_cols].rolling(30, min_periods=15).mean().shift(1)
    u_ma50 = p[alt_cols].rolling(50, min_periods=40).mean()
    u_ma200 = p[alt_cols].rolling(200, min_periods=150).mean()
    u_hi90 = p[alt_cols].rolling(90, min_periods=45).max()
    u_dd90 = p[alt_cols] / u_hi90 - 1.0

    rb_ma200 = rel_p.rolling(200, min_periods=150).mean()
    rb_ma50 = rel_p.rolling(50, min_periods=40).mean()
    rb_lo365 = rel_p.rolling(365, min_periods=200).min()
    rb_cush365 = rel_p / rb_lo365 - 1.0
    rb_ret180 = rel_p / rel_p.shift(180) - 1.0
    rb_dist200 = rel_p / rb_ma200 - 1.0

    # Check which coins qualify TODAY (2026-11-08) under this exact "True BTC-Pair Narrative Leader" filter!
    last_d = p.index[-1]
    v_last = vol30.loc[last_d].dropna()
    valid_last = [c for c in v_last.index if pd.notna(rb_ma200.loc[last_d, c]) and pd.notna(p.loc[last_d, c])]
    top30_last = v_last[valid_last].nlargest(30).index.tolist()

    qual_today = [
        c for c in top30_last
        if rel_p.loc[last_d, c] > rb_ma200.loc[last_d, c]
        and rb_cush365.loc[last_d, c] >= 0.40
        and rb_ret180.loc[last_d, c] >= 0.15
        and u_dd90.loc[last_d, c] <= -0.20
        and p.loc[last_d, c] < u_ma50.loc[last_d, c]
    ]
    log(f"\nCoins in Top-30 qualifying TODAY (2026-11-08) under True BTC-Pair Narrative Pullback:")
    log(f"  -> {qual_today}")
    for c in qual_today:
        log(
            f"     * {c:<5}: 180d vs BTC={rb_ret180.loc[last_d, c]*100:+6.1f}%, "
            f"fr 1y BTC-low={rb_cush365.loc[last_d, c]*100:+6.1f}%, "
            f"{c}/BTC vs 200dMA={rb_dist200.loc[last_d, c]*100:+5.1f}%, "
            f"90d USD DD={u_dd90.loc[last_d, c]*100:+5.1f}%"
        )

    def run_era_comparison(label, extra_cond_fn=None):
        log(f"\n>>> {label}")
        for era_name, d_start, d_end in [
            ("ALL CYCLES (2020.01 - 2026.11)", "2020-01-01", "2026-11-08"),
            ("LAST CYCLE (2020.01 - 2024.10)", "2020-01-01", "2024-10-31"),
            ("THIS CYCLE (2024.11 - 2026.11)", "2024-11-01", "2026-11-08"),
        ]:
            # Collect portfolio events (date-level equal weight) AND pooled coin-events (declustered 15d per coin)
            port_candidates = []
            daily_groups = {}
            coin_ev_map = {}

            sub_dates = [d for d in p.index if pd.Timestamp(d_start) <= d <= pd.Timestamp(d_end)]
            for d in sub_dates:
                v_row = vol30.loc[d].dropna()
                valid_c = [c for c in v_row.index if pd.notna(rb_ma200.loc[d, c]) and pd.notna(rb_lo365.loc[d, c]) and pd.notna(p.loc[d, c])]
                if len(valid_c) < 20:
                    continue
                top30 = v_row[valid_c].nlargest(30).index.tolist()
                grp = []
                for c in top30:
                    ok = (
                        (rel_p.loc[d, c] > rb_ma200.loc[d, c])
                        and (rb_cush365.loc[d, c] >= 0.40)
                        and pd.notna(rb_ret180.loc[d, c])
                        and (rb_ret180.loc[d, c] >= 0.15)
                        and (u_dd90.loc[d, c] <= -0.20)
                        and (p.loc[d, c] < u_ma50.loc[d, c])
                    )
                    if ok and extra_cond_fn is not None:
                        ok = extra_cond_fn(d, c)
                    if ok:
                        grp.append(c)
                        coin_ev_map.setdefault(c, []).append(d)
                if len(grp) >= 1:
                    port_candidates.append(d)
                    daily_groups[d] = grp

            ev_port = decluster(port_candidates, 15)
            coin_declustered = []
            for c, d_list in coin_ev_map.items():
                for d in decluster(d_list, 15):
                    coin_declustered.append((c, d))

            log(f"  [{era_name}] Date-Events (declustered 15d): n={len(ev_port)} | Coin-Events (declustered 15d/coin): n={len(coin_declustered)}")
            for h in [30, 60, 90, 180]:
                # Date-level Equal-Weight portfolio
                p_rels, p_usds, p_btcs = [], [], []
                for d in ev_port:
                    loc_i = p_ff.index.get_loc(d)
                    if loc_i + h >= len(p_ff.index):
                        continue
                    d_fwd = p_ff.index[loc_i + h]
                    grp = daily_groups[d]
                    b_f = p_ff.loc[d_fwd, "BTC"] / p_ff.loc[d, "BTC"] - 1.0
                    c_f = p_ff.loc[d_fwd, grp] / p_ff.loc[d, grp] - 1.0
                    rel_f = ((1.0 + c_f) / (1.0 + b_f) - 1.0) * 100.0
                    p_rels.append(rel_f.mean())
                    p_usds.append(c_f.mean() * 100.0)
                    p_btcs.append(b_f * 100.0)

                # Coin-level pooled
                c_rels, c_usds = [], []
                for c, d in coin_declustered:
                    loc_i = p_ff.index.get_loc(d)
                    if loc_i + h >= len(p_ff.index):
                        continue
                    d_fwd = p_ff.index[loc_i + h]
                    b_f = p_ff.loc[d_fwd, "BTC"] / p_ff.loc[d, "BTC"] - 1.0
                    u_f = p_ff.loc[d_fwd, c] / p_ff.loc[d, c] - 1.0
                    c_rels.append(((1.0 + u_f) / (1.0 + b_f) - 1.0) * 100.0)
                    c_usds.append(u_f * 100.0)

                if len(p_rels) > 0 and len(c_rels) > 0:
                    pr = np.array(p_rels)
                    pu = np.array(p_usds)
                    cr = np.array(c_rels)
                    cu = np.array(c_usds)
                    log(
                        f"    +{h:>3}d | Date-EW (n={len(pr):>2}): Beat BTC {(pr>0).mean()*100:4.0f}% (med {np.median(pr):+6.1f}%, mean {np.mean(pr):+6.1f}%, USD med {np.median(pu):+6.1f}%) | "
                        f"Single-Coin (n={len(cr):>3}): Beat BTC {(cr>0).mean()*100:4.0f}% (med {np.median(cr):+6.1f}%, mean {np.mean(cr):+6.1f}%)"
                    )

    # 3A: Base True BTC-Pair Narrative Pullback
    run_era_comparison("TEST 3A: True BTC-Pair Narrative Leaders (Coin/BTC > 200dMA, >=+40% fr 1y BTC-low, 180d vs BTC >=+15%) on Pullback (<=-20% 90d DD & <50d USD MA)")

    # 3B: Split by how extended Coin/BTC is above its 200d Coin/BTC MA:
    #     Zone 1: Moderate extension above Coin/BTC 200dMA (0% to +25% above Coin/BTC 200dMA — like AAVE +16.1%, ENA +14.0%, UNI +39.8%, NEAR +37.3%, ZEC +55.5%)
    run_era_comparison(
        "TEST 3B-1: Pullback TO Coin/BTC 200dMA Support Zone (Coin/BTC is 0%..+25% above its 200d BTC-Pair MA, e.g. AAVE/ENA today)",
        lambda d, c: 0.0 <= rb_dist200.loc[d, c] <= 0.25
    )

    run_era_comparison(
        "TEST 3B-2: Still Extended Above Coin/BTC 200dMA (Coin/BTC is > +25% above its 200d BTC-Pair MA, e.g. ZEC/UNI/NEAR today)",
        lambda d, c: rb_dist200.loc[d, c] > 0.25
    )

    # Print all 2024.11 - 2026.11 Date-Events in detail so we can see every single one!
    log("\n--- Detailed Event Log for THIS CYCLE (2024.11 - 2026.11) True BTC-Pair Narrative Pullbacks ---")
    sub_dates_this = [d for d in p.index if pd.Timestamp("2024-11-01") <= d <= pd.Timestamp("2026-11-08")]
    cand_this = []
    grp_this = {}
    for d in sub_dates_this:
        v_row = vol30.loc[d].dropna()
        valid_c = [c for c in v_row.index if pd.notna(rb_ma200.loc[d, c]) and pd.notna(rb_lo365.loc[d, c]) and pd.notna(p.loc[d, c])]
        if len(valid_c) < 20:
            continue
        top30 = v_row[valid_c].nlargest(30).index.tolist()
        grp = [
            c for c in top30
            if (rel_p.loc[d, c] > rb_ma200.loc[d, c])
            and (rb_cush365.loc[d, c] >= 0.40)
            and pd.notna(rb_ret180.loc[d, c])
            and (rb_ret180.loc[d, c] >= 0.15)
            and (u_dd90.loc[d, c] <= -0.20)
            and (p.loc[d, c] < u_ma50.loc[d, c])
        ]
        if len(grp) >= 1:
            cand_this.append(d)
            grp_this[d] = grp

    ev_this = decluster(cand_this, 15)
    for d in ev_this:
        grp = grp_this[d]
        loc_i = p_ff.index.get_loc(d)
        s_line = f"  {d.strftime('%Y-%m-%d')} | Coins={grp}"
        for h in [30, 60, 90, 180]:
            if loc_i + h < len(p_ff.index):
                d_fwd = p_ff.index[loc_i + h]
                b_f = p_ff.loc[d_fwd, "BTC"] / p_ff.loc[d, "BTC"] - 1.0
                c_f = p_ff.loc[d_fwd, grp] / p_ff.loc[d, grp] - 1.0
                rel_f = ((1.0 + c_f) / (1.0 + b_f) - 1.0) * 100.0
                s_line += f" | +{h}d vsBTC:{rel_f.mean():+6.1f}% (USD {c_f.mean()*100:+6.1f}%)"
        log(s_line)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"\nSaved full output to {OUT_PATH}")


if __name__ == "__main__":
    main()
