"""Entity layer step 1+2 (claude/entity-plan.md, Avalon 11-01 "需要更系统化的方法"): exchange discovery + scorecard.
Seeds (DefiLlama, GraphSense category exchange, OKX/Binance PoR) -> ATS v1 cluster roots, one canonical exchange name per
root (roots naming >= 2 exchanges or > 20M addresses dropped, as exch_cluster_labels.py). Then one pass over the raw
window (DISC_START.., default 2025-07-01) on CLUSTER level:
  out_total[c] / in_total[c]   value c sent to / received from OTHER clusters
  to_ex[c, e] / from_ex[c, e]  part of that going to / coming from exchange e's seed roots
  n_in[c]                      number of incoming transactions from other clusters
Rules (the standard ones: deposit addresses sweep into the hot wallet; cold storage only talks to its own exchange):
  deposit  : >= 90% of c's outgoing value goes to ONE exchange e, >= 0.5 BTC to it, >= 5 incoming txs, c not a seed
  internal : >= 90% of c's incoming AND >= 90% of its outgoing value is with ONE exchange e, >= 1,000 BTC each way
Output: <chain>/labels/exch_roots_v1.parquet (root, exch, kind, bal_btc, n_addr) and data/research/entity_scorecard.csv
(one row per run: per-exchange seed / +deposit / +internal balances, PoR checks). Needs the Mac (~5 GB RAM).
Lock <chain>/labels/exch_discover.lock; budget EXCH_DISC_BUDGET; resumable by month (state in <chain>/exch_disc/)."""
import csv, datetime as dt, glob, json, os, sys, time, fcntl
import duckdb, numpy as np
T0 = time.time(); BUDGET = float(os.environ.get('EXCH_DISC_BUDGET', 1500))
CH = os.environ.get("MDH_CHAIN_DIR", "/Volumes/chain/datahub/chain"); V1 = os.path.join(CH, "ats_v1"); LB = os.path.join(CH, "labels")
R = os.path.join(CH, "raw"); W = os.path.join(CH, "exch_disc"); os.makedirs(W, exist_ok=True)
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
START = dt.date.fromisoformat(os.environ.get("DISC_START", "2025-07-01")); NB = 16; MAX_ADDR = 20_000_000
def log(*x): print("exch_disc:", *x, flush=True)
lk = open(os.path.join(W, "run.lock"), "w")
try: fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError: log("another run is going; skip"); sys.exit(0)
v1 = json.load(open(os.path.join(V1, "state.json")))
if v1.get("stage") != "live": log("waiting for ats_v1"); sys.exit(0)
END = dt.date.fromisoformat(v1["flows_done"])
SF = os.path.join(W, "state.json"); st = json.load(open(SF)) if os.path.exists(SF) else {}
if st.get("done_end") == END.isoformat() and not os.path.exists(os.path.join(W, "rerun")):
    log(f"up to date ({END})"); sys.exit(0)
B = [np.load(os.path.join(V1, f"addrs_b{b:02d}.npy"), mmap_mode="r") for b in range(NB)]
OFF = np.cumsum([0] + [len(x) for x in B]); N = int(OFF[-1])
def ids(addr):
    addr = np.asarray(addr, np.int64); out = np.full(len(addr), -1, np.int64); bk = addr & (NB - 1)
    for b in range(NB):
        m = np.nonzero(bk == b)[0]
        if len(m) and len(B[b]):
            pos = np.minimum(np.searchsorted(B[b], addr[m]), len(B[b]) - 1)
            ok = B[b][pos] == addr[m]; out[m[ok]] = OFF[b] + pos[ok]
    return out
parent = np.load(os.path.join(V1, "parent.npy"), mmap_mode="r")
# ---- seeds -> roots -> exchange id
ALIAS = {"okex": "okx", "okcoin": "okx", "swisborg": "swissborg", "huobi": "htx", "binanceus": "binance", "bitfinex2": "bitfinex",
         "mexccex": "mexc", "blofincex": "blofin", "pionexcex": "pionex", "gateio": "gate", "gate.io": "gate", "coinbase.com": "coinbase",
         "kraken.com": "kraken", "bitfinex.com": "bitfinex"}
