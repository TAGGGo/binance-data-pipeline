"""Expand exchange seed labels to whole exchange entities with the multi-input clusters of ATS v1 (added 2026-10-31).
Glassnode's public exchange-data notice: exchange labels come from verified/PoR addresses, public tags and clustering
heuristics; balances are lower bounds. We do the same with what is public:
  seeds   = DefiLlama bitcoin-book CEX + GraphSense tagpacks category 'exchange' (+ labels/btc_por_*.csv if added)
  cluster = ATS v1 union-find over the multi-input heuristic (ats_v1/parent.npy, addrs_bXX.npy; CoinJoin-like tx skipped)
Every address whose cluster root holds a seed becomes an exchange address, EXCEPT roots whose seeds name >= 2 different
exchanges (likely a supercluster merge) or roots with > MAX_ADDR addresses. Waits until ATS v1 clustering reached today.
Output: <chain>/labels/btc_exch_cluster_v1.parquet (addr) + a log of the biggest roots. entity_balances.py picks it up."""
import csv, datetime as dt, glob, json, os
import duckdb, numpy as np
CH = os.environ.get("MDH_CHAIN_DIR", "/Volumes/chain/datahub/chain"); O = os.path.join(CH, "ats_v1"); LB = os.path.join(CH, "labels")
MAX_ADDR = int(os.environ.get("EXCH_MAX_ADDR", 20_000_000)); NB = 16
def log(*x): print("exch_cluster:", *x, flush=True)
sf = os.path.join(O, "state.json")
if not os.path.exists(sf): log("no ATS v1 yet"); raise SystemExit(0)
st = json.load(open(sf))
if st.get("stage") in (None, "wait", "index", "cluster"):
    log(f"waiting for ATS v1 clustering (stage {st.get('stage')}, cluster_done {st.get('cluster_done')})"); raise SystemExit(0)
outf = os.path.join(LB, "btc_exch_cluster_v1.parquet")
pf = os.path.join(O, "parent.npy")
if os.path.exists(outf) and os.path.getmtime(outf) > os.path.getmtime(pf):
    log("up to date"); raise SystemExit(0)
B = [np.load(os.path.join(O, f"addrs_b{b:02d}.npy"), mmap_mode="r") for b in range(NB)]
OFF = np.cumsum([0] + [len(x) for x in B]); N = int(OFF[-1])
def ids(addr):
    addr = np.asarray(addr, np.int64); out = np.full(len(addr), -1, np.int64); bk = addr & (NB - 1)
    for b in range(NB):
        m = np.nonzero(bk == b)[0]
        if len(m) and len(B[b]):
            pos = np.minimum(np.searchsorted(B[b], addr[m]), len(B[b]) - 1)
            ok = B[b][pos] == addr[m]; out[m[ok]] = OFF[b] + pos[ok]
    return out
seeds = []
for fn, cond, lab in (("btc_cex_defillama.csv", "true", "label"), ("btc_entity_seeds_graphsense.csv", "category = 'exchange'", "label")):
    p = os.path.join(LB, fn)
    if os.path.exists(p):
        seeds += duckdb.sql(f"select addr::bigint, lower(regexp_replace({lab}, '[^A-Za-z0-9]+', ' ', 'g')) from read_csv('{p}', header=true, all_varchar=true) where {cond}").fetchall()
for p in glob.glob(os.path.join(LB, "btc_por_*.csv")):
    seeds += duckdb.sql(f"select addr::bigint, '{os.path.basename(p)[8:-4]}' from read_csv('{p}', header=true, all_varchar=true)").fetchall()
# one name per exchange: aliases / renames / typos in the public tags must not look like two exchanges (10/31 run dropped
# the whole OKX cluster because of okex vs okx); generic tags ('unknown') carry no exchange identity
ALIAS = {"okex": "okx", "okcoin": "okx", "swisborg": "swissborg", "huobi": "htx", "binanceus": "binance", "bitfinex2": "bitfinex",
         "mexccex": "mexc", "blofincex": "blofin", "pionexcex": "pionex", "bitkub": "bitkub", "gateio": "gate", "gate.io": "gate"}
GENERIC = {"unknown", "?", "exchange", "cex", "hot", "cold", "wallet"}
def canon(n):
    w = (n or "?").split()[0]
    w = w[:-3] if w.endswith("cex") and len(w) > 5 else w
    return ALIAS.get(w, w)
sa = np.array([a for a, _ in seeds], np.int64); names = [canon(n) for _, n in seeds]
parent = np.load(pf, mmap_mode="r")
k = ids(sa); ok = k >= 0
roots = np.asarray(parent[k[ok]]); nm = np.array(names, dtype=object)[ok]
log(f"{len(seeds):,} seed addresses, {ok.sum():,} in the ATS universe, {len(np.unique(roots)):,} roots")
size = np.bincount(np.asarray(parent), minlength=N)
byroot = {}
for r, n in zip(roots, nm):
    s_ = byroot.setdefault(int(r), set())
    if n not in GENERIC: s_.add(n)
keep, drop = [], []
for r, ns in byroot.items():
    if size[r] > MAX_ADDR or len(ns) >= 2 and size[r] > 1000:
        drop.append((int(size[r]), r, sorted(ns)[:5]))
    else:
        keep.append(r)
for sz, r, ns in sorted(drop, reverse=True)[:10]:
    log(f"dropped root {r}: {sz:,} addresses, seeds of {ns}")
mask = np.zeros(N, bool); mask[np.array(keep, np.int64)] = True
sel = np.nonzero(mask[np.asarray(parent)])[0]
parts = []
for b in range(NB):   # per bucket, no 2.8 GB concatenation
    lo, hi = np.searchsorted(sel, OFF[b]), np.searchsorted(sel, OFF[b + 1])
    if hi > lo: parts.append(np.asarray(B[b])[sel[lo:hi] - OFF[b]])
addrs = np.concatenate(parts) if parts else np.zeros(0, np.int64)
top = sorted(((int(size[r]), sorted(byroot[r])[:3]) for r in keep), reverse=True)[:10]
for sz, ns in top: log(f"kept cluster {sz:,} addresses: {ns}")
import pyarrow as pa, pyarrow.parquet as pq
pq.write_table(pa.table({"addr": addrs.astype(np.int64)}), outf + ".tmp")
os.replace(outf + ".tmp", outf)
log(f"wrote {len(addrs):,} exchange addresses ({len(keep):,} clusters kept, {len(drop):,} dropped) -> {outf}")
