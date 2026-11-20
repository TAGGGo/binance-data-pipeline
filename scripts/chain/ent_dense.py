"""One-off prep for ea_urpd (11-02): address index -> dense entity id (int32), so the day loop reads a 1.4 GB int32
array instead of the 2.8 GB int64 parent, and per-entity state shrinks to the number of roots. Sequential, chunked."""
import os, numpy as np, time
from numpy.lib.format import open_memmap
T0 = time.time(); CH = os.environ.get("MDH_CHAIN_DIR", "/Volumes/chain/datahub/chain"); V1 = f"{CH}/ats_v1"; W = f"{CH}/ea_urpd"
os.makedirs(W, exist_ok=True)
parent = np.load(f"{V1}/parent.npy", mmap_mode="r"); N = len(parent); C = 20_000_000
isroot = np.zeros(N, bool)
for a in range(0, N, C): isroot[a:a+C] = np.asarray(parent[a:a+C]) == np.arange(a, min(a+C, N))
rank = np.cumsum(isroot, dtype=np.int32) - 1; nr = int(rank[-1]) + 1; del isroot
print("roots", nr, f"{time.time()-T0:.0f}s", flush=True)
SUF = os.environ.get("EA_SUF", "")
MS = f"{W}/merge_src.npy" if "m" in SUF else None
if MS: src = np.load(MS); dst = np.load(f"{W}/merge_dst.npy"); print("merge map", len(src), flush=True)
E = open_memmap(f"{W}/ent{SUF}.npy", mode="w+", dtype=np.int32, shape=(N,))
for a in range(0, N, C):
    p = np.asarray(parent[a:a+C]).astype(np.int64)
    if MS:
        pos = np.minimum(np.searchsorted(src, p), len(src) - 1); hit = src[pos] == p; p[hit] = dst[pos[hit]]
    E[a:a+C] = rank[p]
E.flush(); np.save(f"{W}/root_of_ent.npy", np.nonzero(rank[1:] != rank[:-1])[0].astype(np.int64) + 1 if False else np.flatnonzero(np.diff(np.concatenate([[-1], rank])) == 1).astype(np.int64))
print("done", f"{time.time()-T0:.0f}s", flush=True)
