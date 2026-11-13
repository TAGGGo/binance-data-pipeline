"""
Compare all dashboard indicators between May 31, 2026 ($73,674 pre-breakdown to $58k)
and Nov 9/10, 2026 ($73,354 current).
Outputs to data/outputs/may_vs_nov_2026_all_indicators.txt.
"""
import os
import json
import duckdb
import pandas as pd
import numpy as np

ROOT = "/Users/ivantang/Projects/datahub/binance-data-pipeline"
DB_PATH = os.path.join(ROOT, "data", "market.snapshot.duckdb")
ONCHAIN_DIR = os.path.join(ROOT, "data", "onchain", "self")
OUT_PATH = os.path.join(ROOT, "data", "outputs", "may_vs_nov_2026_all_indicators.txt")

con = duckdb.connect(DB_PATH, read_only=True)
lines = []

def log(s=""):
    print(s)
    lines.append(s)

log("=========================================================================")
log("FULL INDICATOR COMPARISON: 2026-05-31 ($73,674) vs 2026-11-09/10 ($73,354)")
log("=========================================================================")

# 1. Price, MAs, Spot & Perp CVD
btc = con.execute("""
    SELECT ts::DATE as d, open, high, low, close, quote_volume, (2*taker_buy_quote - quote_volume) as spot_cvd
    FROM bn_kline_1d WHERE symbol = 'BTC' AND market = 'spot' AND ts >= '2025-01-01' ORDER BY d
""").df()
btc["d"] = btc["d"].astype(str)
btc["ma50"] = btc["close"].rolling(50).mean()
btc["ma100"] = btc["close"].rolling(100).mean()
btc["ma200"] = btc["close"].rolling(200).mean()
btc["cvd30"] = btc["spot_cvd"].rolling(30).sum() / 1e8
btc["cvd7"] = btc["spot_cvd"].rolling(7).sum() / 1e8
btc = btc.set_index("d")

perp = con.execute("""
    SELECT ts::DATE as d, (2*taker_buy_quote - quote_volume) as perp_cvd
    FROM bn_kline_1d WHERE symbol = 'BTC' AND market = 'perp' AND ts >= '2025-01-01' ORDER BY d
""").df()
perp["d"] = perp["d"].astype(str)
perp["pcvd30"] = perp["perp_cvd"].rolling(30).sum() / 1e8
perp["pcvd7"] = perp["perp_cvd"].rolling(7).sum() / 1e8
perp = perp.set_index("d")

log("\n--- 1. PRICE, MOVING AVERAGES & CVD ---")
for d in ["2026-05-31", "2026-11-09", "2026-11-10"]:
    r = btc.loc[btc.index[btc.index <= d][-1]]
    p = perp.loc[perp.index[perp.index <= d][-1]]
    c, m50, m100, m200 = r["close"], r["ma50"], r["ma100"], r["ma200"]
    c30, c7, p30, p7 = r["cvd30"], r["cvd7"], p["pcvd30"], p["pcvd7"]
    log(f"[BTC Spot & Perp] {d}: Close=${c:,.0f} | MA50=${m50:,.0f} | MA100=${m100:,.0f} | MA200=${m200:,.0f} | SpotCVD_30d={c30:+.2f}亿 (7d={c7:+.2f}亿) | PerpCVD_30d={p30:+.2f}亿 (7d={p7:+.2f}亿)")

# 2. ETF Flows (5d and 20d)
log("\n--- 2. SPOT ETF FLOWS & STABLECOINS ---")
etf = con.execute("SELECT date::DATE as d, asset, net_inflow_usd FROM v_etf_flows WHERE date >= '2026-01-01' ORDER BY d").df()
etf["d"] = etf["d"].astype(str)
for asset in ["BTC", "ETH"]:
    sub = etf[etf["asset"] == asset].set_index("d")
    sub["f5"] = sub["net_inflow_usd"].rolling(5).sum() / 1e8
    sub["f20"] = sub["net_inflow_usd"].rolling(20).sum() / 1e8
    for target in ["2026-05-31", "2026-11-09"]:
        idx = sub.index[sub.index <= target][-1]
        r = sub.loc[idx]
        f1, f5, f20 = r["net_inflow_usd"]/1e8, r["f5"], r["f20"]
        log(f"[{asset} ETF] {idx} (target {target}): 1d={f1:+.2f}亿 | 5d={f5:+.2f}亿 | 20d(1m)={f20:+.2f}亿")

