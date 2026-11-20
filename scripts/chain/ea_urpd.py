"""Entity-adjusted URPD, Glassnode rules (Avalon 11-02 "最好是和glassnode一样"):
  entity = ats_v1 multi-input cluster; all roots of one labeled exchange (labels/exch_roots_v2) = ONE entity, so moves
  between its wallets are internal. Day by day from genesis: per entity net flow (internal moves cancel);
  net > 0 = buy at that day's close -> balance-weighted average purchase price P; net < 0 = sell, P unchanged.
  Snapshot = each entity's whole balance in one bucket at P. Versions written per snapshot:
    all   : every entity (sanity: total = tracked supply)
    ex    : exchanges excluded (Glassnode rule)               <- main
    ex_cb : also excluded Coinbase custody/cold pool (ETF etc.), i.e. ETF NOT kept
Universe = ats_v1 index (addresses that ever received >= 0.1 BTC, ~90% of supply). Resumable (state in <chain>/ea_urpd/).
Outputs <chain>/ea_urpd/snap_YYYY-MM-DD.npz (bins) + data/onchain/self/ea_urpd_latest{SUF}.csv."""
import os, json, time, datetime as dt, fcntl, numpy as np, pandas as pd, duckdb
from concurrent.futures import ThreadPoolExecutor
T0 = time.time(); BUDGET = float(os.environ.get("EA_BUDGET", 30000))
CH = os.environ.get("MDH_CHAIN_DIR", "/Volumes/chain/datahub/chain"); V1 = f"{CH}/ats_v1"; R = f"{CH}/raw"; W = f"{CH}/ea_urpd"
os.makedirs(W, exist_ok=True); REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SUF = os.environ.get("EA_SUF", "")
def log(*x): print("ea_urpd:", f"{time.time()-T0:.0f}s", *x, flush=True)
lk = open(f"{W}/run{SUF}.lock", "w")
try: fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError: log("another run; skip"); raise SystemExit
SF = f"{W}/state_g{SUF}.json"; st = json.load(open(SF)) if os.path.exists(SF) else {}
v1 = json.load(open(f"{V1}/state.json")); END = dt.date.fromisoformat(v1["flows_done"])
NB = 16; B_ = [np.load(f"{V1}/addrs_b{b:02d}.npy", mmap_mode="r") for b in range(NB)]; OFF = np.cumsum([0] + [len(x) for x in B_]); N = int(OFF[-1])
def ids(addr):
    out = np.full(len(addr), -1, np.int64); bk = addr & (NB - 1)
    for b in range(NB):
        m = np.nonzero(bk == b)[0]
        if len(m) and len(B_[b]):
            pos = np.minimum(np.searchsorted(B_[b], addr[m]), len(B_[b]) - 1); ok = B_[b][pos] == addr[m]; out[m[ok]] = OFF[b] + pos[ok]
    return out
