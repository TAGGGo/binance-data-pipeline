"""Fresh-address merges for the entity layer (11-02, ATS fix): R1 (1-output tx to a fresh address) and R2 (2-output tx,
exactly one fresh output = change) join the receiver root to the sender root. Edges from ats_kit/edges (2024-10 onward).
Guards: sender or receiver is an exchange / miner / labeled (exch_roots_v2) root, or the sender is busy that day (>= 50
such txs) -> no merge. Output <chain>/ea_urpd/merge_src.npy, merge_dst.npy (sorted src roots -> dst root)."""
import os, glob, numpy as np, pandas as pd, time
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
T0 = time.time(); C = os.environ.get("MDH_CHAIN_DIR", "/Volumes/chain/datahub/chain"); V1 = f"{C}/ats_v1"; K = f"{C}/ats_kit"; W = f"{C}/ea_urpd"
N = len(np.load(f"{V1}/parent.npy", mmap_mode="r"))
ex = np.zeros(N, bool); ex[np.load(f"{V1}/flag_exchange.npy")] = True; ex[np.load(f"{V1}/flag_miner.npy")] = True
ex[pd.read_parquet(f"{C}/labels/exch_roots_v2.parquet").root.values.astype(np.int64)] = True
S_, R_ = [], []
for f in sorted(glob.glob(f"{K}/edges/*.npz")):
    z = np.load(f); s = z["s"].astype(np.int64); r = z["r"].astype(np.int64); nout = z["nout"]; fresh = z["fresh"].astype(bool); day = z["day"].astype(np.int64)
    _, inv, cnt = np.unique(day * 400_000_000 + s, return_inverse=True, return_counts=True); busy = cnt[inv] >= 50
    m = fresh & (s != r) & ~busy & ~ex[s] & ~ex[r] & ((nout == 1) | (nout == 2))
    S_.append(s[m]); R_.append(r[m]); print(os.path.basename(f), int(m.sum()), flush=True)
a = np.concatenate(S_); b = np.concatenate(R_)
u = np.unique(np.concatenate([a, b])); ia = np.searchsorted(u, a); ib = np.searchsorted(u, b)
nc, lab = connected_components(coo_matrix((np.ones(len(ia), np.int8), (ia, ib)), shape=(len(u), len(u))), directed=False)
first = np.full(nc, -1, np.int64); order = np.arange(len(u))[::-1]; first[lab[order]] = order
sizes = np.bincount(lab); MAXC = int(os.environ.get("MERGE_MAXC", 200))   # 11-02: first run made a 5.8M-root super-cluster
dst = u[first[lab]]; ch = (dst != u) & (sizes[lab] <= MAXC)
print(f"components > {MAXC} roots left unmerged: {int((sizes > MAXC).sum()):,} ({int(sizes[sizes > MAXC].sum()):,} roots)"); print(f"edges {len(a):,}, roots {len(u):,} -> {nc:,} entities; biggest component {sizes.max():,} roots", flush=True)
np.save(f"{W}/merge_src.npy", u[ch]); np.save(f"{W}/merge_dst.npy", dst[ch]); print(f"done {time.time()-T0:.0f}s, moved {int(ch.sum()):,}", flush=True)