st = con.execute("SELECT date::DATE as d, SUM(circulating_usd)/1e8 as tot_yi FROM stablecoin_supply GROUP BY 1 ORDER BY 1").df()
st["d"] = st["d"].astype(str)
st["chg30"] = st["tot_yi"] - st["tot_yi"].shift(30)
st = st.set_index("d")
for target in ["2026-05-31", "2026-11-09"]:
    idx = st.index[st.index <= target][-1]
    tot, chg = st.loc[idx, "tot_yi"], st.loc[idx, "chg30"]
    log(f"[Stablecoins] {idx}: Total={tot:,.1f}亿 | 30d Chg={chg:+.1f}亿")

# 3. Derivatives & Sentiment
log("\n--- 3. DERIVATIVES, PREMIUM & SENTIMENT ---")
oi = con.execute("SELECT date::DATE as d, SUM(oi_usd)/1e8 as oi_yi FROM v_oi_daily_by_venue WHERE symbol='BTC' GROUP BY 1 ORDER BY 1").df()
oi["d"] = oi["d"].astype(str)
oi["chg30_pct"] = (oi["oi_yi"] / oi["oi_yi"].shift(30) - 1) * 100
oi = oi.set_index("d")
for target in ["2026-05-31", "2026-11-09"]:
    idx = oi.index[oi.index <= target][-1]
    v, chg = oi.loc[idx, "oi_yi"], oi.loc[idx, "chg30_pct"]
    log(f"[BTC OI] {idx}: Total={v:.1f}亿 | 30d Chg={chg:+.1f}%")

fd = con.execute("SELECT date::DATE as d, AVG(funding_8h_bp)*3*365/100 as ann_pct FROM v_funding_daily WHERE symbol='BTC' GROUP BY 1 ORDER BY 1").df()
fd["d"] = fd["d"].astype(str)
fd["ann7"] = fd["ann_pct"].rolling(7).mean()
fd = fd.set_index("d")
for target in ["2026-05-31", "2026-11-09"]:
    idx = fd.index[fd.index <= target][-1]
    a1, a7 = fd.loc[idx, "ann_pct"], fd.loc[idx, "ann7"]
    log(f"[BTC Funding] {idx}: 1d={a1:+.1f}% | 7d Avg={a7:+.1f}%")

cbp = con.execute("SELECT date::DATE as d, premium_bp/100.0 as pct FROM v_coinbase_premium_1d WHERE symbol='BTC' ORDER BY 1").df()
cbp["d"] = cbp["d"].astype(str)
cbp["pct7"] = cbp["pct"].rolling(7).mean()
cbp = cbp.set_index("d")
for target in ["2026-05-31", "2026-11-09"]:
    idx = cbp.index[cbp.index <= target][-1]
    p1, p7 = cbp.loc[idx, "pct"], cbp.loc[idx, "pct7"]
    log(f"[Coinbase Premium] {idx}: 1d={p1:+.3f}% | 7d Avg={p7:+.3f}%")

fg = con.execute("SELECT date::DATE as d, value FROM fear_greed ORDER BY 1").df()
fg["d"] = fg["d"].astype(str)
fg["m30"] = fg["value"].shift(30)
fg = fg.set_index("d")
for target in ["2026-05-31", "2026-11-09"]:
    idx = fg.index[fg.index <= target][-1]
    v, m30 = fg.loc[idx, "value"], fg.loc[idx, "m30"]
    log(f"[Crypto Fear & Greed] {idx}: Value={v} (30d ago={m30})")

dv = con.execute("SELECT * FROM deribit_dvol WHERE currency='BTC'").df()
dcol = [c for c in dv.columns if c in ("date", "ts", "time")][0]
dv["d"] = dv[dcol].astype(str).str[:10]
dv = dv.sort_values("d").groupby("d").last()
for target in ["2026-05-31", "2026-11-09"]:
    idx = dv.index[dv.index <= target][-1]
    v = dv.loc[idx, "close"]
    log(f"[BTC DVOL] {idx}: {v:.1f}")

# 4. On-Chain Self CSVs
log("\n--- 4. ON-CHAIN VALUATION, COST BASIS, WHALES & BEHAVIOUR ---")
urpd_m = pd.read_csv(os.path.join(ONCHAIN_DIR, "urpd_metrics_daily.csv")).set_index("d")
urpd_m["sec_pct"] = urpd_m["seller_exh"].rank(pct=True) * 100
for target in ["2026-05-31", "2026-11-09"]:
    r = urpd_m.loc[target]
    rp, mvrv, psip, v30, se, spct = r["realized_price"], r["mvrv"], r["psip"], r["vol30"], r["seller_exh"]*100, r["sec_pct"]
    log(f"[URPD Metrics] {target}: RealizedPrice=${rp:,.0f} | MVRV={mvrv:.2f} | PSIP={psip:.1f}% | Vol30={v30:.3f} | SellerExh={se:.3f} (HistPct={spct:.1f}%)")

