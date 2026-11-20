"""Look up public seed addresses / txids (data/research/coinbase_btc_seeds.csv) in the ats_v1 clusters: root, root balance,
address count, existing exchange label. Txids: inputs/outputs read from raw (date window from the source date). Read-only."""
import os, glob, hashlib, datetime as dt, numpy as np, pandas as pd, duckdb, time
T0 = time.time(); CH = os.environ.get("MDH_CHAIN_DIR", "/Volumes/chain/datahub/chain"); V1 = f"{CH}/ats_v1"; R = f"{CH}/raw"
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SEED = os.environ.get("SEED_CSV", f"{REPO}/data/research/coinbase_btc_seeds.csv")
def akey(a): return int.from_bytes(hashlib.blake2b(a.encode(), digest_size=8).digest(), 'little', signed=True)
def tkey(h): return np.array([int(h[:16], 16)], np.uint64).view(np.int64)[0]
NB = 16; B = [np.load(f"{V1}/addrs_b{b:02d}.npy", mmap_mode="r") for b in range(NB)]; OFF = np.cumsum([0] + [len(x) for x in B])
def ids(addr):
    addr = np.asarray(addr, np.int64); out = np.full(len(addr), -1, np.int64); bk = addr & (NB - 1)
    for b in range(NB):
        m = np.nonzero(bk == b)[0]
        if len(m) and len(B[b]):
            pos = np.minimum(np.searchsorted(B[b], addr[m]), len(B[b]) - 1); ok = B[b][pos] == addr[m]; out[m[ok]] = OFF[b] + pos[ok]
    return out
parent = np.load(f"{V1}/parent.npy", mmap_mode="r"); bal = np.load(f"{V1}/bal.npy", mmap_mode="r"); N = len(parent)
S = pd.read_csv(SEED); rows = []
for _, s in S.iterrows():
    if s.kind == "address": rows.append(dict(item=s["item"], side="seed", akey=akey(s["item"]), sats=np.nan, label=s.label))
    else:
        d0 = dt.date.fromisoformat(str(s.source_date)); tk = tkey(s["item"])
        days = [d0 - dt.timedelta(days=k) for k in range(0, 4)] if d0.year < 2026 else [dt.date(2026, 6, 1) + dt.timedelta(days=k) for k in range(46)]
        files = [f"{R}/{side}/date={d}.parquet" for d in days for side in ("inputs", "outputs")]
        for f in files:
            if not os.path.exists(f): continue
            for a, v in duckdb.sql(f"select addr, sats from '{f}' where tx = {tk}").fetchall():
                rows.append(dict(item=s["item"], side=("in" if "/inputs/" in f else "out") + ":" + os.path.basename(f)[5:15], akey=a, sats=v, label=s.label))
T = pd.DataFrame(rows); T["idx"] = ids(T.akey.values); T["root"] = [int(parent[i]) if i >= 0 else -1 for i in T.idx]
roots = np.unique(T.root[T.root >= 0].values); rb = {}; rn = {}
for a in range(0, N, 20_000_000):
    p = np.asarray(parent[a:a+20_000_000]); m = np.isin(p, roots)
    if m.any():
        g = pd.DataFrame({"r": p[m], "b": np.asarray(bal[a:a+20_000_000])[m]}).groupby("r").b.agg(["sum", "size"])
        for r, x in g.iterrows(): rb[r] = rb.get(r, 0) + x["sum"]; rn[r] = rn.get(r, 0) + x["size"]
ex = pd.read_parquet(f"{CH}/labels/exch_roots_v1.parquet").set_index("root")
T["root_bal"] = T.root.map(rb).round(0); T["root_naddr"] = T.root.map(rn); T["exch_label"] = T.root.map(ex.exch)
T.drop(columns=["akey"]).to_csv(os.environ.get("SEED_OUT", f"{REPO}/data/research/seed_lookup.csv"), index=False)
pd.set_option("display.width", 250); pd.set_option("display.max_colwidth", 40)
print(T.drop(columns=["akey", "label"]).to_string(), flush=True)
print("by root:\n", T[T.root >= 0].groupby("root").agg(n=("item", "size"), bal=("root_bal", "first"), naddr=("root_naddr", "first"), ex=("exch_label", "first")).sort_values("bal", ascending=False).to_string(), flush=True)
print(f"done {time.time()-T0:.0f}s", flush=True)