GENERIC = {"unknown", "?", "exchange", "cex", "hot", "cold", "wallet"}
NOT_EXCH = ("fbi", "seiz", "silkroad", "silk road", "government", "doj", "magiceden", "magic eden")   # 11-01: GraphSense 'exchange' tags that are not exchanges
def canon(n):
    w = (n or "?").split()[0]
    for suf in (".com", ".io", ".net", ".org", ".co", ".exchange", ".us"):   # 11-01: binance vs binance.com dropped Binance's PoR clusters
        if w.endswith(suf) and len(w) > len(suf) + 2: w = w[: -len(suf)]
    w = w[:-3] if w.endswith("cex") and len(w) > 5 else w
    return ALIAS.get(w, w)
seeds = []
for fn, cond in (("btc_cex_defillama.csv", "true"), ("btc_entity_seeds_graphsense.csv", "category = 'exchange'")):
    p = os.path.join(LB, fn)
    if os.path.exists(p):
        seeds += duckdb.sql(f"select addr::bigint, lower(label) from read_csv('{p}', header=true, all_varchar=true) where {cond}").fetchall()
for p in glob.glob(os.path.join(LB, "btc_por_*.csv")):
    seeds += duckdb.sql(f"select addr::bigint, '{os.path.basename(p)[8:-4]}' from read_csv('{p}', header=true, all_varchar=true)").fetchall()
seeds = [(a, n) for a, n in seeds if not any(x in (n or "") for x in NOT_EXCH)]
sa = np.array([a for a, _ in seeds], np.int64); nm = np.array([canon(n) for _, n in seeds], dtype=object)
k = ids(sa); ok = k >= 0; roots = np.asarray(parent[k[ok]]).astype(np.int64); nm = nm[ok]
sz_f = os.path.join(W, "size.npy")
if not os.path.exists(sz_f) or os.path.getmtime(sz_f) < os.path.getmtime(os.path.join(V1, "parent.npy")):
    np.save(sz_f, np.bincount(np.asarray(parent), minlength=N).astype(np.int32))
size = np.load(sz_f, mmap_mode="r")
byroot = {}
for r, n in zip(roots, nm):
    s_ = byroot.setdefault(int(r), set())
    if n not in GENERIC: s_.add(n)
EXN = sorted({n for s_ in byroot.values() for n in s_}); EXI = {n: i for i, n in enumerate(EXN)}
seed_ex = {}
for r, ns in byroot.items():
    if len(ns) == 1 and size[r] <= MAX_ADDR:
        seed_ex[r] = EXI[next(iter(ns))]
sr = np.array(sorted(seed_ex), np.int64); se = np.array([seed_ex[r] for r in sr], np.int32)
log(f"{len(seeds):,} seed addrs -> {len(byroot):,} roots, kept {len(sr):,} single-exchange roots of {len(EXN)} exchanges")
def seed_of(c):   # cluster ids -> exchange id or -1
    pos = np.minimum(np.searchsorted(sr, c), len(sr) - 1); hit = sr[pos] == c
    return np.where(hit, se[pos], -1)
# ---- one pass over the window (resumable per month)
acc_f = os.path.join(W, "acc.npz")
import hashlib   # seed set signature: the window pairs store exchange ids, so a new seed set means a fresh window pass
SIG = hashlib.md5(sr.tobytes() + se.tobytes() + "|".join(EXN).encode()).hexdigest()
if os.path.exists(acc_f) and st.get("start") == START.isoformat() and st.get("sig") == SIG:
    z = np.load(acc_f); out_total = z["out_total"]; in_total = z["in_total"]; n_in = z["n_in"]
    pairs_to = [z["pairs_to"]]; pairs_from = [z["pairs_from"]]
