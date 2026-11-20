"""
Backtest: In Past Cycles, When BTC Had a Major Pullback, What Happened When You Bought
          *THAT* Cycle's Strong Narrative Leaders (Thick Cushion Coins) vs BTC and vs Laggards?
===============================================================================================
Question (Avalon 2026-11-09):
  "我的意思是如果你跑回测，你应该看之前比特币牛初回调时买入这些强叙事币之后的结局。
   和现在比，而不是拿现在的强势币去回测上一轮他们的历史，因为上一轮他们不一定是强势币。"

Methodology (Zero Lookahead, Point-in-Time Selection of THAT Cycle's Narrative Leaders):
  1. Identify every major BTC Pullback Episode across 2019–2026:
     - BTC is in a post-bottom recovery / bull cycle (BTC is >= +35% above its 365d low)
     - BTC has pulled back >= -15% from its trailing 90d high (like today: BTC down ~-24% from 90d high,
       still +10%..+50% above cycle low).
  2. At each historical BTC pullback date t, dynamically select *THAT* moment's Strong Narrative Coins
     (without using any future information):
     - Universe at date t: Top-30 (and Top-50) liquid altcoins (ex BTC, ETH, stables) by trailing 30d volume (shifted 1d).
     - Definition A ("That Cycle's Thick-Cushion Narrative Leaders"):
       * Coins in Top-30 that have outperformed BTC over the past 180d (>= +15% vs BTC) AND are up >= +80%
         from their 365d USD low (thick cushion from bottom) AND are still above their 200d MA (or Coin/BTC > 200d MA),
         while pulling back >= -20% from their 90d high.
     - Definition B ("Top-5 Strongest 180d Narrative Leaders on Pullback"):
       * Simply take the Top-5 strongest 180d relative-strength winners vs BTC in the Top-30 liquid universe
         that have pulled back >= -15% from their 90d high.
  3. Show every single historical episode (2019 Q3, 2020 Q3 DeFi pullback, 2021 May-Jul pullback,
     2021 Dec pullback, 2023 Aug-Sep early-bull pullback, 2024 Apr-Jul pullback, 2025 Feb-Apr pullback,
     2026 Jun pullback, and Today 2026 Nov), listing the EXACT coins that were that cycle's narrative leaders
     at that moment, and their forward +30d, +60d, +90d, +180d, +365d returns vs BTC and vs Laggards!

Data:
  - data/universe/spot_usdt_1d_spliced.csv.gz (573 coins incl. 177 delisted)
  - data/backfill/cmc_weekly_top300.csv.gz (for 2019 early check)
Date: 2026-11-09
"""

from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
UNIVERSE_CSV = ROOT / "data" / "universe" / "spot_usdt_1d_spliced.csv.gz"
OUT_PATH = ROOT / "data" / "outputs" / "past_cycles_narrative_leaders_on_btc_pullback_2026-11-09.txt"


def load_data():
    df = pd.read_csv(UNIVERSE_CSV, parse_dates=["t"])
    df["date"] = pd.to_datetime(df["t"]).dt.tz_localize(None)
    df["base"] = df["symbol"].str.replace("USDT$", "", regex=True)
    p = df.pivot_table(index="date", columns="base", values="c").sort_index()
    q = df.pivot_table(index="date", columns="base", values="qv").sort_index()
    return p, q


