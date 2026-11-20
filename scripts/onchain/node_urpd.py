"""Exact URPD deltas from the local Bitcoin Core node (pruned OK) -> data/onchain/node_urpd/deltas.csv
Same method as btc-volatility/urpd/q.sql (BigQuery): every output +BTC on its creation day, -BTC on its spend day,
in the 1% log bin of the CREATION-day price (daily (o+h+l+c)/4, Binance spot; urpd/price.csv before 2026-10-07).
Runs natively on macOS from scripts/ops/run_update.sh (Claude's VM cannot reach the node). Read-only RPC; never changes the node.
Resumable: state.json keeps the last processed height; each run stops after NODE_URPD_BUDGET_S seconds (default 1200).
Rows: h_end,day,bin,delta  (h_end = last block of the chunk; dedupe on (h_end,day,bin) when reading)."""
import array, base64, csv, datetime as dt, json, math, os, sys, time, urllib.request
T0 = time.time(); BUDGET = float(os.environ.get("NODE_URPD_BUDGET_S", "1200"))
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUTD = os.path.join(REPO, "data", "onchain", "node_urpd"); os.makedirs(OUTD, exist_ok=True)
DATADIR = os.environ.get("BTC_DATADIR", os.path.expanduser("~/Library/Application Support/Bitcoin"))
START_DAY = os.environ.get("NODE_URPD_START", "2026-10-01")   # overlap with BigQuery deltas (to 2026-10-07) for reconciliation
EPOCH = dt.date(1970, 1, 1)
def log(*a): print("node_urpd:", *a, flush=True)
try:
    user, pw = open(os.path.join(DATADIR, ".cookie")).read().strip().split(":", 1)
except FileNotFoundError:
    log("no .cookie: Bitcoin Core not running (or server=1 missing); skip"); sys.exit(0)
AUTH = "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()
def rpc(calls):
    body = json.dumps([{"jsonrpc": "1.0", "id": i, "method": m, "params": p} for i, (m, p) in enumerate(calls)]).encode()
    req = urllib.request.Request("http://127.0.0.1:8332/", data=body, headers={"Authorization": AUTH, "Content-Type": "application/json"})
    out = sorted(json.load(urllib.request.urlopen(req, timeout=300)), key=lambda r: r["id"])
    for r in out:
        if r.get("error"): raise RuntimeError(r["error"])
    return [r["result"] for r in out]
info = rpc([("getblockchaininfo", [])])[0]
if info.get("initialblockdownload"):
    log(f"node still syncing ({info['blocks']}/{info['headers']}, progress {info['verificationprogress']:.4f}); skip"); sys.exit(0)
tip = info["blocks"]; pruneh = info.get("pruneheight", 0)
# ---- prices: day index -> price ----
px = {}
for r in csv.DictReader(open(os.path.join(REPO, "..", "..", "btc-volatility", "urpd", "price.csv"))):
    if r["d"] < "2026-10-07": px[r["d"]] = float(r["price"])
try:
    import duckdb
    con = duckdb.connect(os.path.join(REPO, "data", "market.duckdb"), read_only=True)
    for ts, p in con.execute("select ts, (open+high+low+close)/4 from bn_kline_1d where symbol='BTC' and market='spot' and ts >= '2026-10-07'").fetchall():
        px[ts.strftime("%Y-%m-%d")] = float(p)
    con.close()
except Exception as e:
    log("price load from duckdb failed:", e)
TODAY = dt.datetime.utcnow().date().isoformat()   # only complete UTC days (today's candle is partial)
def binof(day):
    if day >= TODAY: return None
    p = px.get(day)
    if p is None: return None
    return math.floor(math.log(max(p, 0.0008)) / 0.01)
# ---- height -> day map (all heights; headers are never pruned) ----
HMAP = os.path.join(OUTD, "height_day.u32"); hd = array.array("I")
if os.path.exists(HMAP): hd.frombytes(open(HMAP, "rb").read())
n0 = len(hd)
while len(hd) <= tip and time.time() - T0 < BUDGET * 0.5:
    hs = list(range(len(hd), min(tip + 1, len(hd) + 2000)))
    hashes = rpc([("getblockhash", [h]) for h in hs])
    heads = rpc([("getblockheader", [x]) for x in hashes])
    hd.extend(int(hh["time"] // 86400) for hh in heads)
open(HMAP, "wb").write(hd.tobytes())
if len(hd) <= tip: log(f"height map {len(hd)}/{tip+1} (+{len(hd)-n0}); continue next run"); sys.exit(0)
dayof = lambda h: (EPOCH + dt.timedelta(days=hd[h])).isoformat()
# ---- state / start height ----
SF = os.path.join(OUTD, "state.json")
st = json.load(open(SF)) if os.path.exists(SF) else {}
if "last_height" not in st:
    sd = (dt.date.fromisoformat(START_DAY) - EPOCH).days
    first = next(h for h in range(len(hd)) if hd[h] >= sd and all(hd[k] >= sd for k in range(h, min(h + 12, len(hd)))))
    st = {"start_height": first, "start_day": START_DAY, "last_height": first - 1}
    log(f"start at height {first} ({dayof(first)}); node pruneheight {pruneh}")
nxt = st["last_height"] + 1
if nxt < pruneh:
    log(f"ERROR: needed block {nxt} is pruned (pruneheight {pruneh}); raise prune or reindex"); sys.exit(1)
# ---- process blocks ----
DF = os.path.join(OUTD, "deltas.csv"); new = not os.path.exists(DF)
f = open(DF, "a", newline=""); w = csv.writer(f)
if new: w.writerow(["h_end", "day", "bin", "delta"])
done = 0; missing_px = set()
while nxt <= tip and time.time() - T0 < BUDGET:
    chunk = list(range(nxt, min(tip + 1, nxt + 20)))
    hashes = rpc([("getblockhash", [h]) for h in chunk]); acc = {}
    for h, bh in zip(chunk, hashes):
        b = rpc([("getblock", [bh, 3])])[0]; d = dayof(h); bo = binof(d)
        if bo is None: missing_px.add(d); break
        for tx in b["tx"]:
            for o in tx["vout"]:
                v = o["value"]
                if v > 0: acc[(d, bo)] = acc.get((d, bo), 0.0) + v
            for i in tx["vin"]:
                po = i.get("prevout")
                if not po: continue          # coinbase
                v = po["value"]
                if v <= 0: continue
                cd = dayof(po["height"]); cb = binof(cd)
                if cb is None: missing_px.add(cd); continue
                acc[(d, cb)] = acc.get((d, cb), 0.0) - v
        else:
            continue
        break
    if missing_px: break
    for (d, b_), v in sorted(acc.items()):
        if abs(v) > 1e-9: w.writerow([chunk[-1], d, b_, f"{v:.8f}"])
    f.flush(); st["last_height"] = chunk[-1]; json.dump(st, open(SF, "w")); nxt = chunk[-1] + 1; done += len(chunk)
f.close()
st["tip_at_run"] = tip; st["run_utc"] = dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M"); json.dump(st, open(SF, "w"))
msg = f"processed {done} blocks, last {st['last_height']} ({dayof(st['last_height'])}), tip {tip}, {time.time()-T0:.0f}s"
if missing_px: msg += f"; waiting for price of {sorted(missing_px)[:3]}"
log(msg)