else:
    out_total = np.zeros(N, np.float32); in_total = np.zeros(N, np.float32); n_in = np.zeros(N, np.uint16)
    pairs_to, pairs_from = [], []; st = {"start": START.isoformat(), "sig": SIG}
    log("seed set changed -> new window pass")
d = dt.date.fromisoformat(st["done"]) + dt.timedelta(days=1) if st.get("done") else START
con = duckdb.connect(); con.execute("set threads=4")
def compact(lst):
    if not lst: return np.zeros((0, 3))
    p = np.concatenate(lst)
    if not len(p): return p
    key = p[:, 0].astype(np.int64) * 1000 + p[:, 1].astype(np.int64)
    uk, inv = np.unique(key, return_inverse=True); sv = np.bincount(inv, weights=p[:, 2])
    return np.stack([(uk // 1000).astype(np.float64), (uk % 1000).astype(np.float64), sv], 1)
def persist():
    pt = compact(pairs_to); pf = compact(pairs_from)
    np.savez(acc_f + ".tmp.npz", out_total=out_total, in_total=in_total, n_in=n_in, pairs_to=pt, pairs_from=pf)
    os.replace(acc_f + ".tmp.npz", acc_f); json.dump(st, open(SF, "w"))
    return [pt], [pf]
nd = 0
while d <= END and time.time() - T0 < BUDGET - 240:
    ds = d.isoformat()
    if os.path.exists(f"{R}/inputs/date={ds}.parquet"):
        i = con.execute(f"select tx, addr from '{R}/inputs/date={ds}.parquet' where addr<>0").fetchnumpy()
        o = con.execute(f"select tx, addr, sats from '{R}/outputs/date={ds}.parquet' where addr<>0").fetchnumpy()
        ic = ids(i["addr"]); m = ic >= 0
        itx = i["tx"][m]; icl = np.asarray(parent[ic[m]]).astype(np.int64)
        od_ = np.argsort(itx, kind="stable"); itx = itx[od_]; icl = icl[od_]
        utx, first = np.unique(itx, return_index=True); scl = icl[first]          # sender cluster per tx
        oc = ids(o["addr"]); m2 = oc >= 0
        otx = o["tx"][m2]; ocl = np.asarray(parent[oc[m2]]).astype(np.int64); v = o["sats"][m2].astype(np.float64) / 1e8
        pos = np.searchsorted(utx, otx); pos = np.minimum(pos, len(utx) - 1) if len(utx) else pos
        hit = (len(utx) > 0) & (utx[pos] == otx) if len(utx) else np.zeros(len(otx), bool)
        s = np.where(hit, scl[pos] if len(utx) else 0, -1)
        ext = hit & (s != ocl)
        s, r_, v = s[ext], ocl[ext], v[ext]
        np.add.at(out_total, s, v.astype(np.float32)); np.add.at(in_total, r_, v.astype(np.float32))
        # incoming tx count (one per tx and receiver)
        tr = np.unique(np.stack([otx[m2][ext] if False else otx[ext], r_]).T, axis=0) if len(r_) else np.zeros((0, 2), np.int64)
        if len(tr):
            cnt = np.bincount(np.searchsorted(np.unique(tr[:, 1]), tr[:, 1])); ur = np.unique(tr[:, 1])
            n_in[ur] = np.minimum(n_in[ur].astype(np.int64) + cnt, 65535).astype(np.uint16)
        es = seed_of(r_); m3 = (es >= 0) & (seed_of(s) < 0)
        if m3.any(): pairs_to.append(np.stack([s[m3], es[m3], v[m3]], 1))      # non-seed -> exchange
        ef = seed_of(s); m4 = (ef >= 0) & (seed_of(r_) < 0)
        if m4.any(): pairs_from.append(np.stack([r_[m4], ef[m4], v[m4]], 1))     # exchange -> non-seed
    st["done"] = ds; d += dt.timedelta(days=1); nd += 1
    if nd % 30 == 0:
        pairs_to, pairs_from = persist(); log(f"window to {ds}")
pairs_to, pairs_from = persist(); log(f"window to {st.get('done')}")
if d <= END: sys.exit(0)
# ---- classify
def agg(p):
    if not len(p): return {}
    c = p[:, 0].astype(np.int64); e = p[:, 1].astype(np.int64); v = p[:, 2]
    key = c * 1000 + e; uk, inv = np.unique(key, return_inverse=True); sv = np.bincount(inv, weights=v)
    return dict(zip(uk.tolist(), sv.tolist()))
TO = agg(pairs_to[0]); FR = agg(pairs_from[0])
bal = np.load(os.path.join(V1, "bal.npy"), mmap_mode="r")
rows = []
for r, e in seed_ex.items(): rows.append((r, EXN[e], "seed"))
best_to = {}
for key, v in TO.items():
    c, e = divmod(key, 1000)
    if v > best_to.get(c, (0, -1))[0]: best_to[c] = (v, e)
for c, (v, e) in best_to.items():
    ot = float(out_total[c])
    if ot > 0 and v / ot >= 0.9 and v >= 0.5 and n_in[c] >= 5 and float(bal[c]) < max(1.0, 0.05 * v):   # deposit addresses get swept empty
        rows.append((c, EXN[e], "deposit"))
seen = {r for r, _, _ in rows}
for key, v in FR.items():
    c, e = divmod(key, 1000)
    if c in seen: continue
    it, ot = float(in_total[c]), float(out_total[c]); back = TO.get(c * 1000 + e, 0.0)
    if it > 0 and ot > 0 and v / it >= 0.9 and back / ot >= 0.9 and v >= 1000 and back >= 1000:
        rows.append((c, EXN[e], "internal"))
import pyarrow as pa, pyarrow.parquet as pq
rr = np.array([r for r, _, _ in rows], np.int64)
bb = np.asarray(bal[rr]) if len(rr) else np.zeros(0)
na = np.asarray(size[rr]) if len(rr) else np.zeros(0)
pq.write_table(pa.table({"root": rr, "exch": [x for _, x, _ in rows], "kind": [x for _, _, x in rows], "bal_btc": bb, "n_addr": na}),
               os.path.join(LB, "exch_roots_v1.parquet"))
# ---- scorecard
import pandas as pd
t = pd.DataFrame({"exch": [x for _, x, _ in rows], "kind": [x for _, _, x in rows], "bal": np.maximum(bb, 0)})
pv = t.pivot_table(index="exch", columns="kind", values="bal", aggfunc="sum", fill_value=0)
for kcol in ("seed", "deposit", "internal"):
    if kcol not in pv: pv[kcol] = 0.0
pv["total"] = pv[["seed", "deposit", "internal"]].sum(1); pv = pv.sort_values("total", ascending=False)
cnt = t.groupby(["kind"]).size().to_dict()
log(f"roots: {cnt}; balance seed {pv.seed.sum():,.0f} + deposit {pv.deposit.sum():,.0f} + internal {pv.internal.sum():,.0f} = {pv.total.sum():,.0f} BTC")
for ex, r_ in pv.head(15).iterrows():
    log(f"  {ex:12s} seed {r_.seed:>10,.0f}  deposit {r_.deposit:>9,.0f}  internal {r_.internal:>10,.0f}  total {r_.total:>10,.0f}")
sc = os.path.join(REPO, "data", "research", "entity_scorecard.csv"); new = not os.path.exists(sc)
with open(sc, "a", newline="") as f:
    w = csv.writer(f)
    if new: w.writerow(["run_utc", "balances_as_of", "window_start", "exch", "seed", "deposit", "internal", "total"])
    now = dt.datetime.utcnow().isoformat()[:16]
    for ex, r_ in pv.iterrows():
        w.writerow([now, END.isoformat(), START.isoformat(), ex, round(r_.seed), round(r_.deposit), round(r_.internal), round(r_.total)])
st["done_end"] = END.isoformat(); json.dump(st, open(SF, "w"))
if os.path.exists(os.path.join(W, "rerun")): os.remove(os.path.join(W, "rerun"))
log("done")