sth = pd.read_csv(os.path.join(ONCHAIN_DIR, "sth_daily.csv")).set_index("d")
for target in ["2026-05-31", "2026-11-08"]:
    r = sth.loc[target]
    sup, srp, smvrv, ssopr = r["sth_supply_btc"]/1e4, r["sth_rp"], r["sth_mvrv"], r["sth_sopr"]
    log(f"[STH Metrics] {target}: STH_Supply={sup:.1f}万枚 | STH_RP=${srp:,.0f} | STH_MVRV={smvrv:.3f} | STH_SOPR={ssopr:.4f}")

coh = pd.read_csv(os.path.join(ONCHAIN_DIR, "cohort_daily.csv")).set_index("d")
coh["lth_pct"] = coh["lth_supply"] / coh["supply"] * 100
coh["lth_pct_m30"] = coh["lth_pct"].shift(30)
for target in ["2026-05-31", "2026-11-09"]:
    idx = coh.index[coh.index <= target][-1]
    r = coh.loc[idx]
    tmm, lp, lpm30, lsopr, ssopr = r["tmm"], r["lth_pct"], r["lth_pct_m30"], r["lth_sopr"], r["sth_sopr"]
    log(f"[Cohort & TMM] {idx}: TrueMarketMean=${tmm:,.0f} | LTH_Share={lp:.1f}% (30d ago={lpm30:.1f}%) | LTH_SOPR={lsopr:.3f} | STH_SOPR={ssopr:.3f}")

ea_sup = pd.read_csv(os.path.join(ONCHAIN_DIR, "ea_supply_n.csv")).set_index("d")
ea_sup["whale_chg30"] = ea_sup["whale_bal"] - ea_sup["whale_bal"].shift(30)
ea_sup["w1_10_chg30"] = ea_sup["whale1k10k_bal"] - ea_sup["whale1k10k_bal"].shift(30)
ea_sup["w10_chg30"] = ea_sup["whale10k_bal"] - ea_sup["whale10k_bal"].shift(30)
for target in ["2026-05-31", "2026-11-09"]:
    idx = ea_sup.index[ea_sup.index <= target][-1]
    r = ea_sup.loc[idx]
    c5, wn, wb, wc30, w1c, w10c = r["conc5_excb"]*100, r["whale_n"], r["whale_bal"]/1e4, r["whale_chg30"]/1e4, r["w1_10_chg30"]/1e4, r["w10_chg30"]/1e4
    log(f"[Entity Whales & Conc] {idx}: Conc5={c5:.2f}% | Whales={wn:.0f} ({wb:.1f}万枚, 30d={wc30:+.2f}万枚: 1k-10k={w1c:+.2f}万, 10k+={w10c:+.2f}万)")

ats = pd.read_csv(os.path.join(ONCHAIN_DIR, "ea_ats30_n.csv")).set_index("d")
ats["score"] = ats["gn_buy_excb"] / (ats["gn_buy_excb"] + ats["gn_sell_excb"])
ats["score_m30"] = ats["score"].shift(30)
for target in ["2026-05-31", "2026-11-09"]:
    idx = ats.index[ats.index <= target][-1]
    r = ats.loc[idx]
    sc, scm30 = r["score"], r["score_m30"]
    log(f"[ATS Score] {idx}: Score={sc:.2f} (30d ago={scm30:.2f})")

reg = pd.read_csv(os.path.join(ONCHAIN_DIR, "region_daily.csv")).set_index("d")
reg["us30"] = reg["us"].rolling(30).sum()
reg["eu30"] = reg["eu"].rolling(30).sum()
reg["asia30"] = reg["asia"].rolling(30).sum()
for target in ["2026-05-31", "2026-11-09"]:
    idx = reg.index[reg.index <= target][-1]
    r = reg.loc[idx]
    u30, e30, a30 = r["us30"], r["eu30"], r["asia30"]
    log(f"[Session 30d Delta] {idx}: US={u30:+,.0f} | EU={e30:+,.0f} | Asia={a30:+,.0f}")

