"""Coin-age (cohort) metrics from the spend link (scripts/chain/spend_link.py). Added 2026-10-31; replaces BGeometrics
cdd, lth-sopr, hodl-waves-supply, true-market-mean (and cross-checks sth-sopr / realized price).
Inputs: <chain>/derived/link/{created/*.parquet, agg_<C>/*.parquet, inc.parquet, inc_created.parquet}, prices
(daily avg (o+h+l+c)/4, same as the URPD), self/miner_daily.csv (thermocap).
Per UTC day d (day index from 2009-01-03), with spent[c, s] = BTC created on day c and spent on day s:
  supply, CDD = sum spent[c,d]*(d-c), liveliness = cum CDD / cum coin-days created (cum of supply),
  realized cap = sum unspent[c]*price[c], LTH/STH supply (age >= / < 155 d), LTH-SOPR / STH-SOPR (age >= 1 d),
  True Market Mean = (realized cap - thermocap) / (supply * liveliness)   (Glassnode cointime: investor cap / active supply),
  HODL waves = unspent supply by age band (BGeometrics bands).
Output: data/onchain/self/cohort_daily.csv"""
import csv, datetime as dt, glob, json, os
import duckdb, numpy as np
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CH = os.environ.get("MDH_CHAIN_DIR", "/Volumes/chain/datahub/chain"); L = os.path.join(CH, "derived", "link")
URPD_PX = os.environ.get("URPD_PRICE", os.path.expanduser("~/Projects/btc-volatility/urpd/price.csv"))
OUT = os.path.join(REPO, "data", "onchain", "self"); G = dt.date(2009, 1, 3)
def log(*x): print("cohort:", *x, flush=True)
bj = os.path.join(L, "base.json")
if not os.path.exists(bj): log("no spend-link base yet"); raise SystemExit(0)
base = json.load(open(bj)); C = base["cutoff"]; CT = C[:7]
last = base.get("inc_to", C)
N = (dt.date.fromisoformat(last) - G).days + 1
con = duckdb.connect()
cr = np.zeros(N)
for f in [f for f in glob.glob(os.path.join(L, "created", "m=*.parquet")) if os.path.basename(f)[2:9] <= CT] + \
         ([os.path.join(L, "inc_created.parquet")] if "inc_to" in base and os.path.exists(os.path.join(L, "inc_created.parquet")) else []):
    for c, b in con.execute(f"select cd, btc from read_parquet('{f}')").fetchall():
        if c < N: cr[c] += b
srcs = [os.path.join(L, f"agg_{CT}", "*.parquet")] + ([os.path.join(L, "inc.parquet")] if "inc_to" in base else [])
rows = np.array(con.execute("select cd, sd, sum(btc) from read_parquet([%s]) group by 1, 2" % ",".join("'%s'" % s for s in srcs)).fetchall())
sp = np.zeros((N, N), dtype=np.float64)
c_, s_, v_ = rows[:, 0].astype(int), rows[:, 1].astype(int), rows[:, 2]
ok = (c_ < N) & (s_ < N)
np.add.at(sp, (c_[ok], s_[ok]), v_[ok])
# prices by day index
con.execute(f"attach '{os.path.join(REPO, 'data', 'market.duckdb')}' as m (read_only)")
px = np.zeros(N)
if os.path.exists(URPD_PX):
    for d, p in con.execute(f"select d::date, price from read_csv('{URPD_PX}', header=true)").fetchall():
        i = (d - G).days
        if 0 <= i < N: px[i] = p
for d, p in con.execute("select ts::date, (open+high+low+close)/4 from m.bn_kline_1d where symbol='BTC' and market='spot'").fetchall():
    i = (d - G).days
    if 0 <= i < N: px[i] = p
therm = np.zeros(N)
mf = os.path.join(OUT, "miner_daily.csv")
if os.path.exists(mf):
    for r in csv.DictReader(open(mf)):
        i = (dt.date.fromisoformat(r["d"]) - G).days
        if 0 <= i < N: therm[i] = float(r["thermocap_usd"])
    therm = np.maximum.accumulate(therm)
# blocks mined per UTC day -> cumulative height at end of day (Cointime counts coin-BLOCKS, not coin-days; Glassnode docs:
# "multiplying the value of each coin ... by the number of blocks the coin was unspent")
nb = np.zeros(N)
if os.path.exists(mf):
    for r in csv.DictReader(open(mf)):
        i = (dt.date.fromisoformat(r["d"]) - G).days
        if 0 <= i < N: nb[i] = float(r["blocks"])
H = np.cumsum(nb)
BANDS = [("age_0d_1d", 0, 1), ("age_1d_1w", 1, 7), ("age_1w_1m", 7, 30), ("age_1m_3m", 30, 90), ("age_3m_6m", 90, 180),
         ("age_6m_1y", 180, 365), ("age_1y_2y", 365, 730), ("age_2y_3y", 730, 1095), ("age_3y_4y", 1095, 1460),
         ("age_4y_5y", 1460, 1825), ("age_5y_7y", 1825, 2555), ("age_7y_10y", 2555, 3650), ("age_10y", 3650, 10 ** 6)]
