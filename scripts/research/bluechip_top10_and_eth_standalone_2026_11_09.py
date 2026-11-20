"""Backtest:
Part A: Top-10 / Blue-Chip Altcoins (excluding BTC & ETH) ONLY:
  - Using both CMC historical market-cap Top-10 (2018..2026) and Binance PIT Top-10 by 30d volume (ex BTC, ETH, stables),
    plus the current Blue-Chip basket (SOL, BNB, XRP, DOGE, ADA, TRX, AVAX, LINK, SUI, NEAR, UNI, ZEC, LTC, BCH, DOT).
  - Where is the Top-10 Blue-Chip / BTC index right now?
  - When Top-10 Blue-Chips drop vs BTC like now, is bottom-fishing Top-10 Blue-Chips better than holding BTC?
Part B: Standalone ETH & ETH/BTC Deep Analysis & Backtest (2017..2026):
  - Full cycle structure of ETH/BTC: 2025-04-21 absolute low (0.01805), 2026-06-06 higher low (0.02578),
    2026-09-12 rally peak (0.03269), and current retracement (0.02843).
  - Historical backtest of ETH/BTC and ETH/USD when ETH/BTC is in a post-bottom higher-low structure
    but pulls back below 50d/200d MAs with RSI/30d relative return at current levels.

Question: Avalon (2026-11-09):
  "话说，我们目前应该有很多个山寨币吧，包括已经不在了的。不要50大，咱们就说蓝筹，或者前10。而且eth我是让你单独分析的"
Data:
  - data/universe/spot_usdt_1d_spliced.csv.gz (all Binance USDT spot pairs incl. delisted)
  - data/backfill/cmc_weekly_top300.csv.gz (CMC weekly historical market cap rankings 2018..2026)
  - data/market.snapshot.duckdb (bn_kline_1d, tv_bars, read-only)
Date: 2026-11-09
Outputs:
  - data/outputs/bluechip_top10_and_eth_standalone_2026-11-09.txt
"""
from pathlib import Path
import duckdb
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]

EXCL_SYMS = set("""
BTC ETH USDT USDC BUSD DAI TUSD PAX USDP GUSD HUSD USDK SUSD FRAX LUSD USDD FDUSD PYUSD USDE SUSDE USDS USD1
BSC-USD USDB USDX USDN UST USTC EURS EURT XAUT PAXG DGX WBTC WETH STETH WSTETH WEETH CBBTC BTCB RENBTC HBTC
TBTC LBTC SOLVBTC RETH METH CBETH JITOSOL BNSOL MSOL EZETH RSETH OSETH WBETH BETH SAVAX STSOL BUIDL USDTB
USYC OUSG USD0 RLUSD USDF USDG CLBTC WBNB WTRX WHYPE BGB LEO EUR
""".split())