if not os.path.exists(f"{W}/root_of_ent.npy"): log("run ent_dense.py first"); raise SystemExit
SUF = os.environ.get("EA_SUF", ""); ENT = np.load(f"{W}/{os.environ.get('EA_ENT', 'ent' + SUF)}.npy", mmap_mode="r"); NE = len(np.load(f"{W}/root_of_ent.npy", mmap_mode="r"))   # 11-02: dense entity ids
if len(ENT) != N: log(f"ENT has {len(ENT)} rows but the ats_v1 index has {N}: rerun ent_dense.py"); raise SystemExit
def E(r): return np.asarray(ENT[np.asarray(r, np.int64)]).astype(np.int64)
L = pd.read_parquet(f"{CH}/labels/exch_roots_v2.parquet").drop_duplicates("root")
L["grp"] = L.exch.str.replace("krakenbtc", "kraken").str.replace("gatebtc", "gate")
G = {g: int(x.root.min()) for g, x in L.groupby("grp")}            # one representative root per exchange
lab_roots = E(L.root.values); o_ = np.argsort(lab_roots); lab_roots = lab_roots[o_]
lab_rep = E(np.array([G[g] for g in L.grp.values], np.int64))[o_]
reps = np.sort(E(np.array(list(G.values()), np.int64)))
cb_pool = L[(L.grp == "coinbase") & L.kind.astype(str).str.startswith("cold")].root.values.astype(np.int64)
# Coinbase custody pool kept as separate entities in 'ex' (ETF/custody coins = holders); grouped only as exchange in 'ex_cb'
keep_sep = np.sort(E(cb_pool))
# 11-02: state as disk-backed memmaps (the in-RAM version was killed by macOS memory pressure at 2010-08, no traceback)
from numpy.lib.format import open_memmap
if os.path.exists(f"{W}/Bg{SUF}.npy") and st.get("done"):
    Bal = open_memmap(f"{W}/Bg{SUF}.npy", mode="r+"); P = open_memmap(f"{W}/Pg{SUF}.npy", mode="r+"); T = open_memmap(f"{W}/Tg{SUF}.npy", mode="r+"); d = dt.date.fromisoformat(st["done"]) + dt.timedelta(days=1)
else:
    Bal = open_memmap(f"{W}/Bg{SUF}.npy", mode="w+", dtype=np.float32, shape=(NE,)); P = open_memmap(f"{W}/Pg{SUF}.npy", mode="w+", dtype=np.float32, shape=(NE,)); T = open_memmap(f"{W}/Tg{SUF}.npy", mode="w+", dtype=np.float32, shape=(NE,)); d = dt.date(2009, 1, 3)
    open(f"{W}/ats_daily{SUF}.csv", "w").write("d,buy_ex,sell_ex,buy_excb,sell_excb,buy_small,sell_small,supply\n")
px = pd.read_csv(f"{REPO}/data/onchain/self/urpd_metrics_daily.csv", index_col=0, parse_dates=True).close
pxd = {k.date(): float(v) for k, v in px.items()}
def price(day): return pxd.get(day, 0.05 if day >= dt.date(2010, 7, 18) else 0.0)
def ent(r):
    """root -> entity id: labeled exchange roots collapse to their exchange rep, except Coinbase custody pool roots"""
    pos = np.minimum(np.searchsorted(lab_roots, r), len(lab_roots) - 1); hit = lab_roots[pos] == r
    if len(keep_sep): hit &= ~IS_CB[r]
    e = r.copy(); e[hit] = lab_rep[pos[hit]]; return e
def read_day(ds):
    """11-02: everything state-independent happens here, in worker threads (lookup, entity map, per-entity net)"""
    if not os.path.exists(f"{R}/inputs/date={ds}.parquet"): return None
    cc = duckdb.connect(); cc.execute("set threads=2")
    try:
        o = cc.execute(f"select addr, sats from '{R}/outputs/date={ds}.parquet' where addr<>0").fetchnumpy()
        i = cc.execute(f"select addr, sats from '{R}/inputs/date={ds}.parquet' where addr<>0").fetchnumpy()
    finally: cc.close()
    a = np.concatenate([o["addr"], i["addr"]]); v = np.concatenate([o["sats"], -i["sats"]]).astype(np.float64) / 1e8
    k = ids(a); m = k >= 0
    if not m.any(): return np.zeros(0, np.int64), np.zeros(0)
    r = ent(E(k[m])); v = v[m]
    od = np.argsort(r, kind="stable"); r = r[od]; v = v[od]; u, s0 = np.unique(r, return_index=True)
    return u, np.add.reduceat(v, s0)
