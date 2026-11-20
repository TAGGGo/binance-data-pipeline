"""Backtest: Which historical period does BTC resemble right now, and what stage are altcoins in vs BTC?

Question: Avalon (2026-11-09): "帮我做个回测 目前比特币更像以前的哪个时候 以及山寨币相对于比特币 处于哪个阶段"
Data:
  - data/onchain/self/urpd_metrics_daily.csv (MVRV, PSIP, Realized Price, vol30, Seller Exhaustion, 2010..2026-11-08)
  - data/onchain/self/sth_daily.csv (STH-RP, STH-MVRV, STH-SOPR, 2011..2026-11-08)
  - data/universe/spot_usdt_1d_spliced.csv.gz (Survivorship-free Binance USDT spot universe, 2019..2026-11-09)
  - data/market.snapshot.duckdb (bn_kline_1d, tv_bars_1d for BTC.D / TOTAL3, read-only)
Date: 2026-11-09
Outputs:
  - data/outputs/btc_and_alts_stage_2026-11-09.txt
"""
from pathlib import Path
import duckdb
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]

def run():
    # 1. Load BTC on-chain & price history
    urpd = pd.read_csv(REPO / "data/onchain/self/urpd_metrics_daily.csv", parse_dates=["d"]).set_index("d")
    sth = pd.read_csv(REPO / "data/onchain/self/sth_daily.csv", parse_dates=["d"]).set_index("d")
    df = urpd.join(sth[["sth_rp", "sth_mvrv", "sth_sopr"]], how="inner").sort_index()
    df = df[df.index >= "2013-01-01"].copy()

    # Add today's live reading (2026-11-09, close=74683)
    last = df.iloc[-1].copy()
    live_px = 74683.0
    scale = live_px / last["close"]
    today_row = last.copy()
    today_row["close"] = live_px
    today_row["mvrv"] = last["mvrv"] * scale
    today_row["sth_mvrv"] = live_px / last["sth_rp"]
    today_row["psip"] = 58.1
    df.loc[pd.Timestamp("2026-11-09")] = today_row

    df["ma50"] = df["close"].rolling(50).mean()
    df["ma200"] = df["close"].rolling(200).mean()
    df["ma365"] = df["close"].rolling(365).mean()
    df["px_ma200"] = df["close"] / df["ma200"] - 1
    df["px_ma365"] = df["close"] / df["ma365"] - 1
    df["high_730"] = df["close"].rolling(730, min_periods=200).max()
    df["dd_from_high"] = df["close"] / df["high_730"] - 1
    df["low_180"] = df["close"].rolling(180, min_periods=30).min()
    df["bounce_from_low180"] = df["close"] / df["low_180"] - 1
    df["se_pct"] = df["seller_exh"].expanding(365).rank(pct=True) * 100
    df["ret30"] = df["close"].shift(-30) / df["close"] - 1
    df["ret60"] = df["close"].shift(-60) / df["close"] - 1
    df["ret90"] = df["close"].shift(-90) / df["close"] - 1
    df["ret180"] = df["close"].shift(-180) / df["close"] - 1
    df["maxdd_30"] = pd.concat([df["close"].shift(-k) for k in range(1, 31)], axis=1).min(axis=1) / df["close"] - 1

    now = df.iloc[-1]
    print("=== 1. CURRENT BTC STATE (2026-11-09) ===")
    print(f"Price: {now.close:,.0f} | Realized Price: {now.realized_price:,.0f} | STH-RP: {now.sth_rp:,.0f}")
    print(f"MVRV: {now.mvrv:.2f} | STH-MVRV: {now.sth_mvrv:.3f} | PSIP: {now.psip:.1f}% | SellerExh pct: {now.se_pct:.1f}%")
    print(f"vs 200d MA: {now.px_ma200:+.1%} | vs 365d MA: {now.px_ma365:+.1%} | DD from 2y high: {now.dd_from_high:+.1%} | Bounce from 180d low: {now.bounce_from_low180:+.1%}")

    # Cycle timeline context
    # Cycle tops: 2013-11-30, 2017-12-16, 2021-11-08, 2025-10-06
    cycles = [
        ("2013-2015", "2013-11-30", "2015-01-14"),
        ("2017-2019", "2017-12-16", "2018-12-15"),
        ("2021-2023", "2021-11-08", "2022-11-21"),
        ("2025-2026", "2025-10-06", "2026-07-01"),
    ]
    print("\n=== 2. CYCLE TIMELINE COMPARISON ===")
    for name, top_d, low_d in cycles:
        t_top = pd.Timestamp(top_d)
        t_low = pd.Timestamp(low_d)
        px_top = df.loc[t_top, "close"] if t_top in df.index else np.nan
        px_low = df.loc[t_low, "close"] if t_low in df.index else np.nan
        d_top_to_low = (t_low - t_top).days
        dd_low = px_low / px_top - 1
        # Check day +131 after low (same as today is 2026-07-01 + 131d = 2026-11-09)
        t_plus131 = t_low + pd.Timedelta(days=131)
        if t_plus131 in df.index:
            r131 = df.loc[t_plus131]
            print(f"{name}: Top {top_d} ({px_top:,.0f}) -> Low {low_d} ({px_low:,.0f}, {dd_low:+.1%} in {d_top_to_low}d) | "
                  f"At Low+131d ({t_plus131.date()}): px={r131.close:,.0f} ({r131.close/px_low-1:+.1%} vs low), "
                  f"MVRV={r131.mvrv:.2f}, STH-MVRV={r131.sth_mvrv:.2f}, PSIP={r131.psip:.1f}%, SE_pct={r131.se_pct:.0f}% | "
                  f"fwd 30d={r131.ret30:+.1%}, 90d={r131.ret90:+.1%}, 180d={r131.ret180:+.1%}")

    # 3. Nearest-Neighbor Multi-Factor Analog Search (declustered by 30 days, excluding last 90 days)
    feats = ["mvrv", "sth_mvrv", "psip", "se_pct", "px_ma200", "dd_from_high", "bounce_from_low180"]
    weights = np.array([1.2, 1.5, 1.5, 1.0, 1.0, 1.0, 1.0])
    hist = df.loc["2014-01-01":"2026-08-01"].dropna(subset=feats).copy()
    means = hist[feats].mean()
    stds = hist[feats].std()
    z_hist = (hist[feats] - means) / stds
    z_now = (now[feats] - means) / stds
    dist = np.sqrt(((z_hist - z_now) ** 2 * weights).sum(axis=1))
    hist["dist"] = dist

    # Pick top declustered matches (>=45 days apart)
    picked = []
    for d, row in hist.sort_values("dist").iterrows():
        if all(abs((d - p).days) >= 45 for p in picked):
            picked.append(d)
        if len(picked) >= 12:
            break

    print("\n=== 3. TOP 12 HISTORICAL ANALOG DATES FOR BTC (Multi-Factor Nearest Neighbors) ===")
    for rank, d in enumerate(picked, 1):
        r = hist.loc[d]
        print(f"#{rank:2d} {d.date()} (dist={r.dist:.2f}): px={r.close:,.0f} | MVRV={r.mvrv:.2f} | STH-MVRV={r.sth_mvrv:.2f} | "
              f"PSIP={r.psip:.1f}% | SE_pct={r.se_pct:.0f}% | vs200d={r.px_ma200:+.1%} | DD={r.dd_from_high:+.1%} | "
              f"Bounce180={r.bounce_from_low180:+.1%} || Fwd 30d={r.ret30:+.1%} (maxDD {r.maxdd_30:+.1%}), 60d={r.ret60:+.1%}, 90d={r.ret90:+.1%}, 180d={r.ret180:+.1%}")

    m_df = hist.loc[picked]
    print("\nTop-12 Analogs Summary:")
    for h in [30, 60, 90, 180]:
        s = m_df[f"ret{h}"].dropna()
        print(f"  Fwd {h:3d}d: win={(s>0).mean():.0%} ({int((s>0).sum())}/{len(s)}), median={s.median():+.1%}, mean={s.mean():+.1%}")

    # 4. Regime Filter Backtest:
    # Condition matching today:
    # - MVRV > 1.15 and < 1.65 (not deep bear MVRV<1, not overheated MVRV>2)
    # - PSIP between 52% and 65%
    # - STH-MVRV between 0.92 and 1.03 (pullback right around/below STH cost basis)
    # - Seller Exhaustion in lowest 25% of history (se_pct <= 25)
    cond = (
        df["mvrv"].between(1.15, 1.65)
        & df["psip"].between(52, 65)
        & df["sth_mvrv"].between(0.92, 1.03)
        & (df["se_pct"] <= 25)
    )
    sub = df.loc["2014-01-01":"2026-08-01"][cond.loc["2014-01-01":"2026-08-01"]]
    # Decluster by 30d
    ev_dates = []
    for d in sub.index:
        if not ev_dates or (d - ev_dates[-1]).days >= 30:
            ev_dates.append(d)
    print(f"\n=== 4. REGIME CONDITION BACKTEST (MVRV 1.15..1.65 & PSIP 52..65% & STH-MVRV 0.92..1.03 & SE<=25%) ===")
    print(f"Total matching days: {len(sub)}, Declustered events (30d gap): {len(ev_dates)}")
    df["min_psip_365"] = df["psip"].rolling(365, min_periods=60).min()
    for d in ev_dates:
        r = df.loc[d]
        print(f"  {d.date()}: px={r.close:,.0f}, MVRV={r.mvrv:.2f}, STH-MVRV={r.sth_mvrv:.2f}, PSIP={r.psip:.1f}% (1y min {r.min_psip_365:.1f}%), SE={r.se_pct:.0f}%, vs200d={r.px_ma200:+.1%} "
              f"-> 30d={r.ret30:+.1%} (dd30={r.maxdd_30:+.1%}), 60d={r.ret60:+.1%}, 90d={r.ret90:+.1%}, 180d={r.ret180:+.1%}")
    ev_df = df.loc[ev_dates]
    for h in [30, 60, 90, 180]:
        s = ev_df[f"ret{h}"].dropna()
        print(f"  All Events Fwd {h:3d}d: win={(s>0).mean():.0%} ({int((s>0).sum())}/{len(s)}), median={s.median():+.1%}")

    # Split events into:
    # (A) Already flushed below 50% PSIP in past year (like 2016, 2023, and 2026 after 2026-07-01 / 2026-08-16 where PSIP hit <50%!)
    # (B) Never flushed below 50% PSIP yet (like 2014-06..08, 2018-09..10, 2026-04..05)
    flushed = ev_df[ev_df["min_psip_365"] < 50.0]
    unflushed = ev_df[ev_df["min_psip_365"] >= 50.0]
    print("\n  --> Sub-regime A: Already flushed PSIP < 50% in prior 365d (like NOW, since 2026-08-16 hit <50%):")
    for d, r in flushed.iterrows():
        print(f"      {d.date()}: 30d={r.ret30:+.1%}, 60d={r.ret60:+.1%}, 90d={r.ret90:+.1%}, 180d={r.ret180:+.1%}")
    for h in [30, 60, 90, 180]:
        s = flushed[f"ret{h}"].dropna()
        print(f"      Flushed (<50% PSIP done) Fwd {h:3d}d: win={(s>0).mean():.0%} ({int((s>0).sum())}/{len(s)}), median={s.median():+.1%}")

    print("  --> Sub-regime B: Had NOT flushed PSIP < 50% in prior 365d (mid-bear plateau before capitulation):")
    for d, r in unflushed.iterrows():
        print(f"      {d.date()}: 30d={r.ret30:+.1%}, 60d={r.ret60:+.1%}, 90d={r.ret90:+.1%}, 180d={r.ret180:+.1%}")
    for h in [30, 60, 90, 180]:
        s = unflushed[f"ret{h}"].dropna()
        print(f"      Unflushed (PSIP>=50% all yr) Fwd {h:3d}d: win={(s>0).mean():.0%} ({int((s>0).sum())}/{len(s)}), median={s.median():+.1%}")

    # 5. ALTCOINS VS BTC STAGE ANALYSIS
    print("\n=== 5. ALTCOINS VS BTC STAGE (Survivorship-Free PIT Universe & Cycle Comparison) ===")
    u = pd.read_csv(REPO / "data/universe/spot_usdt_1d_spliced.csv.gz", parse_dates=["t"])
    C = u.pivot_table(index="t", columns="symbol", values="c")
    Q = u.pivot_table(index="t", columns="symbol", values="qv")
    STABLE_SYMS = {s for s in C.columns if any(s.startswith(x) for x in ["USDC", "FDUSD", "TUSD", "BUSD", "USDP", "DAI", "PAXG", "EUR", "WBTC", "UST", "USDE", "USD1"])}
    alt_cols = [s for s in C.columns if s != "BTCUSDT" and s not in STABLE_SYMS]
    vol30 = Q[alt_cols].rolling(30, min_periods=15).mean()

    # Build daily top-50 equal-weight alt vs BTC index & breadth metrics (2019..2026)
    btc_ret = C["BTCUSDT"].pct_change(fill_method=None)
    alt_ret = C[alt_cols].pct_change(fill_method=None)
    rel_30 = (C[alt_cols] / C[alt_cols].shift(30)).div(C["BTCUSDT"] / C["BTCUSDT"].shift(30), axis=0) - 1
    rel_90 = (C[alt_cols] / C[alt_cols].shift(90)).div(C["BTCUSDT"] / C["BTCUSDT"].shift(90), axis=0) - 1
    ma50_alt = C[alt_cols] > C[alt_cols].rolling(50).mean()
    ma200_alt = C[alt_cols] > C[alt_cols].rolling(200).mean()

    rec_list = []
    for dt in C.index[C.index >= "2019-01-01"]:
        v = vol30.loc[dt].dropna()
        top50 = v[v > 0].sort_values(ascending=False).head(50).index
        if len(top50) < 15:
            continue
        r_d = alt_ret.loc[dt, top50].dropna()
        b_d = btc_ret.loc[dt]
        r30 = rel_30.loc[dt, top50].dropna()
        r90 = rel_90.loc[dt, top50].dropna()
        m50 = ma50_alt.loc[dt, top50].mean() * 100
        m200 = ma200_alt.loc[dt, top50].mean() * 100
        rec_list.append({
            "d": dt,
            "alt_vs_btc_1d": r_d.mean() - b_d if len(r_d) else 0.0,
            "beat30_pct": (r30 > 0).mean() * 100 if len(r30) else np.nan,
            "med_rel30": r30.median() * 100 if len(r30) else np.nan,
            "beat90_pct": (r90 > 0).mean() * 100 if len(r90) else np.nan,
            "med_rel90": r90.median() * 100 if len(r90) else np.nan,
            "abv_ma50_pct": m50,
            "abv_ma200_pct": m200,
        })
    adf = pd.DataFrame(rec_list).set_index("d")
    adf.index = pd.to_datetime(adf.index).tz_localize(None)
    adf["cum_log_rel"] = np.log(1 + adf["alt_vs_btc_1d"].clip(-0.5, 0.5)).cumsum()
    adf["rel_idx"] = np.exp(adf["cum_log_rel"]) * 100

    # Also query BTC.D and BTC.D ex-stables from market.snapshot.duckdb
    con = duckdb.connect(str(REPO / "data/market.snapshot.duckdb"), read_only=True)
    tv = con.execute("""
        SELECT CAST(ts AS DATE) d, symbol, close
        FROM tv_bars
        WHERE interval = '1d'
          AND symbol IN ('CRYPTOCAP:BTC.D', 'CRYPTOCAP:USDT.D', 'CRYPTOCAP:USDC.D', 'CRYPTOCAP:TOTAL', 'CRYPTOCAP:TOTAL3', 'CRYPTOCAP:OTHERS.D', 'CRYPTOCAP:ETH.D')
        ORDER BY d
    """).df()
    tvp = tv.pivot_table(index="d", columns="symbol", values="close")
    tvp.index = pd.to_datetime(tvp.index).tz_localize(None)
    tvp = tvp[tvp.index <= "2026-11-08"]
    tvp["btcd_ex"] = tvp["CRYPTOCAP:BTC.D"] / (100.0 - tvp["CRYPTOCAP:USDT.D"].fillna(0) - tvp["CRYPTOCAP:USDC.D"].fillna(0)) * 100.0

    last_a = adf.iloc[-1]
    print(f"Latest PIT Top-50 Alt Breadth ({adf.index[-1].date()}):")
    print(f"  % Alts beating BTC (30d): {last_a.beat30_pct:.1f}% | Median Alt vs BTC (30d): {last_a.med_rel30:+.1f}%")
    print(f"  % Alts beating BTC (90d): {last_a.beat90_pct:.1f}% | Median Alt vs BTC (90d): {last_a.med_rel90:+.1f}%")
    print(f"  % Alts > 50d MA: {last_a.abv_ma50_pct:.1f}% | % Alts > 200d MA: {last_a.abv_ma200_pct:.1f}%")

    # Compare 2019, 2023, 2026 post-BTC-low paths for Alts vs BTC using bn_kline_1d (covers 2018..2026) + PIT universe
    print("\n--- Post-BTC-Low Alt/BTC Path Comparison (2019 vs 2023 vs 2026, Binance Spot) ---")
    df_k = con.execute("SELECT symbol, CAST(ts AS DATE) d, close FROM bn_kline_1d WHERE market='spot'").df()
    px_k = df_k.pivot_table(index="d", columns="symbol", values="close")
    px_k.index = pd.to_datetime(px_k.index)
    btc_k = px_k.pop("BTC")
    STABLE_K = {"USDC", "FDUSD", "TUSD", "USD1", "DAI", "USDP", "BUSD", "PAXG", "XAUT", "EUR"}
    px_k = px_k[[s for s in px_k.columns if s not in STABLE_K]]

    low_anchors = [("2019 Cycle (Low 2018-12-15)", "2018-12-15"), ("2023 Cycle (Low 2022-11-21)", "2022-11-21"), ("2026 Cycle (Low 2026-07-01)", "2026-07-01")]
    for label, start_d in low_anchors:
        lo = pd.Timestamp(start_d)
        sub_k = px_k[px_k.index >= lo]
        b_k = btc_k[btc_k.index >= lo]
        have = [s for s in sub_k.columns if not np.isnan(sub_k[s].iloc[0])]
        rel_k = sub_k[have].div(b_k, axis=0)
        rel_k = rel_k / rel_k.iloc[0] * 100
        med_k = rel_k.median(axis=1)
        days_idx = (med_k.index - lo).days
        med_k.index = days_idx
        w150 = med_k[med_k.index <= 150]
        pk_day = int(w150.idxmax())
        pk_val = w150.max()
        pk_date = (lo + pd.Timedelta(days=pk_day)).date()
        cur_day = min(131, int(med_k.index.max()))
        cur_val = med_k.loc[:131].iloc[-1]
        cur_date = (lo + pd.Timedelta(days=int(med_k.loc[:131].index[-1]))).date()
        w365 = med_k[med_k.index <= 365]
        min_day = int(w365.idxmin())
        min_val = w365.min()
        min_date = (lo + pd.Timedelta(days=min_day)).date()
        # Also check day 200 and day 300 if available
        v200 = f"{med_k.loc[:200].iloc[-1]:.1f}" if med_k.index.max() >= 200 else "n/a"
        v300 = f"{med_k.loc[:300].iloc[-1]:.1f}" if med_k.index.max() >= 300 else "n/a"
        print(f"{label} (n={len(have)} coins):")
        print(f"  1st Bounce Peak (<=150d): {pk_val:.1f} at Day +{pk_day} ({pk_date})")
        print(f"  At Day +131 ({cur_date}): {cur_val:.1f} ({cur_val/pk_val-1:+.1%} from bounce peak, {cur_val-100:+.1f}% vs low day)")
        print(f"  1-Year Alt/BTC Trough:    {min_val:.1f} at Day +{min_day} ({min_date}) | Day +200: {v200} | Day +300: {v300}")

    # Check when PIT Alt Breadth & BTC.D were at similar levels in 2019..2026
    print("\n--- Historical Analog Periods for Current Altcoin Stage (PIT Top-50 Universe) ---")
    adf_tv = adf.join(tvp[["CRYPTOCAP:BTC.D", "btcd_ex", "CRYPTOCAP:OTHERS.D"]], how="left").ffill()
    adf_tv["fwd_alt_vs_btc_30"] = np.exp(adf_tv["cum_log_rel"].shift(-30) - adf_tv["cum_log_rel"]) - 1
    adf_tv["fwd_alt_vs_btc_60"] = np.exp(adf_tv["cum_log_rel"].shift(-60) - adf_tv["cum_log_rel"]) - 1
    adf_tv["fwd_alt_vs_btc_90"] = np.exp(adf_tv["cum_log_rel"].shift(-90) - adf_tv["cum_log_rel"]) - 1
    adf_tv["fwd_alt_vs_btc_180"] = np.exp(adf_tv["cum_log_rel"].shift(-180) - adf_tv["cum_log_rel"]) - 1

    # Today's PIT readings: beat30_pct ~ 18.4%, med_rel30 ~ -13.3%, abv_ma50_pct ~ 8.0%, abv_ma200_pct ~ 36.0%
    alt_cond = (
        (adf_tv["abv_ma50_pct"] <= 15)
        & (adf_tv["abv_ma200_pct"].between(20, 55))
        & (adf_tv["med_rel30"].between(-20, -8))
        & (adf_tv["beat30_pct"] <= 25)
    )
    alt_sub = adf_tv.loc["2019-01-01":"2026-08-01"][alt_cond.loc["2019-01-01":"2026-08-01"]]
    alt_ev = []
    for d in alt_sub.index:
        if not alt_ev or (d - alt_ev[-1]).days >= 30:
            alt_ev.append(d)
    print(f"Alt Post-Bounce Pullback Events (>50dMA<=15%, >200dMA 20..55%, med_rel30 -20..-8%, beat30<=25%): {len(alt_ev)} events")
    for d in alt_ev:
        r = adf_tv.loc[d]
        print(f"  {d.date()}: med_rel30={r.med_rel30:+.1f}%, beat30={r.beat30_pct:.0f}%, >50dMA={r.abv_ma50_pct:.0f}%, >200dMA={r.abv_ma200_pct:.0f}%, "
              f"BTC.D={r['CRYPTOCAP:BTC.D']:.1f}% (ex-stables {r.btcd_ex:.1f}%) "
              f"-> Alt/BTC fwd 30d={r.fwd_alt_vs_btc_30:+.1%}, 60d={r.fwd_alt_vs_btc_60:+.1%}, 90d={r.fwd_alt_vs_btc_90:+.1%}, 180d={r.fwd_alt_vs_btc_180:+.1%}")
    a_ev_df = adf_tv.loc[alt_ev]
    for h in [30, 60, 90, 180]:
        s = a_ev_df[f"fwd_alt_vs_btc_{h}"].dropna()
        print(f"  Alt/BTC Fwd {h:3d}d: beat BTC in {(s>0).mean():.0%} ({int((s>0).sum())}/{len(s)}), median={s.median():+.1%}")

if __name__ == "__main__":
    run()
