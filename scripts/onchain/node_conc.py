"""BTC supply concentration (near5, Murphy/Glassnode definition) from Avalon's own node URPD -> hourly.
near5 = share of BTC supply with cost (creation-day price) within price x [0.95, 1.05]; each 1% log bin
[e^(0.01k), e^(0.01(k+1))) counted pro rata to its overlap with that window. Reproduces the 2026-10-27
exact node series (data/outputs/node_near5.json near_avg) to 0.000 pt. NOT conc.py's bins b-5..b+4 (off up to 2.2 pt).
URPD = base snapshot end of 2026-10-06 (BigQuery, data/onchain/node_urpd/base_urpd_end_2026-10-06.csv)
       + node deltas (data/onchain/node_urpd/deltas.csv) for days >= 2026-10-07 (complete UTC days only).
Before 2026-10-07 the daily series is the static file data/onchain/node_urpd/near5_bigquery_2026-06-01_to_10-06.csv
(same method, computed once from btc-volatility/urpd/deltas.parquet + price.csv on 2026-10-27).
Outputs:
  data/onchain/node_conc_daily.csv  d, avg_price, close, near5_avg, near5_close, near5_avg_7d, source
  data/onchain/node_conc_live.json  live near5 (latest Binance 1h price on the last complete day's URPD),
                                    standard v2 (Murphy 7d-avg turn) status, price->near5 ladder.
Runs from scripts/ops/run_update.sh right after node_urpd.py. Python 3.9, stdlib + duckdb (read-only)."""
import csv, datetime as dt, json, math, os, sys
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ND = os.path.join(REPO, "data", "onchain", "node_urpd")
OUT_CSV = os.path.join(REPO, "data", "onchain", "node_conc_daily.csv")
OUT_JSON = os.path.join(REPO, "data", "onchain", "node_conc_live.json")
OLD_CSV = os.path.join(ND, "near5_bigquery_2026-06-01_to_10-06.csv")
SWITCH = "2026-10-07"
def log(*a): print("node_conc:", *a, flush=True)

def near5(urpd, supply, p):
    lo, hi = 0.95 * p, 1.05 * p; t = 0.0
    for k in range(int(math.floor(math.log(lo) / 0.01)), int(math.floor(math.log(hi) / 0.01)) + 1):
        v = urpd.get(k)
        if not v: continue
        a, b = math.exp(k * 0.01), math.exp((k + 1) * 0.01)
        o = min(b, hi) - max(a, lo)
        if o > 0: t += v * o / (b - a)
    return 100.0 * t / supply

# ---- URPD base + node deltas by day ----
base = {}
for r in csv.DictReader(open(os.path.join(ND, "base_urpd_end_2026-10-06.csv"))):
    base[int(r["bin"])] = float(r["btc"])
seen = {}
DF = os.path.join(ND, "deltas.csv")
if not os.path.exists(DF): log("no deltas.csv yet; skip"); sys.exit(0)
for r in csv.DictReader(open(DF)):
    if r["day"] < SWITCH: continue
    seen[(r["h_end"], r["day"], int(r["bin"]))] = float(r["delta"])   # dedupe on (h_end, day, bin)
byday = {}
for (_, d, b), v in seen.items():
    byday.setdefault(d, {}); byday[d][b] = byday[d].get(b, 0.0) + v
# last complete day = last day fully processed by node_urpd (state.json last_height's day; deltas only hold complete days)
days = sorted(byday)
if not days: log("no node days >= %s yet; skip" % SWITCH); sys.exit(0)

# ---- prices ----
import duckdb
con = duckdb.connect(os.path.join(REPO, "data", "market.duckdb"), read_only=True)
px = {ts.strftime("%Y-%m-%d"): (float(a), float(c)) for ts, a, c in con.execute(
    "select ts, (open+high+low+close)/4, close from bn_kline_1d where symbol='BTC' and market='spot' and ts >= '2026-10-07'").fetchall()}
lts, lp = con.execute("select ts, close from bn_kline_1h where symbol='BTC' and market='spot' order by ts desc limit 1").fetchone()
con.close()

# ---- daily series ----
rows = []
if os.path.exists(OLD_CSV):
    for r in csv.DictReader(open(OLD_CSV)):
        if r["d"] < SWITCH:
            rows.append({"d": r["d"], "avg_price": float(r["avg_price"]), "close": "", "near5_avg": float(r["near5_avg"]), "near5_close": "", "source": "bigquery"})
