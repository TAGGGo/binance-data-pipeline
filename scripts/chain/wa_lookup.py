"""Whale Alert seeds (X, data/research/whale_alert_btc_seeds.csv) -> exchange-side addresses -> ats_v1 roots.
from=#X: all inputs are X. to=#X: the output(s) closest to the reported amount are X. Compares with existing labels
(exch_roots_v1, btc_coinbase_v1). Output data/research/wa_lookup.csv + per exchange root summary. Read-only."""
import os, datetime as dt, numpy as np, pandas as pd, duckdb
CH = os.environ.get("MDH_CHAIN_DIR", "/Volumes/chain/datahub/chain"); V1 = f"{CH}/ats_v1"; R = f"{CH}/raw"
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
NB = 16; B = [np.load(f"{V1}/addrs_b{b:02d}.npy", mmap_mode="r") for b in range(NB)]; OFF = np.cumsum([0] + [len(x) for x in B])
def ids(addr):
    addr = np.asarray(addr, np.int64); out = np.full(len(addr), -1, np.int64); bk = addr & (NB - 1)
    for b in range(NB):
        m = np.nonzero(bk == b)[0]
        if len(m) and len(B[b]):
            pos = np.minimum(np.searchsorted(B[b], addr[m]), len(B[b]) - 1); ok = B[b][pos] == addr[m]; out[m[ok]] = OFF[b] + pos[ok]
    return out
parent = np.load(f"{V1}/parent.npy", mmap_mode="r"); bal = np.load(f"{V1}/bal.npy", mmap_mode="r"); N = len(parent)
S = pd.read_csv(f"{REPO}/data/research/whale_alert_btc_seeds.csv", sep="|"); rows = []
for _, s in S.iterrows():
    tk = np.array([int(s.txid[:16], 16)], np.uint64).view(np.int64)[0]; d0 = dt.date.fromisoformat(s.date)
    for side, lab in (("inputs", s["from"]), ("outputs", s["to"])):
        if not str(lab).startswith("#") and "Coinbase" not in str(lab): continue
        ex = str(lab).lstrip("#").lower().replace("cryptocom", "crypto.com").replace("coinbase institutional", "coinbase")
        got = []
        for k in (0, -1, 1):
            f = f"{R}/{side}/date={d0 + dt.timedelta(days=k)}.parquet"
            if os.path.exists(f): got += duckdb.sql(f"select addr, sats from '{f}' where tx = {tk}").fetchall()
            if got: break
        if side == "outputs" and got:
            tgt = float(s.btc) * 1e8; best = min(got, key=lambda x: abs(x[1] - tgt)); got = [g for g in got if abs(g[1] - best[1]) < 0.02 * tgt]
        for a, v in got: rows.append(dict(txid=s.txid, exch=ex, side=side, addr=a, btc=v / 1e8))
T = pd.DataFrame(rows); print("tx found:", T.txid.nunique(), "/", len(S), flush=True)
ix = ids(T.addr.values); T["root"] = [int(parent[i]) if i >= 0 else -1 for i in ix]
roots = np.unique(T.root[T.root >= 0]); rb = {}; rn = {}
for a in range(0, N, 20_000_000):
    p = np.asarray(parent[a:a+20_000_000]); m = np.isin(p, roots)
    if m.any():
        g = pd.DataFrame({"r": p[m], "b": np.asarray(bal[a:a+20_000_000])[m]}).groupby("r").b.agg(["sum", "size"])
        for r, x in g.iterrows(): rb[r] = rb.get(r, 0) + x["sum"]; rn[r] = rn.get(r, 0) + x["size"]
e1 = pd.read_parquet(f"{CH}/labels/exch_roots_v1.parquet").drop_duplicates("root").set_index("root").exch
e2 = pd.read_parquet(f"{CH}/labels/btc_coinbase_v1.parquet").drop_duplicates("root").set_index("root").kind
T["root_bal"] = T.root.map(rb); T["root_n"] = T.root.map(rn); T["lab_exch"] = T.root.map(e1); T["lab_cb"] = T.root.map(e2)
T.to_csv(f"{REPO}/data/research/wa_lookup.csv", index=False)
pd.set_option("display.width", 220)
G = T[T.root >= 0].groupby(["exch", "root"]).agg(n=("addr", "size"), bal=("root_bal", "first"), naddr=("root_n", "first"), lab=("lab_exch", "first"), cb=("lab_cb", "first")).reset_index()
print(G.sort_values(["exch", "bal"], ascending=[True, False]).to_string(), flush=True)
print("unmatched addresses (not in ats_v1):", int((T.root < 0).sum()), flush=True)