def decluster(dates, min_gap_days=30):
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
        "PAXG", "WBTC", "WBETH", "BETH", "STETH", "CBETH", "RETH", "WETH", "WSTETH", "WEETH", "CBBTC", "BTCB",
        "UST", "USTC", "USDE", "SUSDE", "FDUSD", "TUSD"
    }
    alt_cols = [c for c in p.columns if c not in STABLES and c not in ("BTC", "ETH") and not c.endswith(("UP", "DOWN", "BULL", "BEAR"))]

    lines = []
    def log(s=""):
        print(s)
        lines.append(s)

    btc = p["BTC"]
    btc_lo365 = btc.rolling(365, min_periods=180).min()
    btc_hi90 = btc.rolling(90, min_periods=30).max()
    btc_cush365 = btc / btc_lo365 - 1.0
    btc_dd90 = btc / btc_hi90 - 1.0
    btc_ma200 = btc.rolling(200, min_periods=150).mean()

    vol30 = q[alt_cols].rolling(30, min_periods=15).mean().shift(1)
    rel_p = p[alt_cols].div(btc, axis=0)

    u_ma50 = p[alt_cols].rolling(50, min_periods=40).mean()
    u_ma200 = p[alt_cols].rolling(200, min_periods=150).mean()
    u_lo365 = p[alt_cols].rolling(365, min_periods=180).min()
    u_hi90 = p[alt_cols].rolling(90, min_periods=30).max()
    u_cush365 = p[alt_cols] / u_lo365 - 1.0
    u_dd90 = p[alt_cols] / u_hi90 - 1.0

    rb_ma200 = rel_p.rolling(200, min_periods=150).mean()
    rb_lo365 = rel_p.rolling(365, min_periods=180).min()
    rb_cush365 = rel_p / rb_lo365 - 1.0
    rb_ret180 = rel_p / rel_p.shift(180) - 1.0
    rb_ret90 = rel_p / rel_p.shift(90) - 1.0

    log("=" * 105)
    log("PART 1: EVERY HISTORICAL 'BTC PULLBACK' EPISODE (2019–2026) & THAT EPISODE'S NARRATIVE LEADERS")
    log("=" * 105)
    log("How each episode & its contemporary 'Strong Narrative Coins' are defined (100% Point-in-Time, Zero Lookahead):")
    log("  1. Universe at date d: Top-30 liquid altcoins by trailing 30d volume (shifted 1 day).")
    log("  2. 'That Moment's Thick-Cushion Narrative Leaders' (Group L):")
    log("     - Up >= +60% from 365d USD low (thick cushion above cycle bottom)")
    log("     - Outperformed BTC over trailing 180d (180d vs BTC > +10%) AND Coin/BTC > 200d Coin/BTC MA (or USD > 200d MA)")
    log("     - Pulled back >= -18% from 90d USD high (dipping with BTC)")
    log("  3. Also tracked: 'Top-5 180d Relative-Strength Leaders on Pullback' (Group Top5) and 'Beaten-Down Laggards' (Group Weak: <+25% fr 1y low & <200dMA).")

    # Let's inspect BTC's current state on 2026-11-08:
    d_now = p.index[-1]
    log(f"\nToday ({d_now.strftime('%Y-%m-%d')}): BTC=${btc.loc[d_now]:,.2f} | fr 365d low={btc_cush365.loc[d_now]*100:+.1f}% | 90d DD={btc_dd90.loc[d_now]*100:+.1f}% | vs 200dMA={(btc.loc[d_now]/btc_ma200.loc[d_now]-1)*100:+.1f}%")

    # Build daily groups from 2019-06-01 to 2026-11-08
    eval_dates = [d for d in p.index if d >= pd.Timestamp("2019-06-01")]
    daily_info = {}

    for d in eval_dates:
        v_row = vol30.loc[d].dropna()
        valid_c = [c for c in v_row.index if pd.notna(u_ma200.loc[d, c]) and pd.notna(u_lo365.loc[d, c]) and pd.notna(p.loc[d, c]) and pd.notna(rb_ret180.loc[d, c])]
        if len(valid_c) < 15:
            continue
        top30 = v_row[valid_c].nlargest(30).index.tolist()

        # Group L: That moment's Thick-Cushion Narrative Leaders on Pullback
        grp_L = [
            c for c in top30
            if u_cush365.loc[d, c] >= 0.60
            and rb_ret180.loc[d, c] >= 0.10
            and ((p.loc[d, c] > u_ma200.loc[d, c]) or (rel_p.loc[d, c] > rb_ma200.loc[d, c]))
            and u_dd90.loc[d, c] <= -0.18
        ]

        # Group Top5: Top-5 strongest 180d vs BTC winners in Top-30 that are in a pullback (90d DD <= -18% & beat BTC over 180d)
        pullback_winners = [c for c in top30 if rb_ret180.loc[d, c] > 0.05 and u_dd90.loc[d, c] <= -0.18 and u_cush365.loc[d, c] >= 0.40]
        grp_top5 = sorted(pullback_winners, key=lambda c: rb_ret180.loc[d, c], reverse=True)[:5]

        # Group Weak: Beaten-down laggards (<+25% above 365d USD low and below 200d MA)
        grp_weak = [
            c for c in top30
            if u_cush365.loc[d, c] < 0.25
            and (p.loc[d, c] < u_ma200.loc[d, c])
        ]

        daily_info[d] = {
            "top30": top30,
            "grp_L": grp_L,
            "grp_top5": grp_top5,
            "grp_weak": grp_weak,
            "btc_dd90": btc_dd90.loc[d],
            "btc_cush365": btc_cush365.loc[d],
        }

    # Condition for a BTC Pullback Event where narrative leaders are also pulling back:
    # BTC 90d DD <= -14% AND at least 2 Thick-Cushion Narrative Leaders in grp_L
    cand_dates = [
        d for d, info in daily_info.items()
        if info["btc_dd90"] <= -0.14 and len(info["grp_L"]) >= 2
    ]
    ev_dates_30 = decluster(cand_dates, 30)

    log(f"\nFound {len(ev_dates_30)} Declustered (30d gap) BTC Pullback Episodes (2019–2026) with >=2 Thick-Cushion Narrative Leaders:")
    log("-" * 105)

    horizons = [30, 60, 90, 180, 365]

    def classify_cycle_stage(d):
        # Classify each historical date into its macro BTC cycle stage for clarity
        if pd.Timestamp("2019-06-01") <= d <= pd.Timestamp("2019-12-31"):
            return "Early-Cycle Recovery Peak/Pullback (2019)"
        if pd.Timestamp("2020-01-01") <= d <= pd.Timestamp("2020-10-31"):
            return "Early Bull Pullback (2020 DeFi Summer)"
        if pd.Timestamp("2021-01-01") <= d <= pd.Timestamp("2021-08-31"):
            return "Mid-Bull Major Correction (2021 May-Jul)"
        if pd.Timestamp("2021-09-01") <= d <= pd.Timestamp("2022-12-31"):
            return "Cycle Top / Bear Transition (2021.11-2022)"
        if pd.Timestamp("2023-01-01") <= d <= pd.Timestamp("2024-09-30"):
            return "Early/Mid Bull Pullback (2023-2024)"
        if pd.Timestamp("2024-10-01") <= d <= pd.Timestamp("2025-05-31"):
            return "Bull Wave Pullback (Early 2025)"
        return "Late 2025 - 2026 Cycle"

    for d in ev_dates_30:
        info = daily_info[d]
        grp = info["grp_L"]
        stage = classify_cycle_stage(d)
        loc_i = p_ff.index.get_loc(d)

        # Format coins with their trailing 1y cushion at date d
        coin_tags = [f"{c}({u_cush365.loc[d, c]*100:+.0f}%)" for c in grp[:6]]
        f_str = ""
        for h in [30, 60, 90, 180]:
            if loc_i + h < len(p_ff.index):
                d_fwd = p_ff.index[loc_i + h]
                b_f = p_ff.loc[d_fwd, "BTC"] / p_ff.loc[d, "BTC"] - 1.0
                c_f = p_ff.loc[d_fwd, grp] / p_ff.loc[d, grp] - 1.0
                rel_f = ((1.0 + c_f) / (1.0 + b_f) - 1.0) * 100.0
                f_str += f" | +{h:>3}d: USD {c_f.mean()*100:+6.1f}% (vsBTC {rel_f.mean():+6.1f}%)"
        log(f"  {d.strftime('%Y-%m-%d')} [{stage:<38}] BTC90dDD={info['btc_dd90']*100:+5.1f}%")
        log(f"     Leaders (n={len(grp):>2}): {', '.join(coin_tags)}{f_str}")

    # =========================================================================
    # PART 2: SUMMARY STATISTICS BY CYCLE PHASE (EARLY/MID BULL PULLBACK vs BEAR TRANSITION)
    # =========================================================================
    log("\n" + "=" * 105)
    log("PART 2: STATISTICAL SUMMARY OF BUYING *THAT* CYCLE'S NARRATIVE LEADERS ON BTC PULLBACKS")
    log("=" * 105)

    def summarize_subset(title, sub_dates, grp_key="grp_L"):
        log(f"\n>>> {title} (n={len(sub_dates)} declustered 30d episodes)")
        for h in [30, 60, 90, 180, 365]:
            b_rets, ew_usds, ew_rels, med_rels, vs_weak = [], [], [], [], []
            coin_rels_pooled = []
            for d in sub_dates:
                info = daily_info[d]
                grp = info[grp_key]
                weak = info["grp_weak"]
                if len(grp) == 0:
                    continue
                loc_i = p_ff.index.get_loc(d)
                if loc_i + h >= len(p_ff.index):
                    continue
                d_fwd = p_ff.index[loc_i + h]
                b_f = p_ff.loc[d_fwd, "BTC"] / p_ff.loc[d, "BTC"] - 1.0
                c_f = p_ff.loc[d_fwd, grp] / p_ff.loc[d, grp] - 1.0
                rel_f = ((1.0 + c_f) / (1.0 + b_f) - 1.0) * 100.0

                b_rets.append(b_f * 100.0)
                ew_usds.append(c_f.mean() * 100.0)
                ew_rels.append(rel_f.mean())
                med_rels.append(rel_f.median())
                coin_rels_pooled.extend(rel_f.tolist())

                if len(weak) >= 2:
                    w_f = p_ff.loc[d_fwd, weak] / p_ff.loc[d, weak] - 1.0
                    vs_weak.append(((1.0 + c_f.mean()) / (1.0 + w_f.mean()) - 1.0) * 100.0)

            if len(ew_rels) == 0:
                continue
            b_a = np.array(b_rets)
            u_a = np.array(ew_usds)
            ew_a = np.array(ew_rels)
            md_a = np.array(med_rels)
            cp_a = np.array(coin_rels_pooled)
            vw_a = np.array(vs_weak)
            vw_str = f" | Beat Laggards: {(vw_a>0).mean()*100:3.0f}% (med {np.median(vw_a):+5.1f}%)" if len(vw_a) > 0 else ""
            log(
                f"  +{h:>3}d (n={len(ew_a):>2} eps, {len(cp_a):>3} coins): "
                f"BTC Med {np.median(b_a):+6.1f}% | "
                f"Leaders USD Win {(u_a>0).mean()*100:3.0f}% (med {np.median(u_a):+6.1f}%) | "
                f"EW Beat BTC: {(ew_a>0).mean()*100:3.0f}% (med {np.median(ew_a):+6.1f}%, mean {np.mean(ew_a):+6.1f}%) | "
                f"Single-Coin Beat BTC: {(cp_a>0).mean()*100:3.0f}% (med {np.median(cp_a):+6.1f}%)"
                f"{vw_str}"
            )

    # 1. All BTC Pullback Episodes (2019-2026)
    summarize_subset("1. ALL BTC PULLBACK EPISODES (2019–2026) — Buying That Moment's Thick-Cushion Narrative Leaders", ev_dates_30, "grp_L")

    # 2. Split into:
    #    (A) TRUE EARLY/MID-BULL PULLBACKS (where BTC was in a genuine bull recovery / mid-cycle correction:
    #        2020 DeFi Summer pullback, 2021 May-Jul mid-bull pullback, 2023-2024 early/mid-bull pullbacks, Early 2025, Summer 2026)
    #    (B) CYCLE-TOP / BEAR-TRANSITION PULLBACKS (2019 H2 post-mini-bull bleed, 2021 Nov - 2022 bear market, Autumn 2025 bleed)
    early_mid_bull_dates = [
        d for d in ev_dates_30
        if (pd.Timestamp("2020-03-01") <= d <= pd.Timestamp("2021-08-31"))
        or (pd.Timestamp("2023-01-01") <= d <= pd.Timestamp("2024-09-30"))
        or (pd.Timestamp("2025-01-01") <= d <= pd.Timestamp("2025-05-31"))
        or (pd.Timestamp("2026-01-01") <= d <= pd.Timestamp("2026-08-31"))
    ]
    bear_transition_dates = [d for d in ev_dates_30 if d not in early_mid_bull_dates]

    summarize_subset(
        "2A. IF THIS IS AN 'EARLY / MID-BULL PULLBACK' (2020, 2021 May-Jul, 2023-2024, 2025 Spring, 2026 Spring/Summer)",
        early_mid_bull_dates, "grp_L"
    )
    summarize_subset(
        "2B. IF THIS IS A 'MINI-BULL TOP / BEAR TRANSITION' (2019 H2, 2021.11-2022 Bear, 2025 Autumn)",
        bear_transition_dates, "grp_L"
    )

    # 3. Also split by OBJECTIVE EX-ANTE REGIME (No hindsight on whether bull continues!):
    #    - Ex-Ante Bull Pullback: BTC is still ABOVE its 200d MA at date d
    #    - Ex-Ante Deep Pullback (Like Today): BTC is BELOW its 200d MA, but still >= +10% above 365d low
    ex_ante_abv200 = [d for d in ev_dates_30 if btc.loc[d] > btc_ma200.loc[d]]
    ex_ante_bel200 = [d for d in ev_dates_30 if btc.loc[d] <= btc_ma200.loc[d]]

    summarize_subset("3A. EX-ANTE FILTER (Zero Hindsight): BTC Pullback while BTC > 200d MA", ex_ante_abv200, "grp_L")
    summarize_subset("3B. EX-ANTE FILTER (Zero Hindsight, Like TODAY): BTC Pullback while BTC <= 200d MA", ex_ante_bel200, "grp_L")

    # 4. Also test Group Top5 (Top-5 180d Relative-Strength Winners on Pullback) across Early/Mid Bull vs All
    summarize_subset("4. TOP-5 STRONGEST 180D NARRATIVE WINNERS ON PULLBACK — All Episodes (2021–2026)", ev_dates_30, "grp_top5")
    summarize_subset("4A. TOP-5 STRONGEST 180D NARRATIVE WINNERS ON PULLBACK — In Early/Mid-Bull Pullbacks", early_mid_bull_dates, "grp_top5")

    # =========================================================================
    # PART 3: 2018–2026 CMC WEEKLY TOP-50 POINT-IN-TIME NARRATIVE LEADERS
    #         (Specifically capturing 2019 Q3 Pullback & 2020 Sept DeFi Summer Pullback!)
    # =========================================================================
    log("\n" + "=" * 105)
    log("PART 3: 2019–2026 MULTI-CYCLE CHECK VIA CMC WEEKLY TOP-50 (INCLUDING 2019 & 2020 DEFI SUMMER)")
    log("=" * 105)
    cmc = pd.read_csv(ROOT / "data" / "backfill" / "cmc_weekly_top300.csv.gz")
    cmc["date"] = pd.to_datetime(cmc["date"])
    cmc["symbol"] = cmc["symbol"].str.upper()

    # Pivot by cmc_id to avoid ticker collisions, map cmc_id -> symbol at each date
    p_cmc = cmc.pivot_table(index="date", columns="cmc_id", values="price_usd").sort_index()
    m_cmc = cmc.pivot_table(index="date", columns="cmc_id", values="market_cap_usd").sort_index()
    sym_map = cmc.groupby("cmc_id")["symbol"].last().to_dict()

    btc_id = cmc[cmc["symbol"] == "BTC"]["cmc_id"].iloc[0]
    eth_id = cmc[cmc["symbol"] == "ETH"]["cmc_id"].iloc[0]

    excl_ids = {
        cid for cid, sym in sym_map.items()
        if sym in STABLES or sym in ("BTC", "ETH", "WBTC", "STETH", "WETH", "USDT", "USDC", "BUSD", "DAI", "TUSD", "USDD", "LEO", "BGB")
    }
    alt_ids = [cid for cid in p_cmc.columns if cid not in excl_ids]

    p_cmc_ff = p_cmc.ffill()
    btc_w = p_cmc[btc_id]
    btc_w_hi13 = btc_w.rolling(13, min_periods=6).max()
    btc_w_lo52 = btc_w.rolling(52, min_periods=26).min()
    btc_w_dd13 = btc_w / btc_w_hi13 - 1.0
    btc_w_cush52 = btc_w / btc_w_lo52 - 1.0

    rel_w = p_cmc[alt_ids].div(btc_w, axis=0)
    u_w_lo52 = p_cmc[alt_ids].rolling(52, min_periods=26).min()
    u_w_hi13 = p_cmc[alt_ids].rolling(13, min_periods=6).max()
    u_w_ma28 = p_cmc[alt_ids].rolling(28, min_periods=20).mean()  # ~200d MA
    rb_w_ma28 = rel_w.rolling(28, min_periods=20).mean()
    rb_w_ret26 = rel_w / rel_w.shift(26) - 1.0  # ~180d vs BTC

    u_w_cush52 = p_cmc[alt_ids] / u_w_lo52 - 1.0
    u_w_dd13 = p_cmc[alt_ids] / u_w_hi13 - 1.0

    cmc_episodes = []
    last_ep_d = None
    for d in p_cmc.index:
        if d < pd.Timestamp("2019-01-01"):
            continue
        if btc_w_dd13.loc[d] > -0.14 or btc_w_cush52.loc[d] < 0.25:
            continue
        m_row = m_cmc.loc[d, alt_ids].dropna()
        # Exclude stablecoin-like prices near $1.00 if symbol looks like USD
        valid_ids = [
            cid for cid in m_row.index
            if pd.notna(u_w_ma28.loc[d, cid]) and pd.notna(rb_w_ret26.loc[d, cid]) and pd.notna(p_cmc.loc[d, cid])
            and not (0.97 <= p_cmc.loc[d, cid] <= 1.03 and "USD" in sym_map.get(cid, ""))
        ]
        top50_ids = m_row[valid_ids].nlargest(50).index.tolist()

        leaders = [
            cid for cid in top50_ids
            if u_w_cush52.loc[d, cid] >= 0.60
            and rb_w_ret26.loc[d, cid] >= 0.10
            and ((p_cmc.loc[d, cid] > u_w_ma28.loc[d, cid]) or (rel_w.loc[d, cid] > rb_w_ma28.loc[d, cid]))
            and u_w_dd13.loc[d, cid] <= -0.18
        ]
        if len(leaders) >= 2:
            if last_ep_d is None or (d - last_ep_d).days >= 35:
                cmc_episodes.append((d, leaders))
                last_ep_d = d

    log(f"Found {len(cmc_episodes)} Declustered CMC Weekly Episodes (2019–2026):")
    for d, leaders in cmc_episodes:
        loc_i = p_cmc_ff.index.get_loc(d)
        tags = [f"{sym_map[cid]}({u_w_cush52.loc[d, cid]*100:+.0f}%)" for cid in leaders[:6]]
        f_str = ""
        for w_h, d_label in [(4, "30d"), (8, "60d"), (13, "90d"), (26, "180d")]:
            if loc_i + w_h < len(p_cmc_ff.index):
                d_fwd = p_cmc_ff.index[loc_i + w_h]
                b_f = p_cmc_ff.loc[d_fwd, btc_id] / p_cmc_ff.loc[d_fwd - pd.Timedelta(days=0), btc_id] if False else (p_cmc_ff.loc[d_fwd, btc_id] / p_cmc_ff.loc[d, btc_id] - 1.0)
                c_f = (p_cmc_ff.loc[d_fwd, leaders] / p_cmc_ff.loc[d, leaders] - 1.0).clip(-0.99, 5.0)
                rel_f = ((1.0 + c_f) / (1.0 + b_f) - 1.0) * 100.0
                f_str += f" | +{d_label}: USD {c_f.mean()*100:+6.1f}% (vsBTC {rel_f.mean():+6.1f}%)"
        log(f"  {d.strftime('%Y-%m-%d')} | BTC13wDD={btc_w_dd13.loc[d]*100:+5.1f}% | Leaders(n={len(leaders):>2}): {', '.join(tags)}{f_str}")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"\nSaved full output to {OUT_PATH}")


if __name__ == "__main__":
    main()