EDGES = np.concatenate([[0], np.arange(1000, 150001, 1000)]).astype(np.float64)
IS_REP = np.zeros(NE, bool); IS_REP[reps] = True; IS_CB = np.zeros(NE, bool); IS_CB[keep_sep] = True   # 11-02: lookups instead of np.isin (snapshots were ~18 s each)
def snapshot(day):
    dn = (dt.date.fromisoformat(day) - dt.date(2009, 1, 3)).days
    nz = np.concatenate([np.flatnonzero(Bal[a:a+20_000_000] > 1e-8) + a for a in range(0, NE, 20_000_000)]); b = Bal[nz].astype(np.float64); p = P[nz].astype(np.float64)
    isrep = IS_REP[nz]; iscb = IS_CB[nz]; sth = (dn - T[nz].astype(np.float64)) < 155
    bi = np.minimum(p // 1000, len(EDGES) - 2).astype(np.int64)
    out = {}
    for name, m in (("all", np.ones(len(nz), bool)), ("ex", ~isrep), ("ex_cb", ~isrep & ~iscb), ("sth_ex", ~isrep & sth), ("sth_excb", ~isrep & ~iscb & sth)):
        out[name] = np.bincount(bi[m], weights=b[m], minlength=len(EDGES) - 1)
    np.savez(f"{W}/snap{SUF}_{day}.npz", edges=EDGES, **out)
    px_ = price(dt.date.fromisoformat(day)); ex_ = ~isrep; xc_ = ~isrep & ~iscb; rows_ = [px_]
    for m in (ex_ & sth, ex_ & ~sth, xc_ & sth, xc_ & ~sth): rows_.append(b[m].sum())
    for m in (ex_ & sth, xc_ & sth): rows_.append((b[m] * p[m]).sum())
    for m in (ex_, xc_):
        lo_, hi_ = px_ * 0.95, px_ * 1.05; rows_.append(100 * b[m & (p >= lo_) & (p < hi_)].sum() / b[m].sum())
    w_ = xc_ & (b >= 1000); rows_ += [int(w_.sum()), b[w_].sum(), b[w_ & (b < 10000)].sum(), b[w_ & (b >= 10000)].sum()]
    open(SUPF, "a").write(day + "," + ",".join(f"{x:.2f}" for x in rows_) + "\n")
    ex_tot = {g: float(Bal[E([r])[0]]) for g, r in G.items()}
    return out, ex_tot
GN = sorted(G, key=lambda g: G[g]); GR = E(np.array([G[g] for g in GN], np.int64)); ex_rows = []
if not os.path.exists(f"{W}/exch_daily{SUF}.csv"): open(f"{W}/exch_daily{SUF}.csv", "w").write("d," + ",".join(GN) + ",coinbase_pool\n")
ats_rows = []; from collections import deque; WIN = deque(maxlen=30); wk_rows = []
ATSF = f"{W}/ats30{SUF}.csv"   # 11-02: daily (was weekly); header only when the file is new (an hourly run used to truncate it)
if not os.path.exists(ATSF): open(ATSF, "w").write("d,gn_buy_ex,gn_sell_ex,sym_sell_ex,gn_buy_excb,gn_sell_excb,sym_sell_excb,gn_buy_small,gn_sell_small,sym_sell_small,bal_ex\n")
WF = f"{W}/win{SUF}.npz"   # the 30-day window survives between incremental runs
if os.path.exists(WF):
    _z = np.load(WF); WIN.extend((_z[f"u{i}"], _z[f"n{i}"]) for i in range(int(_z["k"])))
AGG = [np.zeros(0, np.int64), np.zeros(0), np.zeros(0, np.int32)]   # sorted entity ids, 30-day net, number of days present
def agg_add(u_, n_, sgn):
    U_, D_, C_ = AGG; pos = np.searchsorted(U_, u_); hit = (pos < len(U_)) & (U_[np.minimum(pos, len(U_) - 1)] == u_) if len(U_) else np.zeros(len(u_), bool)
    D_[pos[hit]] += sgn * n_[hit]; C_[pos[hit]] += sgn
    if (~hit).any():
        assert sgn > 0
        nu, nn = u_[~hit], n_[~hit]; ip = pos[~hit]
        U_ = np.insert(U_, ip, nu); D_ = np.insert(D_, ip, nn); C_ = np.insert(C_, ip, 1)
    keep = C_ > 0
    AGG[:] = [U_[keep], D_[keep], C_[keep]] if not keep.all() else [U_, D_, C_]
for _u, _n in list(WIN): agg_add(_u, _n, +1)
OLDF = f"{W}/oldsp{SUF}.csv"; old_rows = []
if not os.path.exists(OLDF): open(OLDF, "w").write("d,spent_5y,spent_10y\n")
SUPF = f"{W}/supply{SUF}.csv"
if not os.path.exists(SUPF): open(SUPF, "w").write("d,price,sth_ex,lth_ex,sth_excb,lth_excb,sth_cost_ex,sth_cost_excb,conc5_ex,conc5_excb,whale_n,whale_bal,whale1k10k_bal,whale10k_bal\n")
def save_win():
    dd = {"k": np.array(len(WIN))}
    for i, (a_, b_) in enumerate(WIN): dd[f"u{i}"] = a_; dd[f"n{i}"] = b_
    np.savez(WF + ".tmp.npz", **dd); os.replace(WF + ".tmp.npz", WF)
pool = ThreadPoolExecutor(int(os.environ.get("EA_THREADS", 8))); q = {}; n = 0
while d <= END and time.time() - T0 < BUDGET:
    for k in range(12):
        x = d + dt.timedelta(days=k)
        if x <= END and x.isoformat() not in q: q[x.isoformat()] = pool.submit(read_day, x.isoformat())
    got = q.pop(d.isoformat()).result()
    if got is not None and len(got[0]):
        u, net = got
        b0 = Bal[u].astype(np.float64); p0 = P[u].astype(np.float64); pr = price(d)
        buy = net > 0; nb = b0 + net
        P[u[buy]] = ((p0[buy] * b0[buy] + net[buy] * pr) / nb[buy]).astype(np.float32)
        dn = (d - dt.date(2009, 1, 3)).days; t0 = T[u].astype(np.float64)
        T[u[buy]] = ((t0[buy] * b0[buy] + net[buy] * dn) / nb[buy]).astype(np.float32)
        Bal[u] = np.maximum(nb, 0).astype(np.float32)
        if d >= dt.date(2015, 1, 1):
            isrep = IS_REP[u]; iscb = IS_CB[u]; small = b0 < 1000
            bc = np.minimum(np.maximum(net, 0), b0); sc = np.minimum(np.maximum(-net, 0), b0)
            vals = []
            for m_ in (~isrep, ~isrep & ~iscb, ~isrep & ~iscb & small): vals += [bc[m_].sum(), sc[m_].sum()]
            ats_rows.append(d.isoformat() + "," + ",".join(f"{x:.2f}" for x in vals) + "\n")
        if d >= dt.date(2024, 9, 1):
            if len(WIN) == WIN.maxlen: agg_add(*WIN[0], -1)      # the day leaving the 30-day window
            WIN.append((u, net)); agg_add(u, net, +1)
        # 11-02 entity-adjusted old-coin spending: sells (net < 0) by entities whose average coin age is >= 5 / 10 years
        if d >= dt.date(2016, 1, 1):
            age = dn - t0; sell = np.minimum(np.maximum(-net, 0), b0); okx = ~IS_REP[u] & ~IS_CB[u]
            old_rows.append(f"{d.isoformat()},{sell[okx & (age >= 1826)].sum():.2f},{sell[okx & (age >= 3652)].sum():.2f}\n")
    if d >= dt.date(2016, 1, 1):
        ex_rows.append(d.isoformat() + "," + ",".join(f"{x:.1f}" for x in Bal[GR].astype(np.float64)) + f",{float(Bal[keep_sep].astype(np.float64).sum()):.1f}\n")
    if d >= dt.date(2024, 11, 1) and len(AGG[0]):
        U2, D2 = AGG[0], AGG[1]; Be = Bal[U2].astype(np.float64); Bs = np.maximum(Be - D2, 0)
        rep2 = IS_REP[U2]; cb2 = IS_CB[U2]; sm2 = np.maximum(Be, Bs) < 1000
        gb = np.maximum(D2, 0); gs = np.minimum(np.maximum(-D2, 0), Be); ss = np.minimum(np.maximum(-D2, 0), Bs); vals = []
        for m_ in (~rep2, ~rep2 & ~cb2, ~rep2 & ~cb2 & sm2): vals += [gb[m_].sum(), gs[m_].sum(), ss[m_].sum()]
        vals.append(Be[~rep2].sum())
        open(ATSF, "a").write(d.isoformat() + "," + ",".join(f"{x:.2f}" for x in vals) + "\n")
    st["done"] = d.isoformat(); n += 1
    if d == dt.date(2024, 8, 31):   # 11-02: checkpoint, so method changes that only touch 2024-09+ replay ~2 years, not 17
        import shutil; Bal.flush(); P.flush(); T.flush()
        for x in ("Bg", "Pg", "Tg"): shutil.copy(f"{W}/{x}{SUF}.npy", f"{W}/ckpt_{x}{SUF}.npy")
        json.dump(dict(st), open(f"{W}/ckpt_state{SUF}.json", "w")); log("checkpoint 2024-08-31")
    if (d.day == 1 and d.month in (1, 4, 7, 10) and d.year >= 2016) or (d >= dt.date(2024, 9, 1) and d.weekday() == 6) or d >= dt.date(2026, 4, 1): snapshot(d.isoformat())
    if n % 100 == 0:
        Bal.flush(); P.flush(); T.flush(); open(f"{W}/ats_daily{SUF}.csv", "a").write("".join(ats_rows)); ats_rows = []; open(f"{W}/exch_daily{SUF}.csv", "a").write("".join(ex_rows)); ex_rows = []; open(OLDF, "a").write("".join(old_rows)); old_rows = []; save_win(); json.dump(st, open(SF, "w")); log("at", d)
    d += dt.timedelta(days=1)
Bal.flush(); P.flush(); T.flush(); open(f"{W}/ats_daily{SUF}.csv", "a").write("".join(ats_rows)); open(f"{W}/exch_daily{SUF}.csv", "a").write("".join(ex_rows)); open(OLDF, "a").write("".join(old_rows)); save_win(); json.dump(st, open(SF, "w"))
if d > END:
    out, ex_tot = snapshot(END.isoformat())
    df = pd.DataFrame({"lo": EDGES[:-1], "hi": EDGES[1:], **out}); df.to_csv(f"{REPO}/data/onchain/self/ea_urpd_latest{SUF}.csv", index=False)
    import shutil; shutil.copy(f"{W}/exch_daily{SUF}.csv", f"{REPO}/data/onchain/self/ea_exch_daily{SUF}.csv"); shutil.copy(ATSF, f"{REPO}/data/onchain/self/ea_ats30{SUF}.csv"); shutil.copy(OLDF, f"{REPO}/data/onchain/self/ea_oldsp{SUF}.csv"); shutil.copy(SUPF, f"{REPO}/data/onchain/self/ea_supply{SUF}.csv"); shutil.copy(f"{W}/ats_daily{SUF}.csv", f"{REPO}/data/research/ea_ats_daily{SUF}.csv")
    if SUF == "_n":
        import subprocess, sys; subprocess.run([sys.executable, os.path.join(REPO, "scripts", "onchain", "ea_export.py")])
    log("done to", END, {k: round(float(v.sum())) for k, v in out.items()})
    log("exchange entity balances:", {g: round(x) for g, x in sorted(ex_tot.items(), key=lambda t: -t[1])[:15]})
else:
    log("paused at", st["done"])