urpd = dict(base)
for d in days:
    for b, v in byday[d].items(): urpd[b] = urpd.get(b, 0.0) + v
    if d not in px: log("no price for", d); continue
    a, c = px[d]; sup = sum(urpd.values())
    rows.append({"d": d, "avg_price": round(a, 2), "close": round(c, 2), "near5_avg": round(near5(urpd, sup, a), 3), "near5_close": round(near5(urpd, sup, c), 3), "source": "node"})
supply = sum(urpd.values()); last_day = days[-1]
for i, r in enumerate(rows):
    w = [x["near5_avg"] for x in rows[max(0, i - 6): i + 1]]
    r["near5_avg_7d"] = round(sum(w) / len(w), 3) if len(w) == 7 else ""
with open(OUT_CSV, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["d", "avg_price", "close", "near5_avg", "near5_close", "near5_avg_7d", "source"]); w.writeheader(); w.writerows(rows)

# ---- concentration standard v2 (2026-10-27, replaces the retired Q1): Murphy-style turn on the 7-day average ----
# episode starts when the 7d avg >= 13; track its running max; TURN = 7d avg <= running max - 0.4 pt (fires once per max,
# re-armed by a new high; episode ends when the 7d avg falls below 13 after a turn). Direction = price max-day -> turn day.
# Real-time (no future data). Backtest 2017..2026-10: 27/34 right 30 days later (79%), since 2024 10/16; baseline 53%.
ma = [r["near5_avg_7d"] for r in rows]
mx = None; mxi = None; fired = False; last_turn = None
for i, v in enumerate(ma):
    if v == "": continue
    if v < 13 and (mx is None or fired): mx = None; fired = False; continue
    if mx is None:
        if v >= 13: mx, mxi, fired = v, i, False
        continue
    if v > mx: mx, mxi, fired = v, i, False
    elif not fired and v <= mx - 0.4:
        fired = True
        last_turn = {"day": rows[i]["d"], "peak_day": rows[mxi]["d"], "peak_ma7": mx, "dir": "up" if rows[i]["avg_price"] > rows[mxi]["avg_price"] else "down"}
std = {"rule": "7d avg falls 0.4 pt from its running max (episode starts at 7d avg >= 13%)", "last_turn": last_turn}
if mx is not None:
    std.update({"peak_day": rows[mxi]["d"], "peak_ma7": mx, "peak_avg_price": rows[mxi]["avg_price"], "turn_line": round(mx - 0.4, 3),
                "status": "turned" if fired else "not turned"})
else:
    std.update({"status": "no episode (7d avg < 13%)"})

# ---- live ----
live = round(near5(urpd, supply, lp), 3)
prev6 = [r["near5_avg"] for r in rows[-6:]]
live_ma7 = round((sum(prev6) + live) / 7, 3)   # last 6 complete days + live value
ladder = {str(k): round(near5(urpd, supply, k), 2) for k in range(int(lp // 1000 * 1000) - 6000, int(lp // 1000 * 1000) + 9000, 1000)}
pt = dt.timezone(dt.timedelta(hours=-7))  # PDT (until 2026-11-01)
out = {"computed_utc": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
       "live_price": round(lp, 2), "live_price_hour_utc": lts.strftime("%Y-%m-%d %H:00"),
       "live_price_hour_pt": (lts.replace(tzinfo=dt.timezone.utc) + dt.timedelta(hours=1)).astimezone(pt).strftime("%m/%d %H:%M PT (close of hour)"),
       "urpd_as_of_day": last_day, "supply_btc": round(supply, 2),
       "near5_live": live, "ma7_live": live_ma7, "latest_daily": rows[-1], "standard_v2": std,
       "live_ma7_vs_turn_line": (None if "turn_line" not in std else ("below" if live_ma7 <= std["turn_line"] else "above")),
       "ladder_price_to_near5": ladder,
       "note": "live = latest Binance 1h close on the URPD of the last complete UTC day; daily values use (o+h+l+c)/4"}
json.dump(out, open(OUT_JSON, "w"), indent=1)
log("urpd %s, daily %s %.2f%% (7d %s), live %.2f%% / live 7d %.2f%% @ %.0f; v2 peak %s %s line %s -> %s" % (
    last_day, rows[-1]["d"], rows[-1]["near5_avg"], rows[-1]["near5_avg_7d"], live, live_ma7, lp,
    std.get("peak_day"), std.get("peak_ma7"), std.get("turn_line"), std["status"]))
