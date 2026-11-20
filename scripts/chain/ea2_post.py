"""Apply the CURRENT labels to the label-free ea2 replay (seconds, no replay). Labels: labels/exch_roots_v2.parquet
(+ labels/extra_roots.csv: root,exch,kind — e.g. Arkham-found wallets, ETF wallets; kind 'cold*' of coinbase and kind
'etf' = custody/ETF = kept as holders in 'ex', dropped in 'ex_cb'). Writes data/onchain/self/ea2_*.csv + ea2_cards.json:
  ea2_supply.csv  d, price, sth/lth (ex, excb), STH cost, ±5% share, whales (excb, entities >= 1,000 BTC)
  ea2_exch.csv    d, per exchange balance (entities >= 10 BTC), custody pool
  ea2_ats.csv     d, 30-day buys/sells (ex, excb, excb <1000 BTC)  [daily from 2024-11]
  ea2_old.csv     d, sells by entities with average age >= 5y / 10y (labels excluded)
  ea2_cards.json  latest + ~30-day-earlier URPD / STH-URPD bins and the exchange series (same shape as ea_cards.json)"""
import os, glob, json, datetime as dt, numpy as np, pandas as pd
CH = os.environ.get("MDH_CHAIN_DIR", "/Volumes/chain/datahub/chain"); W = f"{CH}/ea2"; WE = f"{CH}/ea_urpd"
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")); O = f"{REPO}/data/onchain/self"
ENT = np.load(f"{WE}/ent.npy", mmap_mode="r")
L = pd.read_parquet(f"{CH}/labels/exch_roots_v2.parquet")[["root", "exch", "kind"]]
xf = f"{CH}/labels/extra_roots.csv"
if os.path.exists(xf): L = pd.concat([pd.read_csv(xf)[["root", "exch", "kind"]], L])
L = L.drop_duplicates("root"); L["grp"] = L.exch.str.replace("krakenbtc", "kraken").str.replace("gatebtc", "gate")
L["ent"] = np.asarray(ENT[L.root.values.astype(np.int64)]).astype(np.int64)
af = f"{CH}/labels/auto_ents.csv"   # 11-03: exch_scan.py promotions (ea2 entity ids; overflow ids resolved with xidx PAR)
if os.path.exists(af):
    A = pd.read_csv(af)
    if len(A):
        _x = np.load(f"{W}/xidx.npz"); PAR = _x["par"]; NE = len(np.load(f"{WE}/root_of_ent.npy", mmap_mode="r")); e = A.ent.values.astype(np.int64).copy()
        for _ in range(64):
            m_ = e >= NE
            if not m_.any(): break
            nx_ = PAR[e[m_] - NE]
            if (nx_ == e[m_]).all(): break
            e[m_] = nx_
        A = A.assign(ent=e, root=-1, grp=A.exch); L = pd.concat([L, A[~A.ent.isin(set(L.ent))][["root", "exch", "kind", "grp", "ent"]]])