def run():
    # =========================================================================
    # PART A: BLUE-CHIP / TOP-10 ALTCOINS (EX BTC & ETH)
    # =========================================================================
    u = pd.read_csv(REPO / "data/universe/spot_usdt_1d_spliced.csv.gz", parse_dates=["t"])
    print("=== 0. UNIVERSE INVENTORY ===")
    total_pairs = u["symbol"].nunique()
    latest_date = u["t"].max()
    active_today = u[u["t"] == latest_date]["symbol"].nunique()
    delisted_or_inactive = total_pairs - active_today
    print(f"Binance spliced spot USDT universe: {total_pairs} total historical coins ({active_today} active on {latest_date.date()}, {delisted_or_inactive} delisted/inactive)")

    C = u.pivot_table(index="t", columns="symbol", values="c")
    Q = u.pivot_table(index="t", columns="symbol", values="qv")
    C.index = pd.to_datetime(C.index).tz_localize(None)
    Q.index = pd.to_datetime(Q.index).tz_localize(None)

    def is_excl(col):
        base = col[:-4] if col.endswith("USDT") else col
        return (base in EXCL_SYMS) or any(base.startswith(p) for p in ["USD", "EUR", "WBTC", "STETH", "PAXG"])

    alt_cols = [s for s in C.columns if not is_excl(s)]
    vol30 = Q[alt_cols].rolling(30, min_periods=15).mean().shift(1)  # strictly trailing up to t-1

    btc = C["BTCUSDT"]
    eth = C["ETHUSDT"]
    rel30 = (C[alt_cols] / C[alt_cols].shift(30)).div(btc / btc.shift(30), axis=0) - 1
    rel90 = (C[alt_cols] / C[alt_cols].shift(90)).div(btc / btc.shift(90), axis=0) - 1
    dd90_usd = C[alt_cols] / C[alt_cols].rolling(90, min_periods=30).max() - 1
    ma50 = C[alt_cols] > C[alt_cols].rolling(50).mean()
    ma200 = C[alt_cols] > C[alt_cols].rolling(200).mean()

    # What are today's PIT Top-10 Alts (ex BTC, ETH, stables) by 30d volume?
    v_now = vol30.iloc[-1].dropna().sort_values(ascending=False)
    top10_now = v_now.head(10).index.tolist()
    print(f"\n=== 1. CURRENT TOP-10 BLUE-CHIP ALTS (EX BTC & ETH) BY 30D VOLUME ({C.index[-1].date()}) ===")
    for rank, s in enumerate(top10_now, 1):
        px_s = C.loc[C.index[-1], s]
        r30_s = rel30.loc[C.index[-1], s] * 100
        r90_s = rel90.loc[C.index[-1], s] * 100
        dd_s = dd90_usd.loc[C.index[-1], s] * 100
        m50_s = "YES" if ma50.loc[C.index[-1], s] else "NO"
        m200_s = "YES" if ma200.loc[C.index[-1], s] else "NO"
        print(f"  #{rank:2d} {s[:-4]:6s}: px={px_s:<10.4g} | 30d vs BTC={r30_s:+6.1f}% | 90d vs BTC={r90_s:+6.1f}% | 90d USD DD={dd_s:+6.1f}% | >50dMA={m50_s:3s} | >200dMA={m200_s:3s}")

    # Build daily PIT Top-10 (ex BTC & ETH) metrics & forward returns (with strict delisting handling: if delisted, use last traded price)
    rows = []
    dates = C.index[(C.index >= "2020-12-31") & (C.index <= "2026-11-08")]
    for dt in dates:
        v = vol30.loc[dt].dropna()
        t10 = v[v > 0].sort_values(ascending=False).head(10).index
        if len(t10) < 10:
            continue
        r1 = (C.loc[dt, t10] / C.shift(1).loc[dt, t10] - 1).mean()
        b1 = btc.loc[dt] / btc.shift(1).loc[dt] - 1
        r30 = rel30.loc[dt, t10].dropna()
        r90 = rel90.loc[dt, t10].dropna()
        d90 = dd90_usd.loc[dt, t10].dropna()
        rec = {
            "d": dt,
            "t10_vs_btc_1d": r1 - b1,
            "med_rel30": r30.median() * 100,
            "mean_rel30": r30.mean() * 100,
            "med_rel90": r90.median() * 100,
            "med_dd90": d90.median() * 100,
            "beat30_cnt": int((r30 > 0).sum()),
            "abv_ma50_cnt": int(ma50.loc[dt, t10].sum()),
            "abv_ma200_cnt": int(ma200.loc[dt, t10].sum()),
        }
        for h in (30, 60, 90, 180):
            t_fut = dt + pd.Timedelta(days=h)
            if t_fut not in C.index:
                continue
            b_f = btc.loc[t_fut] / btc.loc[dt] - 1
            # Check if any top10 delisted in window
            sub_window = C.loc[dt:t_fut, t10]
            end_px = sub_window.ffill().iloc[-1]
            delisted_mask = sub_window.iloc[-1].isna()
            # Conservative: if delisted, use last traded price (or penalize -50% extra; for Top 10 almost never delists except LUNA)
            ret_f = end_px / C.loc[dt, t10] - 1
            rec[f"btc_{h}"] = b_f * 100
            rec[f"t10_ew_{h}"] = ret_f.mean() * 100
            rec[f"t10_med_{h}"] = ret_f.median() * 100
            rec[f"t10_ew_vs_btc_{h}"] = ((1 + ret_f.mean()) / (1 + b_f) - 1) * 100
            rec[f"t10_med_vs_btc_{h}"] = ((1 + ret_f.median()) / (1 + b_f) - 1) * 100
            rec[f"t10_beat_share_{h}"] = (ret_f > b_f).mean() * 100
            rec[f"delist_cnt_{h}"] = int(delisted_mask.sum())
        rows.append(rec)

    t10_df = pd.DataFrame(rows).set_index("d")
    t10_df["cum_log_rel"] = np.log(1 + t10_df["t10_vs_btc_1d"].clip(-0.5, 0.5)).cumsum()
    t10_df["rel_idx"] = np.exp(t10_df["cum_log_rel"]) * 100

    now10 = t10_df.iloc[-1]
    print(f"\nTop-10 Blue-Chip Summary Today ({t10_df.index[-1].date()}):")
    print(f"  30d vs BTC median: {now10.med_rel30:+.1f}% (mean {now10.mean_rel30:+.1f}%) | 90d vs BTC median: {now10.med_rel90:+.1f}% | 90d USD DD: {now10.med_dd90:+.1f}%")
    print(f"  Count beating BTC (30d): {int(now10.beat30_cnt)}/10 | >50d MA: {int(now10.abv_ma50_cnt)}/10 | >200d MA: {int(now10.abv_ma200_cnt)}/10")

    # Compare 2018-2026 CMC Weekly Top-10 (ex BTC, ETH, stables) for multi-cycle perspective (2019 vs 2023 vs 2026)
    print("\n=== 2. MULTI-CYCLE TOP-10 BLUE-CHIP VS BTC (CMC Weekly 2018..2026 + Binance PIT Daily) ===")
    cmc = pd.read_csv(REPO / "data/backfill/cmc_weekly_top300.csv.gz")
    cmc["symbol"] = cmc["symbol"].str.upper()
    snaps = {t: g.set_index("cmc_id") for t, g in cmc.groupby("date")}
    s_dates = sorted(snaps)
    cmc_rows = []
    for a, b in zip(s_dates[:-1], s_dates[1:]):
        sa, sb = snaps[a], snaps[b]
        btc_a = sa[sa.symbol == "BTC"].price_usd.iloc[0]
        btc_b = sb[sb.symbol == "BTC"].price_usd.iloc[0]
        el = sa[~sa.symbol.isin(EXCL_SYMS) & (sa.market_cap_usd > 0) & (sa.price_usd > 0)]
        el = el[~el.price_usd.between(0.97, 1.03)].sort_values("market_cap_usd", ascending=False).head(10)
        el = el[el.index.isin(sb.index)]
        r = (sb.loc[el.index, "price_usd"] / el.price_usd - 1).clip(-0.99, 5.0)
        r_btc = btc_b / btc_a - 1
        cw10 = (r * el.market_cap_usd).sum() / el.market_cap_usd.sum()
        ew10 = r.mean()
        cmc_rows.append({"d": pd.Timestamp(b), "btc": r_btc, "cw10": cw10, "ew10": ew10})
    cdf = pd.DataFrame(cmc_rows).set_index("d")
    cdf["ew10_rel"] = np.exp((np.log(1 + cdf["ew10"]) - np.log(1 + cdf["btc"])).cumsum()) * 100
    cdf["cw10_rel"] = np.exp((np.log(1 + cdf["cw10"]) - np.log(1 + cdf["btc"])).cumsum()) * 100
    print(f"CMC Weekly Top-10 History: {cdf.index[0].date()} -> {cdf.index[-1].date()}")
    # Compare 2019 cycle low (2018-12-16) and 2023 cycle low (2022-11-20) on CMC Top-10, plus 2023 and 2026 on Binance PIT Top-10
    for name, lo_d in [("2019 Cycle (Low 2018-12-16, CMC Top-10)", "2018-12-16"), ("2023 Cycle (Low 2022-11-20, CMC Top-10)", "2022-11-20")]:
        sub = cdf[cdf.index >= pd.Timestamp(lo_d)].copy()
        sub["days"] = (sub.index - sub.index[0]).days
        sub["idx"] = sub["ew10_rel"] / sub["ew10_rel"].iloc[0] * 100
        w150 = sub[sub.days <= 150]
        pk = w150["idx"].max(); pk_d = w150["idx"].idxmax().date(); pk_days = int(w150.loc[w150["idx"].idxmax(), "days"])
        d131 = sub[sub.days <= 133].iloc[-1]
        w365 = sub[sub.days <= 365]
        lo1y = w365["idx"].min(); lo1y_d = w365["idx"].idxmax() if False else w365["idx"].idxmin().date(); lo1y_days = int(w365.loc[w365["idx"].idxmin(), "days"])
        print(f"  {name}: Bounce peak={pk:.1f} at +{pk_days}d ({pk_d}) | At ~+131d ({d131.name.date()}): {d131.idx:.1f} ({d131.idx/pk-1:+.1%} from peak) | 1y trough={lo1y:.1f} at +{lo1y_days}d ({lo1y_d})")

    for name, lo_d in [("2023 Cycle (Low 2022-11-21, Binance PIT Top-10)", "2022-11-21"), ("2026 Cycle (Low 2026-07-01, Binance PIT Top-10)", "2026-07-01")]:
        sub = t10_df[t10_df.index >= pd.Timestamp(lo_d)].copy()
        sub["days"] = (sub.index - sub.index[0]).days
        sub["idx"] = sub["rel_idx"] / sub["rel_idx"].iloc[0] * 100
        w150 = sub[sub.days <= 150]
        pk = w150["idx"].max(); pk_d = w150["idx"].idxmax().date(); pk_days = int(w150.loc[w150["idx"].idxmax(), "days"])
        d131 = sub[sub.days <= 131].iloc[-1]
        w365 = sub[sub.days <= 365]
        lo1y = w365["idx"].min(); lo1y_d = w365["idx"].idxmin().date(); lo1y_days = int(w365.loc[w365["idx"].idxmin(), "days"])
        print(f"  {name}: Bounce peak={pk:.1f} at +{pk_days}d ({pk_d}) | At +131d ({d131.name.date()}): {d131.idx:.1f} ({d131.idx/pk-1:+.1%} from peak) | Trough so far={lo1y:.1f} at +{lo1y_days}d ({lo1y_d})")

    # Backtest: Bottom-fishing Top-10 Blue-Chip Alts vs Holding BTC
    print("\n=== 3. BACKTEST: BOTTOM-FISHING TOP-10 BLUE-CHIP ALTS VS HOLDING BTC (2021..2026) ===")
    def test_t10_regime(label, mask, gap=30):
        sub = t10_df.loc[:"2026-08-01"][mask.loc[:"2026-08-01"]]
        ev = []
        for d in sub.index:
            if not ev or (d - ev[-1]).days >= gap:
                ev.append(d)
        e = t10_df.loc[ev]
        print(f"\n--- {label} (Declustered events={len(e)}, total days={len(sub)}) ---")
        for d in ev:
            r = t10_df.loc[d]
            print(f"    Event {d.date()}: med_rel30={r.med_rel30:+.1f}%, >50dMA={int(r.abv_ma50_cnt)}/10, >200dMA={int(r.abv_ma200_cnt)}/10 "
                  f"-> T10 vs BTC: 30d={r.t10_ew_vs_btc_30:+.1f}%, 60d={r.t10_ew_vs_btc_60:+.1f}%, 90d={r.t10_ew_vs_btc_90:+.1f}% (BTC 90d={r.btc_90:+.1f}%)")
        for h in (30, 60, 90, 180):
            s_ew = e[f"t10_ew_vs_btc_{h}"].dropna()
            s_med = e[f"t10_med_vs_btc_{h}"].dropna()
            s_btc = e[f"btc_{h}"].dropna()
            s_abs = e[f"t10_ew_{h}"].dropna()
            dl = int(e[f"delist_cnt_{h}"].sum()) if f"delist_cnt_{h}" in e else 0
            print(f"  Horizon {h:3d}d (n={len(s_ew)}, delisted coins in window={dl}):")
            print(f"    Hold BTC abs           : win={(s_btc>0).mean():.0%} ({int((s_btc>0).sum())}/{len(s_btc)}), median={s_btc.median():+.1f}%")
            print(f"    Top-10 EW vs BTC       : beat BTC={(s_ew>0).mean():.0%} ({int((s_ew>0).sum())}/{len(s_ew)}), median excess={s_ew.median():+.1f}% (abs median={s_abs.median():+.1f}%)")
            print(f"    Top-10 Median Coin vs BTC: beat BTC={(s_med>0).mean():.0%} ({int((s_med>0).sum())}/{len(s_med)}), median excess={s_med.median():+.1f}%")

    # (A) Today's exact Blue-Chip setup: Top-10 med_rel30 <= -6%, >50dMA <= 2/10, >200dMA >= 2/10
    m_now_setup = (t10_df["med_rel30"] <= -6.0) & (t10_df["abv_ma50_cnt"] <= 2) & (t10_df["abv_ma200_cnt"] >= 2)
    test_t10_regime("Setup A: Current Top-10 Setup (med_rel30 <= -6%, <=2/10 above 50dMA, >=2/10 above 200dMA)", m_now_setup)

    # (B) Unconditional "Top-10 Blue-Chips dropped a lot vs BTC" (med_rel30 <= -10%, <=2/10 above 50dMA, regardless of 200dMA)
    m_oversold = (t10_df["med_rel30"] <= -10.0) & (t10_df["abv_ma50_cnt"] <= 2)
    test_t10_regime("Setup B: All Top-10 Heavy Bleed Events (med_rel30 <= -10% & <=2/10 above 50dMA)", m_oversold)

    # (C) Deep Washout of Top-10 (0/10 above 50dMA AND 0/10 above 200dMA AND 90d USD drawdown <= -40%)
    m_washout = (t10_df["abv_ma50_cnt"] == 0) & (t10_df["abv_ma200_cnt"] == 0) & (t10_df["med_dd90"] <= -40.0)
    test_t10_regime("Setup C: Top-10 Total Capitulation (0/10 > 50dMA, 0/10 > 200dMA, 90d USD DD <= -40%)", m_washout)

    # (D) Right-side Trend Reclaim of Top-10 (after >=45d with <=3/10 above 50dMA, Top-10 reclaims >=6/10 above 50dMA & >=5/10 beating BTC over 30d)
    drought = (t10_df["abv_ma50_cnt"] <= 3).rolling(45).mean().shift(1) >= 0.75
    m_reclaim = drought & (t10_df["abv_ma50_cnt"] >= 6) & (t10_df["beat30_cnt"] >= 5)
    test_t10_regime("Setup D: Top-10 Right-Side Reclaim (>=6/10 > 50dMA & >=5/10 beating BTC after >=45d drought)", m_reclaim)

    # =========================================================================
    # PART B: STANDALONE ETH & ETH/BTC DEEP ANALYSIS (2017..2026)
    # =========================================================================
    print("\n=========================================================================")
    print("=== PART B: STANDALONE ETH & ETH/BTC ANALYSIS AND BACKTEST (2017..2026) ===")
    print("=========================================================================")
    con = duckdb.connect(str(REPO / "data/market.snapshot.duckdb"), read_only=True)
    k = con.execute("""
        SELECT CAST(ts AS DATE) d, symbol, close, high, low
        FROM bn_kline_1d
        WHERE market='spot' AND symbol IN ('BTC', 'ETH')
        ORDER BY d
    """).df()
    p = k.pivot_table(index="d", columns="symbol", values="close").dropna()
    p.index = pd.to_datetime(p.index)
    p["r"] = p["ETH"] / p["BTC"]
    p["r_ma20"] = p["r"].rolling(20).mean()
    p["r_ma50"] = p["r"].rolling(50).mean()
    p["r_ma100"] = p["r"].rolling(100).mean()
    p["r_ma200"] = p["r"].rolling(200).mean()
    p["r_chg30"] = p["r"] / p["r"].shift(30) - 1
    p["r_chg90"] = p["r"] / p["r"].shift(90) - 1
    p["r_low365"] = p["r"].rolling(365, min_periods=100).min()
    p["r_high180"] = p["r"].rolling(180, min_periods=60).max()
    p["r_above_1y_low"] = p["r"] / p["r_low365"] - 1
    p["r_dd_180h"] = p["r"] / p["r_high180"] - 1

    # RSI-14 on ETH/BTC and ETH/USD
    def calc_rsi(s, n=14):
        delta = s.diff()
        up = delta.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
        dn = (-delta.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
        return 100 - 100 / (1 + up / dn)

    p["r_rsi14"] = calc_rsi(p["r"], 14)
    p["eth_rsi14"] = calc_rsi(p["ETH"], 14)
    p["eth_ma200"] = p["ETH"].rolling(200).mean()

    for h in (30, 60, 90, 180):
        p[f"r_fwd{h}"] = (p["r"].shift(-h) / p["r"] - 1) * 100
        p[f"eth_fwd{h}"] = (p["ETH"].shift(-h) / p["ETH"] - 1) * 100
        p[f"btc_fwd{h}"] = (p["BTC"].shift(-h) / p["BTC"] - 1) * 100

    now_e = p.iloc[-1]
    print(f"Data range: {p.index[0].date()} -> {p.index[-1].date()} ({len(p)} days)")
    print(f"Current ETH/USD: {now_e.ETH:,.2f} (RSI14={now_e.eth_rsi14:.1f}, vs 200dMA={(now_e.ETH/now_e.eth_ma200-1)*100:+.1f}%)")
    print(f"Current ETH/BTC: {now_e.r:.5f} (RSI14={now_e.r_rsi14:.1f}, 30d chg={now_e.r_chg30*100:+.1f}%, 90d chg={now_e.r_chg90*100:+.1f}%)")
    print(f"  2025-04-21 Multi-Year Bottom : 0.01805 (current is +{(now_e.r/0.01805-1)*100:.1f}% above)")
    print(f"  2025-08-24 Post-Bottom Peak  : 0.04212 (current is {(now_e.r/0.04212-1)*100:+.1f}% from peak, retraced {(0.04212-now_e.r)/(0.04212-0.01805)*100:.1f}%)")
    print(f"  2026-02-15 Stage Low         : 0.02857")
    print(f"  2026-06-06 2026 Bottom       : 0.02578 (current is +{(now_e.r/0.02578-1)*100:.1f}% above)")
    print(f"  2026-09-12 2026 Rally Peak   : 0.03269 (current is {(now_e.r/0.03269-1)*100:+.1f}% from peak, retraced {(0.03269-now_e.r)/(0.03269-0.02578)*100:.1f}%)")
    print(f"  Moving Averages              : MA20={now_e.r_ma20:.5f} ({(now_e.r/now_e.r_ma20-1)*100:+.1f}%), MA50={now_e.r_ma50:.5f} ({(now_e.r/now_e.r_ma50-1)*100:+.1f}%), MA100={now_e.r_ma100:.5f} ({(now_e.r/now_e.r_ma100-1)*100:+.1f}%), MA200={now_e.r_ma200:.5f} ({(now_e.r/now_e.r_ma200-1)*100:+.1f}%)")

    # Compare the 3 historical ETH/BTC bottom-and-retest cycles:
    # Cycle 1: 2019-09-05 Bottom (0.01640) -> 2020-02-18 Peak (0.02858) -> 2020-03..05 Retest
    # Cycle 2: 2022-06-13 Bottom (0.05375) -> 2022-09-07 Peak (0.08448) -> 2022-10..2023-03 Retest
    # Cycle 3: 2025-04-21 Bottom (0.01805) -> 2025-08-24 Peak (0.04212) -> 2026-06-06 Higher Low (0.02578) -> 2026-09-12 Bounce (0.03269) -> Now (0.02843)
    print("\n--- Historical ETH/BTC Bottom -> Rally -> Deep Retracement Comparisons ---")
    episodes = [
        ("2019-2020 Bottom & Retest", "2019-09-05", "2020-02-18", "2020-03-12"),
        ("2020 Spring 2nd Retest",    "2019-09-05", "2020-02-18", "2020-05-14"),
        ("2022 Bottom & Post-Merge",  "2022-06-13", "2022-09-07", "2022-11-09"),
        ("2025-2026 June Higher Low", "2025-04-21", "2025-08-24", "2026-06-06"),
        ("2026 June->Sept->Nov (NOW)","2026-06-06", "2026-09-12", "2026-11-09"),
    ]
    for name, d_lo, d_hi, d_rt in episodes:
        v_lo = p.loc[d_lo, "r"]
        v_hi = p.loc[d_hi, "r"]
        v_rt = p.loc[d_rt, "r"]
        ret_pct = (v_hi - v_rt) / (v_hi - v_lo) * 100
        rt_row = p.loc[d_rt]
        print(f"  {name:28s}: Low {d_lo} ({v_lo:.5f}) -> Peak {d_hi} ({v_hi:.5f}, +{(v_hi/v_lo-1)*100:.1f}%) -> Retest {d_rt} ({v_rt:.5f}, retraced {ret_pct:.1f}% of rally) | "
              f"From retest: ETH/BTC 30d={rt_row.r_fwd30:+.1f}%, 90d={rt_row.r_fwd90:+.1f}%, 180d={rt_row.r_fwd180:+.1f}%")

    # Systematic Backtest on ETH/BTC:
    # What happens when ETH/BTC crosses/sits below its 200d MA and 50d MA (30d chg <= -5%),
    # split by whether ETH/BTC is in a structural downtrend (making new 1y lows) vs holding a higher low (>5% above 1y low)?
    print("\n--- Systematic ETH/BTC Backtest (2018..2026, Declustered 30d) ---")
    def test_eth_cond(label, mask, gap=30):
        sub = p.loc["2018-05-01":"2026-08-01"][mask.loc["2018-05-01":"2026-08-01"]]
        ev = []
        for d in sub.index:
            if not ev or (d - ev[-1]).days >= gap:
                ev.append(d)
        e = p.loc[ev]
        print(f"\n  {label} (events={len(e)}, days={len(sub)}):")
        for d in ev:
            r = p.loc[d]
            print(f"    {d.date()}: ETH/BTC={r.r:.5f}, 30d={r.r_chg30*100:+.1f}%, vs200d={(r.r/r.r_ma200-1)*100:+.1f}%, abv1yLow={r.r_above_1y_low*100:+.1f}% "
                  f"-> ETH/BTC 30d={r.r_fwd30:+.1f}%, 60d={r.r_fwd60:+.1f}%, 90d={r.r_fwd90:+.1f}%, 180d={r.r_fwd180:+.1f}% | ETH/USD 90d={r.eth_fwd90:+.1f}%")
        for h in (30, 60, 90, 180):
            sr = e[f"r_fwd{h}"].dropna()
            se = e[f"eth_fwd{h}"].dropna()
            sb = e[f"btc_fwd{h}"].dropna()
            print(f"    Fwd {h:3d}d (n={len(sr)}): ETH beats BTC={(sr>0).mean():.0%} ({int((sr>0).sum())}/{len(sr)}), median ETH/BTC={sr.median():+.1f}% | ETH/USD med={se.median():+.1f}% vs BTC/USD med={sb.median():+.1f}%")

    # Condition 1: Like today — ETH/BTC < MA200 & < MA50, 30d chg <= -5%, RSI14 <= 40, BUT still +5%..+30% above 365d low (Higher-Low Retest)
    c_hl_retest = (
        (p["r"] < p["r_ma200"])
        & (p["r"] < p["r_ma50"])
        & (p["r_chg30"] <= -0.05)
        & (p["r_rsi14"] <= 40)
        & (p["r_above_1y_low"].between(0.05, 0.35))
    )
    test_eth_cond("ETH/BTC Higher-Low Retest below 200dMA (like NOW: <MA200, 30d<=-5%, RSI<=40, +5%..+35% above 1y low)", c_hl_retest)

    # Condition 2: ETH/BTC breaking to new 1y lows (<2% above 1y low, <MA200, 30d<=-5%, RSI<=40)
    c_breakdown = (
        (p["r"] < p["r_ma200"])
        & (p["r_chg30"] <= -0.05)
        & (p["r_rsi14"] <= 40)
        & (p["r_above_1y_low"] < 0.02)
    )
    test_eth_cond("ETH/BTC New-Low Breakdown (<MA200, 30d<=-5%, RSI<=40, within 2% of 1y low)", c_breakdown)

    # Condition 3: Right-side reclaim of 200d MA after spending >=60 days below 200d MA
    below200_60 = (p["r"] < p["r_ma200"]).rolling(60).mean().shift(1) >= 0.85
    c_reclaim200 = below200_60 & (p["r"] > p["r_ma200"]) & (p["r"] > p["r_ma50"])
    test_eth_cond("ETH/BTC Right-Side Reclaim of 200dMA & 50dMA (after >=60d below 200dMA)", c_reclaim200)

if __name__ == "__main__":
    run()