exch = pd.read_csv(os.path.join(ONCHAIN_DIR, "ea_exch_daily_n.csv")).set_index("d")
exch_cols = [c for c in exch.columns if c != "coinbase_pool"]
exch["tot"] = exch[exch_cols].sum(axis=1) / 1e4
exch["chg30"] = exch["tot"] - exch["tot"].shift(30)
for target in ["2026-05-31", "2026-11-09"]:
    idx = exch.index[exch.index <= target][-1]
    r = exch.loc[idx]
    t, c30 = r["tot"], r["chg30"]
    log(f"[Exchange Reserves] {idx}: Total={t:.1f}万枚 | 30d Chg={c30:+.2f}万枚")

act = pd.read_csv(os.path.join(ONCHAIN_DIR, "ea2_active.csv")).set_index("d")
act["ma7"] = act["n_ent"].rolling(7).mean() / 1e4
act["chg30_pct"] = (act["ma7"] / act["ma7"].shift(30) - 1) * 100
for target in ["2026-05-31", "2026-11-09"]:
    idx = act.index[act.index <= target][-1]
    r = act.loc[idx]
    m7, c30 = r["ma7"], r["chg30_pct"]
    log(f"[Active Entities 7d] {idx}: {m7:.2f}万/day (30d Chg={c30:+.1f}%)")

# 5. Macro & Equities
log("\n--- 5. MACRO, LIQUIDITY & EQUITIES ---")
for tk in ["QQQ", "SPY", "GOOG", "NVDA"]:
    eq = con.execute(f"SELECT date::DATE as d, close FROM equity_daily WHERE ticker='{tk}' ORDER BY 1").df()
    eq["d"] = eq["d"].astype(str)
    eq["chg21"] = (eq["close"] / eq["close"].shift(21) - 1) * 100
    eq["dd_ytd"] = (eq["close"] / eq["close"].cummax() - 1) * 100
    eq = eq.set_index("d")
    for target in ["2026-05-31", "2026-11-09"]:
        idx = eq.index[eq.index <= target][-1]
        r = eq.loc[idx]
        c, c21, dd = r["close"], r["chg21"], r["dd_ytd"]
        log(f"[Equity {tk}] {idx}: Close=${c:,.2f} | 1m Chg={c21:+.1f}% | Drawdown from High={dd:+.1f}%")

nl = con.execute("SELECT * FROM v_net_liquidity ORDER BY 1").df()
dcol = nl.columns[0]
nl[dcol] = nl[dcol].astype(str)
nl = nl.set_index(dcol)
for target in ["2026-05-31", "2026-11-09"]:
    idx = nl.index[nl.index <= target][-1]
    log(f"[Net Liquidity] {idx}: {nl.loc[idx].to_dict()}")

mac = con.execute("SELECT * FROM v_macro_daily ORDER BY 1").df()
dcol = mac.columns[0]
mac[dcol] = mac[dcol].astype(str)
mac = mac.set_index(dcol)
for target in ["2026-05-31", "2026-11-09"]:
    idx = mac.index[mac.index <= target][-1]
    log(f"[Macro Daily] {idx}: {mac.loc[idx].to_dict()}")

for s in ["DGS10", "DGS2", "DGS30"]:
    df = con.execute(f"SELECT date::DATE as d, value FROM fred_series WHERE series_id='{s}' AND value IS NOT NULL ORDER BY 1").df()
    df["d"] = df["d"].astype(str)
    df = df.set_index("d")
    for t in ["2026-05-31", "2026-11-09"]:
        idx = df.index[df.index <= t][-1]
        v = df.loc[idx, "value"]
        log(f"[FRED {s}] {idx}: {v:.2f}%")

vix = con.execute("SELECT date::DATE as d, close FROM vix_daily ORDER BY 1").df()
vix["d"] = vix["d"].astype(str)
vix = vix.set_index("d")
for t in ["2026-05-31", "2026-11-09"]:
    idx = vix.index[vix.index <= t][-1]
    v = vix.loc[idx, "close"]
    log(f"[VIX] {idx}: {v:.2f}")

cnn = con.execute("SELECT date::DATE as d, score FROM cnn_fear_greed ORDER BY 1").df()
cnn["d"] = cnn["d"].astype(str)
cnn = cnn.set_index("d")
for t in ["2026-05-31", "2026-11-09"]:
    idx = cnn.index[cnn.index <= t][-1]
    v = cnn.loc[idx, "score"]
    log(f"[US Equity Fear & Greed] {idx}: {v:.0f}")

with open(OUT_PATH, "w") as f:
    f.write("\n".join(lines) + "\n")
print("Saved to", OUT_PATH)
