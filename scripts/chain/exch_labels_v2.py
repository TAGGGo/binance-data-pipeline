"""Exchange/custody label set v2 (11-02): exch_roots_v1 (seeds/PoR/deposit rules) + Whale-Alert hot roots + cold pools
from cb_trace2 (cb_pool_<name>.csv). Pool rules: keep core/mixed roots; drop 'amb' (single funding, never spent = maybe a user
withdrawal) and roots found in >1 pool (contested). v1 wins on conflicts. Output <chain>/labels/exch_roots_v2.parquet."""
import os, glob, pandas as pd
CH = os.environ.get("MDH_CHAIN_DIR", "/Volumes/chain/datahub/chain"); LB = f"{CH}/labels"
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")); RS = f"{REPO}/data/research"
HOT = {"coinbase": [214016, 19], "kraken": [22341, 55], "gemini": [236], "crypto.com": [1321], "bitstamp": [2925066, 6519430], "upbit": [1708]}
CAND = {155489905: "upbit"}   # Mr.100 wallet, Arkham/Crystal say Upbit, not confirmed
v1 = pd.read_parquet(f"{LB}/exch_roots_v1.parquet")[["root", "exch", "kind", "bal_btc"]].drop_duplicates("root")
rows = [dict(root=r, exch=e, kind="hot_wa") for e, rs in HOT.items() for r in rs]
rows += [dict(root=r, exch=e, kind="candidate") for r, e in CAND.items()]
P = {}
for f in glob.glob(f"{RS}/cb_pool_*.csv"):
    n = os.path.basename(f)[8:-4].replace("cryptocom", "crypto.com"); D = pd.read_csv(f); D = D[(D.bal > 1) & (D.root >= 0)]
    D["amb"] = (D.vout == 0) & (D.ntx == 1)
    P[n] = D.groupby("root").agg(bal_btc=("bal", "first"), amb=("amb", "all"), core=("cls", lambda s: (s == "core").all()), left=("cls", lambda s: (s == "left").all())).reset_index()
cnt = pd.concat([g.root for g in P.values()]).value_counts()
for n, g in P.items():
    g = g[~g.amb & ~g.left & g.root.map(lambda r: cnt[r] == 1)]
    rows += [dict(root=r, exch=n, kind="cold_core" if c else "cold_mixed", bal_btc=b) for r, c, b in zip(g.root, g.core, g.bal_btc)]
new = pd.DataFrame(rows); new = new[~new.root.isin(v1.root)]
out = pd.concat([v1, new], ignore_index=True).drop_duplicates("root")
out.to_parquet(f"{LB}/exch_roots_v2.parquet", index=False)
s = out.groupby("exch").bal_btc.sum().sort_values(ascending=False)
print("labels v2:", len(out), "roots; BTC (pool/seed balances, hot balances not included):"); print(s.head(20).round(0).to_string())
print("total", round(s.sum()))
