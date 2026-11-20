"""Self-computed on-chain metrics from the URPD (replaces BGeometrics for these; added 2026-10-31, Avalon: paid plan ends,
free plan withholds the newest 7 days). Same URPD as node_conc.py / node_quantiles.py:
  history = btc-volatility/urpd/deltas.parquet (d, bin, delta; BigQuery, to 2026-10-06), from 2026-10-07 node deltas.csv.
  Bin k = cost price [e^(0.01k), e^(0.01(k+1))) = BTC price on the coin's creation day ((o+h+l+c)/4). Raw UTXO, no entity adj.
Per complete UTC day (2017-01-01 on):
  realized_price = sum(btc x bin mid price) / supply      mvrv = close / realized_price
  psip = % of supply with cost < close (partial bin interpolated in log space)      (psip_avg uses the day's avg price)
  seller_exh = psip(fraction) x stdev of 30 daily log returns (Glassnode/our 10-28 definition)
Live (newest Binance 1h close on the last complete URPD): same metrics -> self/urpd_metrics_live.json
Output: data/onchain/self/urpd_metrics_daily.csv. Needs duckdb (reads parquet + market.duckdb read-only)."""
import csv, json, math, os, datetime as dt
import duckdb
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ON = os.path.join(REPO, "data", "onchain"); ND = os.path.join(ON, "node_urpd"); OUTD = os.path.join(ON, "self")
os.makedirs(OUTD, exist_ok=True)
HIST = os.environ.get("URPD_HIST", os.path.expanduser("~/Projects/btc-volatility/urpd/deltas.parquet"))
if not os.path.exists(HIST):
    HIST = os.path.join(os.path.dirname(REPO), "..", "btc-volatility", "urpd", "deltas.parquet")
SWITCH = "2026-10-07"; START = os.environ.get("SELF_START", "2010-08-01")  # first months with a market price
def log(*a): print("self_urpd:", *a, flush=True)

con = duckdb.connect()
hist = con.execute(f"select d::varchar, bin, sum(delta)::double from read_parquet('{HIST}') where d < '{SWITCH}' group by 1,2 order by 1").fetchall()
byday = {}
for d, b, v in hist:
    byday.setdefault(d, []).append((int(b), v))
seen = {}
for r in csv.DictReader(open(os.path.join(ND, "deltas.csv"))):
    if r["day"] >= SWITCH:
        seen[(r["h_end"], r["day"], int(r["bin"]))] = float(r["delta"])
nd = {}
for (_, d, b), v in seen.items():
    nd.setdefault(d, {}); nd[d][b] = nd[d].get(b, 0.0) + v
for d, m in nd.items():
    byday[d] = list(m.items())
m = duckdb.connect(os.path.join(REPO, "data", "market.duckdb"), read_only=True)
px = {str(ts)[:10]: (float(a), float(c)) for ts, a, c in m.execute(
    "select ts, (open+high+low+close)/4, close from bn_kline_1d where symbol='BTC' and market='spot'").fetchall()}
lts, lp = m.execute("select ts, close from bn_kline_1h where symbol='BTC' and market='spot' order by ts desc limit 1").fetchone()
m.close()
# before Binance (2017-08-17) use the URPD's own price.csv (avg) as both avg and close
pcsv = os.path.join(os.path.dirname(HIST), "price.csv")
if os.path.exists(pcsv):
    for r in csv.DictReader(open(pcsv)):
        if r["d"] not in px and float(r["price"]) > 0:
            px[r["d"]] = (float(r["price"]), float(r["price"]))

def stats(u, p):
    sup = rc = below = 0.0
    lp_ = math.log(p) / 0.01
    for k, v in u.items():
        if v <= 0: continue
        sup += v; rc += v * math.exp((k + 0.5) * 0.01)
        if k + 1 <= lp_: below += v
        elif k < lp_: below += v * (lp_ - k)
    return sup, rc / sup, 100.0 * below / sup

urpd = {}; rows = []; rets = []; prev_c = None
for d in sorted(byday):
    for b, v in byday[d]:
        urpd[b] = urpd.get(b, 0.0) + v
    if d < START or d not in px:
        continue
    a, c = px[d]
    sup, rp, ps = stats(urpd, c)
    ps_avg = stats(urpd, a)[2]
    if prev_c: rets.append(math.log(c / prev_c))
    prev_c = c
    vol = (sum((x - sum(rets[-30:]) / len(rets[-30:])) ** 2 for x in rets[-30:]) / (len(rets[-30:]) - 1)) ** 0.5 if len(rets) >= 30 else None
    rows.append({"d": d, "close": round(c, 2), "supply": round(sup, 2), "realized_price": round(rp, 2), "mvrv": round(c / rp, 4),
                 "psip": round(ps, 3), "psip_avg": round(ps_avg, 3), "vol30": round(vol, 5) if vol else "",
                 "seller_exh": round(ps / 100 * vol, 5) if vol else ""})
with open(os.path.join(OUTD, "urpd_metrics_daily.csv"), "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
sup, rp, ps = stats(urpd, lp)
r30 = rets[-29:] + [math.log(lp / prev_c)]
mu = sum(r30) / len(r30); vol = (sum((x - mu) ** 2 for x in r30) / (len(r30) - 1)) ** 0.5
live = {"computed_utc": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M"), "price": round(lp, 2), "price_hour_utc": str(lts)[:16],
        "urpd_as_of_day": rows[-1]["d"], "realized_price": round(rp, 2), "mvrv": round(lp / rp, 4), "psip": round(ps, 3),
        "seller_exh": round(ps / 100 * vol, 5), "vol30": round(vol, 5)}
json.dump(live, open(os.path.join(OUTD, "urpd_metrics_live.json"), "w"), indent=1)
log("daily %s..%s (%d rows); last %s rp %.0f mvrv %.3f psip %.1f%% sexh %s; live %.0f psip %.1f%% sexh %.4f" % (
    rows[0]["d"], rows[-1]["d"], len(rows), rows[-1]["d"], rows[-1]["realized_price"], rows[-1]["mvrv"], rows[-1]["psip"], rows[-1]["seller_exh"], lp, ps, live["seller_exh"]))