# 11-01 extra (Avalon): cost basis per coin-age band + old coins moving (5y+/7y+/10y+) -> cohort_extra.csv
XB = [("1d_1w", 1, 7), ("1w_1m", 7, 30), ("1m_3m", 30, 90), ("3m_6m", 90, 180), ("6m_1y", 180, 365), ("1y_2y", 365, 730),
      ("2y_3y", 730, 1095), ("3y_5y", 1095, 1825), ("5y_10y", 1825, 3650), ("10y", 3650, 10 ** 6)]
xout = []
rem = np.zeros(N); out = []; cum_cdd = cum_cdc = cum_cbd = cum_cbc = 0.0; prev_sup = 0.0
ages_all = np.arange(N)
for d in range(N):
    rem[d] += cr[d]
    col = sp[:, d]
    rem -= col
    age = d - ages_all[: d + 1]
    sup = rem[: d + 1].sum()
    cdd = float((col[: d + 1] * age).sum())
    cbd = float((col[: d + 1] * (H[d] - H[: d + 1])).sum())        # coinblocks destroyed today
    cum_cdd += cdd; cum_cdc += prev_sup
    cum_cbd += cbd; cum_cbc += prev_sup * nb[d]                      # coinblocks created today
    prev_sup = sup
    # liveliness in coin-DAYS: matches BGeometrics better (TMM -1.6..-2.4%) than Glassnode's documented coin-BLOCKS
    # (-2.2..-2.9%); both computed, block version kept in the output column liveliness_blocks for reference (10/31 check)
    live = cum_cdd / cum_cdc if cum_cdc > 0 else 0.0
    live_b = cum_cbd / cum_cbc if cum_cbc > 0 else 0.0
    rc = float((rem[: d + 1] * px[: d + 1]).sum())
    lth_mask = age >= 155; sth_mask = (age >= 1) & ~lth_mask
    lth_sup = float(rem[: d + 1][lth_mask].sum())
    def sopr(mask):
        v = col[: d + 1][mask]; den = float((v * px[: d + 1][mask]).sum())
        return float(v.sum() * px[d] / den) if den > 0 and px[d] > 0 else None
    tmm = (rc - therm[d]) / (sup * live) if sup > 0 and live > 0 and px[d] > 0 else None
    cs = np.concatenate([[0.0], np.cumsum(rem[: d + 1][::-1])])   # cs[k] = supply with age < k
    bands = [float(cs[min(hi, d + 1)] - cs[min(lo, d + 1)]) for _, lo, hi in BANDS]
    if px[d] > 0:
        cv = np.concatenate([[0.0], np.cumsum((rem[: d + 1] * px[: d + 1])[::-1])])
        rps = []
        for _, lo, hi in XB:
            a_, b_ = min(lo, d + 1), min(hi, d + 1); q_ = cs[b_] - cs[a_]
            rps.append(round(float((cv[b_] - cv[a_]) / q_), 2) if q_ > 1 else "")
        ag = age; cv_ = col[: d + 1]
        olds = [round(float(cv_[ag >= k].sum()), 2) for k in (1825, 2555, 3650)]
        xout.append([(G + dt.timedelta(days=d)).isoformat()] + rps + olds)
        out.append([(G + dt.timedelta(days=d)).isoformat(), round(sup, 2), round(cdd), round(live, 6), round(live_b, 6), round(rc), round(lth_sup, 2),
                    round(sup - lth_sup, 2), sopr(lth_mask), sopr(sth_mask), round(tmm, 2) if tmm else ""] + [round(x, 2) for x in bands])
with open(os.path.join(OUT, "cohort_daily.csv"), "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["d", "supply", "cdd", "liveliness", "liveliness_blocks", "realized_cap", "lth_supply", "sth_supply", "lth_sopr", "sth_sopr", "tmm"] + [b[0] for b in BANDS])
    for r in out:
        w.writerow([("" if x is None else (round(x, 5) if isinstance(x, float) and i in (8, 9) else x)) for i, x in enumerate(r)])  # cols 8, 9 = SOPRs
with open(os.path.join(OUT, "cohort_extra.csv"), "w", newline="") as f:
    w = csv.writer(f); w.writerow(["d"] + ["rp_" + b[0] for b in XB] + ["spent_5y", "spent_7y", "spent_10y"]); w.writerows(xout)
log(f"{out[0][0]}..{out[-1][0]}; last supply {out[-1][1]:,.0f}, CDD {out[-1][2]:,}, liveliness {out[-1][3]}, TMM {out[-1][10]}, LTH-SOPR {out[-1][8]}")
