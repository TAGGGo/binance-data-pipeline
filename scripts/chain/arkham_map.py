"""Map Arkham-attributed BTC addresses (data/research/arkham/btc_entity_addrs.csv) onto our ats_v1 clusters and compare
with our labels (exch_roots_v2). Output data/research/arkham/arkham_vs_ours.csv + per-entity summary in the log."""
import os, hashlib, numpy as np, pandas as pd
CH = os.environ.get("MDH_CHAIN_DIR", "/Volumes/chain/datahub/chain"); V1 = f"{CH}/ats_v1"
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")); OUT = f"{REPO}/data/research/arkham"
def akey(a): return int.from_bytes(hashlib.blake2b(a.encode(), digest_size=8).digest(), 'little', signed=True)
NB = 16; B = [np.load(f"{V1}/addrs_b{b:02d}.npy", mmap_mode="r") for b in range(NB)]; OFF = np.cumsum([0] + [len(x) for x in B])
def ids(addr):
    addr = np.asarray(addr, np.int64); out = np.full(len(addr), -1, np.int64); bk = addr & (NB - 1)
    for b in range(NB):
        m = np.nonzero(bk == b)[0]
        if len(m) and len(B[b]):
            pos = np.minimum(np.searchsorted(B[b], addr[m]), len(B[b]) - 1); ok = B[b][pos] == addr[m]; out[m[ok]] = OFF[b] + pos[ok]
    return out
parent = np.load(f"{V1}/parent.npy", mmap_mode="r"); bal = np.load(f"{V1}/bal.npy", mmap_mode="r"); N = len(parent)
A = pd.read_csv(f"{OUT}/btc_entity_addrs.csv"); A["k"] = [akey(a) for a in A.address]; ix = ids(A.k.values)
A["root"] = [int(parent[i]) if i >= 0 else -1 for i in ix]
roots = np.unique(A.root[A.root >= 0]); rb = {}; rn = {}
for a in range(0, N, 20_000_000):
    p = np.asarray(parent[a:a+20_000_000]); m = np.isin(p, roots)
    if m.any():
        g = pd.DataFrame({"r": p[m], "b": np.asarray(bal[a:a+20_000_000])[m]}).groupby("r").b.agg(["sum", "size"])
        for r, x in g.iterrows(): rb[r] = rb.get(r, 0) + x["sum"]; rn[r] = rn.get(r, 0) + x["size"]
L = pd.read_parquet(f"{CH}/labels/exch_roots_v2.parquet").drop_duplicates("root").set_index("root")
A["root_bal"] = A.root.map(rb); A["root_n"] = A.root.map(rn); A["our_exch"] = A.root.map(L.exch); A["our_kind"] = A.root.map(L.kind)
A.drop(columns=["k"]).to_csv(f"{OUT}/arkham_vs_ours.csv", index=False)
pd.set_option("display.width", 220)
R = A[A.root >= 0].drop_duplicates(["entity", "root"])
S = R.groupby("entity").agg(addrs=("address", "size"), roots=("root", "nunique"), bal=("root_bal", "sum"),
                            bal_labeled_same=("root_bal", lambda s: s[R.loc[s.index, "our_exch"].fillna("").str.contains(R.loc[s.index, "entity"].iloc[0].split("-")[0])].sum()),
                            bal_unlabeled=("root_bal", lambda s: s[R.loc[s.index, "our_exch"].isna()].sum()))
print(S.sort_values("bal", ascending=False).round(0).head(25).to_string(), flush=True)
print("not in our index:", int((A.root < 0).sum()), "of", len(A), flush=True)
cb = R[R.entity == "coinbase"]
print("coinbase roots by our label:\n", cb.groupby(cb.our_exch.fillna("none")).agg(n=("root", "size"), bal=("root_bal", "sum")).round(0).to_string(), flush=True)
print("coinbase biggest unlabeled roots:\n", cb[cb.our_exch.isna()].sort_values("root_bal", ascending=False)[["root", "root_bal", "root_n", "label"]].head(15).to_string(), flush=True)
