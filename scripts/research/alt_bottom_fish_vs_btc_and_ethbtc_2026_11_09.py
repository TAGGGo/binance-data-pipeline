"""Backtest:
1) When altcoins have dropped heavily vs BTC (like now), is bottom-fishing alts better than holding BTC?
2) When does rotating from BTC into alts actually have positive expected value (high win rate)?
3) ETH/BTC ratio: 2026-06-06 bottom -> 2026-09-12 peak -> current retracement & historical analogs.

Question: Avalon (2026-11-09):
  "山寨币跌这么多了 现在抄底不是性价比很高吗？还是继续拿着比特币好呢？
   以及ETH汇率 今年6月还是啥时候就走到底部了 现在回吐了多少？还在上升趋势吗 你怎么看"
Data:
  - data/universe/spot_usdt_1d_spliced.csv.gz (Survivorship-free PIT Binance USDT spot universe)
  - data/market.snapshot.duckdb (bn_kline_1d, read-only)
  - data/onchain/self/urpd_metrics_daily.csv (BTC PSIP, MVRV)
Date: 2026-11-09
Outputs:
  - data/outputs/alt_bottom_fish_vs_btc_2026-11-09.txt
"""
from pathlib import Path
import duckdb
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]

def run():
    u = pd.read_csv(REPO / "data/universe/spot_usdt_1d_spliced.csv.gz", parse_dates=["t"])
    C = u.pivot_table(index="t", columns="symbol", values="c")
    Q = u.pivot_table(index="t", columns="symbol", values="qv")
    C.index = pd.to_datetime(C.index).tz_localize(None)
    Q.index = pd.to_datetime(Q.index).tz_localize(None)

    STABLE_SYMS = {s for s in C.columns if any(s.startswith(x) for x in [
        "USDC", "FDUSD", "TUSD", "BUSD", "USDP", "DAI", "PAXG", "EUR", "WBTC", "UST", "USDE", "USD1"
    ])}
    alt_cols = [s for s in C.columns if s != "BTCUSDT" and s not in STABLE_SYMS]
    vol30 = Q[alt_cols].rolling(30, min_periods=15).mean()

    btc = C["BTCUSDT"]
    rel30 = (C[alt_cols] / C[alt_cols].shift(30)).div(btc / btc.shift(30), axis=0) - 1
    dd90_usd = C[alt_cols] / C[alt_cols].rolling(90, min_periods=30).max() - 1
    ma50 = C[alt_cols] > C[alt_cols].rolling(50).mean()
    ma200 = C[alt_cols] > C[alt_cols].rolling(200).mean()

    # Compute forward 30d, 60d, 90d returns for:
    # (1) Holding BTC
    # (2) Equal-weight Top-50 Alts
    # (3) Bottom-fishing the 10 "most crashed" alts in Top-50 (worst 30d relative return)
    # (4) Buying the 10 "most resilient" alts in Top-50 (best 30d relative return)
    rows = []
    dates = C.index[(C.index >= "2019-07-01") & (C.index <= "2026-11-08")]
    for i, dt in enumerate(dates):
        v = vol30.loc[dt].dropna()
        top50 = v[v > 0].sort_values(ascending=False).head(50).index
        if len(top50) < 25:
            continue
        r30 = rel30.loc[dt, top50].dropna()
        d90 = dd90_usd.loc[dt, top50].dropna()
        if len(r30) < 20:
            continue
        worst10 = r30.sort_values().head(10).index
        best10 = r30.sort_values(ascending=False).head(10).index

        rec = {
            "d": dt,
            "med_rel30": r30.median() * 100,
            "med_dd90": d90.median() * 100,
            "beat30_pct": (r30 > 0).mean() * 100,
            "abv_ma50": ma50.loc[dt, top50].mean() * 100,
            "abv_ma200": ma200.loc[dt, top50].mean() * 100,
        }
        for h in (30, 60, 90):
            t_fut = dt + pd.Timedelta(days=h)
            if t_fut not in C.index:
                continue
            b_f = btc.loc[t_fut] / btc.loc[dt] - 1
            # For alts in top50, if delisted/missing at t_fut, use last available price up to t_fut
            sub_f = C.loc[dt:t_fut, top50].ffill().iloc[-1] / C.loc[dt, top50] - 1
            rec[f"btc_{h}"] = b_f * 100
            rec[f"top50_{h}"] = sub_f.mean() * 100
            rec[f"worst10_{h}"] = sub_f[worst10].mean() * 100
            rec[f"best10_{h}"] = sub_f[best10].mean() * 100
            rec[f"top50_vs_btc_{h}"] = ((1 + sub_f.mean()) / (1 + b_f) - 1) * 100
            rec[f"worst10_vs_btc_{h}"] = ((1 + sub_f[worst10].mean()) / (1 + b_f) - 1) * 100
            rec[f"best10_vs_btc_{h}"] = ((1 + sub_f[best10].mean()) / (1 + b_f) - 1) * 100
        rows.append(rec)

    df = pd.DataFrame(rows).set_index("d")
    now = df.iloc[-1]
    print("=== 1. TODAY'S ALTCOIN VALUATION / CRASH DEPTH (2026-11-08) ===")
    print(f"Top-50 Alts median 90d USD drawdown: {now.med_dd90:.1f}% | 30d vs BTC median: {now.med_rel30:+.1f}%")
    print(f"% beating BTC (30d): {now.beat30_pct:.1f}% | % > 50d MA: {now.abv_ma50:.1f}% | % > 200d MA: {now.abv_ma200:.1f}%")

    def eval_regime(name, mask, gap=30):
        sub = df.loc[:"2026-08-01"][mask.loc[:"2026-08-01"]]
        ev = []
        for d in sub.index:
            if not ev or (d - ev[-1]).days >= gap:
                ev.append(d)
        e = df.loc[ev]
        print(f"\n--- {name} (Declustered events={len(e)}, total days={len(sub)}) ---")
        for h in (30, 60, 90):
            s_top = e[f"top50_vs_btc_{h}"].dropna()
            s_wst = e[f"worst10_vs_btc_{h}"].dropna()
            s_bst = e[f"best10_vs_btc_{h}"].dropna()
            s_btc = e[f"btc_{h}"].dropna()
            print(f"  Horizon {h:2d}d (n={len(s_top)}):")
            print(f"    Hold BTC abs return       : win={(s_btc>0).mean():.0%}, median={s_btc.median():+.1f}%")
            print(f"    Buy Top-50 Alts vs BTC    : beat BTC={(s_top>0).mean():.0%} ({int((s_top>0).sum())}/{len(s_top)}), median excess={s_top.median():+.1f}% (abs median={e[f'top50_{h}'].median():+.1f}%)")
            print(f"    Buy 10 Most-Crashed vs BTC: beat BTC={(s_wst>0).mean():.0%} ({int((s_wst>0).sum())}/{len(s_wst)}), median excess={s_wst.median():+.1f}%")
            print(f"    Buy 10 Strongest vs BTC   : beat BTC={(s_bst>0).mean():.0%} ({int((s_bst>0).sum())}/{len(s_bst)}), median excess={s_bst.median():+.1f}%")

    # Test 3 regimes:
    # Regime 1: Current state ("Alts dropped a lot recently, >50dMA<=15%, med_rel30 <= -10%, but >200dMA still >= 20% — mid-bleed")
    r1 = (df["abv_ma50"] <= 15) & (df["med_rel30"] <= -10) & (df["abv_ma200"] >= 20)
    eval_regime("REGIME 1 (NOW: Mid-Bleed — >50dMA<=15%, med_rel30<=-10%, but >200dMA>=20%)", r1)

    # Regime 2: True Extreme Capitulation ("Total washout — >50dMA<=10%, >200dMA<=10%, med_dd90 <= -45%")
    r2 = (df["abv_ma50"] <= 10) & (df["abv_ma200"] <= 10) & (df["med_dd90"] <= -45)
    eval_regime("REGIME 2 (True Extreme Capitulation — >50dMA<=10% AND >200dMA<=10% AND 90d DD<=-45%)", r2)

    # Regime 3: Trend Reclaim ("Right-side entry — after >60d with >50dMA<30%, alts reclaim >50dMA >= 55% & beat30 >= 50%")
    low_60 = (df["abv_ma50"] < 30).rolling(60).mean().shift(1) >= 0.75
    r3 = low_60 & (df["abv_ma50"] >= 55) & (df["beat30_pct"] >= 50)
    eval_regime("REGIME 3 (Right-Side Rotation — Reclaim >50dMA>=55% & beat30>=50% after >=60d drought)", r3)

    # Part 2: ETH/BTC detailed check
    con = duckdb.connect(str(REPO / "data/market.snapshot.duckdb"), read_only=True)
    k = con.execute("""
        SELECT CAST(ts AS DATE) d, symbol, close, high, low
        FROM bn_kline_1d
        WHERE market='spot' AND symbol IN ('BTC', 'ETH') AND ts >= '2018-01-01'
        ORDER BY d
    """).df()
    p = k.pivot_table(index="d", columns="symbol", values="close")
    p.index = pd.to_datetime(p.index)
    p["r"] = p["ETH"] / p["BTC"]
    p["ma50"] = p["r"].rolling(50).mean()
    p["ma200"] = p["r"].rolling(200).mean()

    print("\n=== 2. ETH/BTC RATIO STRUCTURE (2025-2026) ===")
    s26 = p.loc["2026-01-01":]
    lo26_d = s26["r"].idxmin()
    lo26_v = s26.loc[lo26_d, "r"]
    s_post = p.loc[lo26_d:]
    hi26_d = s_post["r"].idxmax()
    hi26_v = s_post.loc[hi26_d, "r"]
    cur_d = p.index[-1]
    cur_v = p["r"].iloc[-1]
    retrace_26 = (hi26_v - cur_v) / (hi26_v - lo26_v) * 100
    print(f"2026 Low: {lo26_d.date()} at {lo26_v:.5f} (ETH={p.loc[lo26_d,'ETH']:.0f}, BTC={p.loc[lo26_d,'BTC']:.0f})")
    print(f"2026 Rally Peak: {hi26_d.date()} at {hi26_v:.5f} (+{(hi26_v/lo26_v-1)*100:.1f}% from June low)")
    print(f"Current ({cur_d.date()}): {cur_v:.5f} ({(cur_v/hi26_v-1)*100:+.1f}% from Sept peak, {(cur_v/lo26_v-1)*100:+.1f}% above June low)")
    print(f"Share of 2026-06..2026-09 rally given back (retraced): {retrace_26:.1f}%")
    print(f"Distance to 200d MA ({p['ma200'].iloc[-1]:.5f}): {(cur_v/p['ma200'].iloc[-1]-1)*100:+.1f}% | Distance to 50d MA ({p['ma50'].iloc[-1]:.5f}): {(cur_v/p['ma50'].iloc[-1]-1)*100:+.1f}%")

if __name__ == "__main__":
    run()