cust = L.kind.astype(str).str.startswith("cold") & (L.grp == "coinbase") | (L.kind.astype(str) == "etf")
EX = np.sort(L.ent[~cust].unique()); CU = np.sort(L.ent[cust].unique())
egrp = L[~cust].drop_duplicates("ent").set_index("ent").grp; GN = sorted(egrp.unique())
NBIN = 150; lo = np.arange(NBIN) * 1000.0
# 11-03: days after the ats_v1 address index end are NOT reliable (coins sent to newer addresses vanish from the tracked
# set and look like sells: 10/31 -3.2 万, 11/01 -2.2 万 BTC). Only use snapshots / ATS days up to index_upto.
CUT = json.load(open(f"{W}/state.json"))["done"]   # 11-03: ea2 now follows addresses newer than the index itself (overflow entities)
def upto(fs): return [f for f in fs if os.path.basename(f)[:10] <= CUT]
rows, exr = [], []
for f in upto(sorted(glob.glob(f"{W}/snap/2*.npz"))):
    z = np.load(f); day = os.path.basename(f)[:10]; ids = z["ids"].astype(np.int64); b = z["B"].astype(np.float64); p = z["P"].astype(np.float64)
    dn = float(z["dn"]); price = float(z["price"]); sth = (dn - z["T"].astype(np.float64)) < 155; sm = z["small"]
    isx = np.isin(ids, EX); isc = np.isin(ids, CU); ex = ~isx; xc = ~isx & ~isc
    hb = lambda m: np.bincount(np.minimum(p[m] // 1000, NBIN - 1).astype(np.int64), weights=b[m], minlength=NBIN)
    H = {"ex": hb(ex) + z["h_small"], "ex_cb": hb(xc) + z["h_small"], "sth_ex": hb(ex & sth) + z["h_small_sth"], "sth_excb": hb(xc & sth) + z["h_small_sth"]}
    np.savez(f"{W}/snap/post_{day}.npz", **H) if False else None
    conc = lambda h: 100 * h[(lo + 500 >= price * 0.95) & (lo + 500 < price * 1.05)].sum() / h.sum()
    w = xc & (b >= 1000)
    rows.append(dict(d=day, price=price, sth_ex=b[ex & sth].sum() + sm[0], lth_ex=b[ex & ~sth].sum() + sm[1], sth_excb=b[xc & sth].sum() + sm[0], lth_excb=b[xc & ~sth].sum() + sm[1],
                     sth_cost_ex=(b[ex & sth] * p[ex & sth]).sum() + sm[2], sth_cost_excb=(b[xc & sth] * p[xc & sth]).sum() + sm[2],
                     conc5_ex=conc(H["ex"]), conc5_excb=conc(H["ex_cb"]), whale_n=int(w.sum()), whale_bal=b[w].sum(), whale1k10k_bal=b[w & (b < 10000)].sum(), whale10k_bal=b[w & (b >= 10000)].sum()))
    g = pd.Series(b[isx], index=egrp.reindex(ids[isx]).values).groupby(level=0).sum()
    exr.append({"d": day, **{k: float(g.get(k, 0)) for k in GN}, "coinbase_pool": b[isc].sum()})
    globals().setdefault("HIST", {})[day] = H
S = pd.DataFrame(rows); S.to_csv(f"{O}/ea2_supply.csv", index=False, float_format="%.2f"); X = pd.DataFrame(exr); X.to_csv(f"{O}/ea2_exch.csv", index=False, float_format="%.1f")
ar = []
for f in upto(sorted(glob.glob(f"{W}/ats/*.npz"))):
    z = np.load(f); ids = z["ids"].astype(np.int64); D = z["D"].astype(np.float64); Be = z["Be"].astype(np.float64)
    isx = np.isin(ids, EX); isc = np.isin(ids, CU); gb = np.maximum(D, 0); gs = np.minimum(np.maximum(-D, 0), Be); small = np.maximum(Be, Be - D) < 1000; s0 = z["small"]
    r = {"d": os.path.basename(f)[:10]}
    for nm, m in (("ex", ~isx), ("excb", ~isx & ~isc), ("small", ~isx & ~isc & small)):
        r[f"gn_buy_{nm}"] = gb[m].sum() + s0[0]; r[f"gn_sell_{nm}"] = gs[m].sum() + s0[1]
    ar.append(r)
pd.DataFrame(ar).to_csv(f"{O}/ea2_ats.csv", index=False, float_format="%.2f")
orr = []
for f in upto(sorted(glob.glob(f"{W}/old/*.npz"))):
    z = np.load(f); ids = z["ids"].astype(np.int64); m = ~np.isin(ids, EX) & ~np.isin(ids, CU); s = z["sell"].astype(np.float64); a = z["age"]
    orr.append({"d": os.path.basename(f)[:10], "spent_5y": s[m].sum(), "spent_10y": s[m & (a >= 3652)].sum()})
pd.DataFrame(orr).to_csv(f"{O}/ea2_old.csv", index=False, float_format="%.2f")
# long-history ATS from weekly snapshots (entities >= 10 BTC; 28-day change between snapshots), for backtests
wk = sorted(f for f in upto(glob.glob(f"{W}/snap/2*.npz")) if os.path.basename(f)[:4] >= "2016" and dt.date.fromisoformat(os.path.basename(f)[:10]).weekday() == 6)
SN = {os.path.basename(f)[:10]: f for f in wk}; hr = []
def bmap(f):
    z = np.load(f); return pd.Series(z["B"].astype(np.float64), index=z["ids"].astype(np.int64))
for t1 in SN:
    t0 = (dt.date.fromisoformat(t1) - dt.timedelta(days=28)).isoformat()
    if t0 not in SN: continue
    b1, b0 = bmap(SN[t1]), bmap(SN[t0]); u = b1.index.union(b0.index); Be = b1.reindex(u).fillna(0).values; Bs = b0.reindex(u).fillna(0).values; Dl = Be - Bs
    ids_ = u.values; isx = np.isin(ids_, EX); isc = np.isin(ids_, CU); gb = np.maximum(Dl, 0); gs = np.minimum(np.maximum(-Dl, 0), Be)
    r = {"d": t1}
    for nm, m in (("ex", ~isx), ("excb", ~isx & ~isc)): r[f"buy_{nm}"] = gb[m].sum(); r[f"sell_{nm}"] = gs[m].sum()
    hr.append(r)
pd.DataFrame(hr).to_csv(f"{O}/ea2_ats_weekly_hist.csv", index=False, float_format="%.2f")
days = sorted(HIST); last = days[-1]; want = (dt.date.fromisoformat(last) - dt.timedelta(days=30)).isoformat(); prev = max([x for x in days if x <= want] or [days[0]])
out = {"asof": last, "prev": prev, "lo": lo.tolist()}
for k in ("ex", "ex_cb", "sth_ex", "sth_excb"): out[k] = np.round(HIST[last][k], 1).tolist(); out[k + "_prev"] = np.round(HIST[prev][k], 1).tolist()
Xr = X[X.d >= "2023-01-01"]
out["exch"] = {"dates": Xr.d.tolist(), "total": Xr[GN].sum(axis=1).round(0).tolist(), "cb_pool": Xr.coinbase_pool.round(0).tolist(), "last": {g: round(float(Xr[g].iloc[-1])) for g in GN}, "src": "ea2"}
json.dump(out, open(f"{O}/ea2_cards.json", "w"))
# active entities: ats_v1 daily n_ent up to its index end (same clusters), ea2's own count after (includes new addresses)
_v1 = json.load(open(f"{CH}/ats_v1/state.json")); A1 = pd.read_csv(f"{CH}/ats_v1/daily.csv", usecols=["d", "n_ent"]); A1 = A1[A1.d >= "2012-01-01"]   # 11-03: ats_v1 to its last day; ea2 counts (incl. new addresses) override after the index end
if os.path.exists(f"{W}/active.csv"):
    A2 = pd.read_csv(f"{W}/active.csv", names=["d", "n_ent"]).drop_duplicates("d", keep="last"); A1 = pd.concat([A1, A2[A2.d > _v1["index_upto"]]])
A1.drop_duplicates("d", keep="last").to_csv(f"{O}/ea2_active.csv", index=False)
import shutil   # 11-03: the board reads these names (dashboard.py); ea2 replaces the old ea_urpd _n pipeline
shutil.copy(f"{O}/ea2_cards.json", f"{O}/ea_cards.json"); shutil.copy(f"{O}/ea2_supply.csv", f"{O}/ea_supply_n.csv"); shutil.copy(f"{O}/ea2_ats.csv", f"{O}/ea_ats30_n.csv")
print("ea2_post:", last, "snapshots", len(days), "ats days", len(ar), "| exchanges", round(float(Xr[GN].iloc[-1].sum())), "custody", round(float(Xr.coinbase_pool.iloc[-1])), "| labels", len(L), flush=True)
