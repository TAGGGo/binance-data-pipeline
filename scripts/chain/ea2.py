"""Entity replay v2, LABEL-FREE (Avalon 11-02 "每次新发现就要跑几个小时"): labels are applied afterwards by ea2_post.py,
so a new exchange / ETF label takes seconds, not a replay. Entity = ats_v1 multi-input cluster (ent.npy, no grouping).
Day by day from genesis: per-entity net flow; net > 0 = buy at that day's close (balance-weighted average price P and
average acquisition day T); net < 0 = sell (P, T unchanged).
Snapshots (<chain>/ea2/snap/YYYY-MM-DD.npz): weekly (Sundays) from 2016, daily from 2026-04-01, plus every run's last day:
  big entities (balance >= 10 BTC): ids, B, P, T            -> any label set can be applied later
  small entities (< 10 BTC, never exchanges/ETFs in practice): price histograms (all / STH) + STH/LTH sums + STH cost
ATS (daily from 2024-11, <chain>/ea2/ats/YYYY-MM-DD.npz): 30-day net per entity (ids, D, Be for entities >= 10 BTC at
either end; sums for the small ones). Old coins (<chain>/ea2/old/): per-day sells by entities with average age >= 5y.
Resumable; checkpoint copy at 2024-08-31. Universe = ats_v1 index (addresses that ever got >= 0.1 BTC)."""
import os, json, time, datetime as dt, fcntl, shutil, numpy as np, pandas as pd, duckdb
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from numpy.lib.format import open_memmap
T0 = time.time(); BUDGET = float(os.environ.get("EA_BUDGET", 86000))
CH = os.environ.get("MDH_CHAIN_DIR", "/Volumes/chain/datahub/chain"); V1 = f"{CH}/ats_v1"; R = f"{CH}/raw"; WE = f"{CH}/ea_urpd"; W = f"{CH}/ea2"
for sub in ("snap", "ats", "old"): os.makedirs(f"{W}/{sub}", exist_ok=True)
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
def log(*x): print("ea2:", f"{time.time()-T0:.0f}s", *x, flush=True)
lk = open(f"{W}/run.lock", "w")
try: fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError: log("another run; skip"); raise SystemExit
SF = f"{W}/state.json"; st = json.load(open(SF)) if os.path.exists(SF) else {}
_v1 = json.load(open(f"{V1}/state.json")); END = dt.date.fromisoformat(_v1["flows_done"]); IDX_END = dt.date.fromisoformat(_v1["index_upto"])
if not os.environ.get("EA2_FROM_CKPT") and st.get("done") == END.isoformat() and os.path.exists(f"{W}/snap/{END}.npz"):
    out_csv = f"{REPO}/data/onchain/self/ea2_exch.csv"
    lbl_files = [f"{CH}/labels/{fn}" for fn in ("exch_roots_v2.parquet", "extra_roots.csv", "auto_ents.csv", "not_exch.csv")]
    lbl_mtime = max((os.path.getmtime(p) for p in lbl_files if os.path.exists(p)), default=0)
    if os.path.exists(out_csv) and os.path.getmtime(out_csv) >= lbl_mtime:
        log("up to date", END); raise SystemExit
    import subprocess, sys
    subprocess.run([sys.executable, os.path.join(REPO, "scripts", "chain", "ea2_post.py")])
    raise SystemExit
NB = 16; B_ = [np.load(f"{V1}/addrs_b{b:02d}.npy", mmap_mode="r") for b in range(NB)]; OFF = np.cumsum([0] + [len(x) for x in B_]); N = int(OFF[-1])
def ids(addr):
    out = np.full(len(addr), -1, np.int64); bk = addr & (NB - 1)
    for b in range(NB):
        m = np.nonzero(bk == b)[0]
        if len(m) and len(B_[b]):
            pos = np.minimum(np.searchsorted(B_[b], addr[m]), len(B_[b]) - 1); ok = B_[b][pos] == addr[m]; out[m[ok]] = OFF[b] + pos[ok]
    return out
ENT = np.load(f"{WE}/ent.npy", mmap_mode="r"); NE = len(np.load(f"{WE}/root_of_ent.npy", mmap_mode="r"))
if len(ENT) != N: log("ENT/index size mismatch: rerun ent_dense.py"); raise SystemExit
CAPX = int(os.environ.get("EA2_CAPX", 40_000_000)); NT = NE + CAPX   # 11-03: room for entities of addresses newer than the ats_v1 index
if os.environ.get("EA2_FROM_CKPT"):
    log("rebuilding state from the 2024-08-31 checkpoint")
    for x in ("B", "P", "T"):
        a_ = open_memmap(f"{W}/{x}.npy", mode="w+", dtype=np.float32, shape=(NT,)); c_ = np.load(f"{W}/ckpt_{x}.npy", mmap_mode="r")
        for i_ in range(0, NE, 20_000_000): j_ = min(i_ + 20_000_000, NE); a_[i_:j_] = c_[i_:j_]
        a_.flush(); del a_
    st = json.load(open(f"{W}/ckpt_state.json"))
    for f_ in (f"{W}/win.npz", f"{W}/xidx.npz"):
        if os.path.exists(f_): os.remove(f_)
if os.path.exists(f"{W}/B.npy") and st.get("done"):
    Bal = open_memmap(f"{W}/B.npy", mode="r+"); P = open_memmap(f"{W}/P.npy", mode="r+"); T = open_memmap(f"{W}/T.npy", mode="r+"); d = dt.date.fromisoformat(st["done"]) + dt.timedelta(days=1)
    if len(Bal) < NT: log("state arrays are the old size: run once with EA2_FROM_CKPT=1"); raise SystemExit
else:
    Bal, P, T = [open_memmap(f"{W}/{x}.npy", mode="w+", dtype=np.float32, shape=(NT,)) for x in ("B", "P", "T")]; d = dt.date(2009, 1, 3)
XF = f"{W}/xidx.npz"   # overflow index: addresses newer than the ats_v1 index -> overflow entity ids NE.. (union-find PAR)
if os.path.exists(XF):
    _x = np.load(XF); XK = _x["k"]; XE = _x["e"]; NX = int(_x["n"]); PAR = _x["par"]
else:
    XK = np.zeros(0, np.int64); XE = np.zeros(0, np.int64); NX = 0; PAR = np.zeros(0, np.int64)
def find(e):
    e = np.asarray(e, np.int64).copy()
    for _ in range(64):
        m_ = e >= NE
        if not m_.any(): break
        nx_ = PAR[e[m_] - NE]
        if (nx_ == e[m_]).all(): break
        e[m_] = nx_
    return e
px = pd.read_csv(f"{REPO}/data/onchain/self/urpd_metrics_daily.csv", index_col=0, parse_dates=True).close
pxd = {k.date(): float(v) for k, v in px.items()}
def price(day): return pxd.get(day, 0.05 if day >= dt.date(2010, 7, 18) else 0.0)
def read_day(ds):
    if not os.path.exists(f"{R}/inputs/date={ds}.parquet"): return None
    cc = duckdb.connect(); cc.execute("set threads=2")
    try:
        o = cc.execute(f"select tx, addr, sats from '{R}/outputs/date={ds}.parquet' where addr<>0").fetchnumpy()
        i = cc.execute(f"select tx, addr, sats from '{R}/inputs/date={ds}.parquet' where addr<>0").fetchnumpy()
    finally: cc.close()
    a = np.concatenate([o["addr"], i["addr"]]); v = np.concatenate([o["sats"], -i["sats"]]).astype(np.float64) / 1e8
    tx = np.concatenate([o["tx"], i["tx"]]); isin_ = np.concatenate([np.zeros(len(o["addr"]), bool), np.ones(len(i["addr"]), bool)])
    k = ids(a); m = k >= 0
    unk = None
    if dt.date.fromisoformat(ds) > IDX_END:   # rows of addresses newer than the index, resolved in the main thread
        mu = ~m & (np.abs(v) >= 0.001)
        ki = m & isin_   # known inputs (for co-spend links)
        unk = (a[mu], v[mu], tx[mu], isin_[mu], tx[ki], np.asarray(ENT[k[ki]]).astype(np.int64))
    if not m.any(): return np.zeros(0, np.int64), np.zeros(0), unk
    r = np.asarray(ENT[k[m]]).astype(np.int64); v = v[m]
    od = np.argsort(r, kind="stable"); r = r[od]; v = v[od]; u, s0 = np.unique(r, return_index=True)
    return u, np.add.reduceat(v, s0), unk
def resolve_unknown(unk):
    """new addresses -> entities: co-spent with an index address -> that entity; co-spent only with new ones -> one
    overflow entity; first-seen receivers -> fresh overflow entity. Updates XK/XE/PAR/NX."""
    global XK, XE, PAR, NX
    a, v, tx, isin_, ktx, kent = unk
    if not len(a): return np.zeros(0, np.int64), np.zeros(0)
    ua = np.unique(a)
    if len(XK):
        pos = np.searchsorted(XK, ua); have = (pos < len(XK)) & (XK[np.minimum(pos, len(XK) - 1)] == ua)
    else:
        have = np.zeros(len(ua), bool)
    new = ua[~have]
    if len(new):
        if NX + len(new) > CAPX: raise SystemExit("CAPX too small")
        ne = NE + NX + np.arange(len(new), dtype=np.int64); NX += len(new)
        PAR = np.concatenate([PAR, ne]); XK = np.concatenate([XK, new]); XE = np.concatenate([XE, ne])
        o_ = np.argsort(XK, kind="stable"); XK, XE = XK[o_], XE[o_]
    ent = find(XE[np.searchsorted(XK, a)])
    df = pd.DataFrame({"tx": tx[isin_], "e": ent[isin_]})
    if len(df):
        kn = pd.DataFrame({"tx": ktx, "e": kent}).drop_duplicates("tx").set_index("tx").e.rename("e_known")
        g = df.groupby("tx").e.min().rename("e_new").to_frame().join(kn, how="left")
        g["target"] = g.e_known.fillna(g.e_new).astype(np.int64)
        lk = df.join(g.target, on="tx"); lk = lk[lk.e != lk.target].drop_duplicates()
        for e_, t_ in zip(lk.e.values, lk.target.values):
            e_, t_ = int(find([e_])[0]), int(find([t_])[0])
            if e_ == t_ or e_ < NE: continue
            be, bt = float(Bal[e_]), float(Bal[t_])
            if be > 0:
                nb = be + bt; P[t_] = (float(P[t_]) * bt + float(P[e_]) * be) / nb; T[t_] = (float(T[t_]) * bt + float(T[e_]) * be) / nb
                Bal[t_] = nb; Bal[e_] = 0
            PAR[e_ - NE] = t_
        ent = find(XE[np.searchsorted(XK, a)])
    return ent, v
NBIN = 150; BIG = 10.0
def dayno(day): return (day - dt.date(2009, 1, 3)).days
def snapshot(day):
    dn = dayno(day)
    nz = np.concatenate([np.flatnonzero(Bal[a:a+20_000_000] > 1e-8) + a for a in range(0, NT, 20_000_000)])
    b = Bal[nz].astype(np.float64); p = P[nz].astype(np.float64); t = T[nz].astype(np.float64)
    big = b >= BIG; sm = ~big; sth = (dn - t) < 155; bi = np.minimum(p // 1000, NBIN - 1).astype(np.int64)
    np.savez(f"{W}/snap/{day}.npz", ids=nz[big].astype(np.int32), B=b[big].astype(np.float32), P=p[big].astype(np.float32), T=t[big].astype(np.float32),
             h_small=np.bincount(bi[sm], weights=b[sm], minlength=NBIN), h_small_sth=np.bincount(bi[sm & sth], weights=b[sm & sth], minlength=NBIN),
             small=np.array([b[sm & sth].sum(), b[sm & ~sth].sum(), (b[sm & sth] * p[sm & sth]).sum()]), price=np.array(price(day)), dn=np.array(dn))
WIN = deque(maxlen=30); WF = f"{W}/win.npz"
if os.path.exists(WF):
    _z = np.load(WF); WIN.extend((_z[f"u{i}"], _z[f"n{i}"]) for i in range(int(_z["k"])))
AGG = [np.zeros(0, np.int64), np.zeros(0), np.zeros(0, np.int32)]
def agg_add(u_, n_, sgn):
    U_, D_, C_ = AGG; pos = np.searchsorted(U_, u_); hit = (pos < len(U_)) & (U_[np.minimum(pos, len(U_) - 1)] == u_) if len(U_) else np.zeros(len(u_), bool)
    D_[pos[hit]] += sgn * n_[hit]; C_[pos[hit]] += sgn
    if (~hit).any():
        nu, nn = u_[~hit], n_[~hit]; ip = pos[~hit]
        U_ = np.insert(U_, ip, nu); D_ = np.insert(D_, ip, nn); C_ = np.insert(C_, ip, 1)
    keep = C_ > 0
    AGG[:] = [U_[keep], D_[keep], C_[keep]] if not keep.all() else [U_, D_, C_]
for _u, _n in list(WIN): agg_add(_u, _n, +1)
def save_state():
    global act_rows
    Bal.flush(); P.flush(); T.flush()
    if act_rows: open(f"{W}/active.csv", "a").write("".join(act_rows)); act_rows = []
    dd = {"k": np.array(len(WIN))}
    for i, (a_, b_) in enumerate(WIN): dd[f"u{i}"] = a_; dd[f"n{i}"] = b_
    np.savez(WF + ".tmp.npz", **dd); os.replace(WF + ".tmp.npz", WF)
    np.savez(XF + ".tmp.npz", k=XK, e=XE, n=np.array(NX), par=PAR); os.replace(XF + ".tmp.npz", XF); json.dump(st, open(SF + ".tmp", "w")); os.replace(SF + ".tmp", SF)
act_rows = []   # 11-03: active entities per day (entities with a non-zero net), for the board's 活跃实体 card
pool = ThreadPoolExecutor(int(os.environ.get("EA_THREADS", 8))); q = {}; n = 0
while d <= END and time.time() - T0 < BUDGET:
    for k in range(12):
        x = d + dt.timedelta(days=k)
        if x <= END and x.isoformat() not in q: q[x.isoformat()] = pool.submit(read_day, x.isoformat())
    got = q.pop(d.isoformat()).result(); dn = dayno(d)
    if got is not None and got[2] is not None:
        eu, vu = resolve_unknown(got[2])
        if len(eu):
            uu_ = np.concatenate([got[0], eu]); vv_ = np.concatenate([got[1], vu]); o_ = np.argsort(uu_, kind="stable"); uu_, vv_ = uu_[o_], vv_[o_]
            u0, s0_ = np.unique(uu_, return_index=True); got = (u0, np.add.reduceat(vv_, s0_), None)
    if got is not None and len(got[0]):
        u, net = got[0], got[1]
        act_rows.append(f"{d},{int((u < NE).sum() + ((u >= NE) & (np.abs(net) >= 0.1)).sum())}\n")   # 11-03: like ats_v1 (index = addresses that got >= 0.1 BTC): indexed entities + new-address entities moving >= 0.1 BTC
        b0 = Bal[u].astype(np.float64); p0 = P[u].astype(np.float64); t0 = T[u].astype(np.float64); pr = price(d)
        buy = net > 0; nb = b0 + net
        P[u[buy]] = ((p0[buy] * b0[buy] + net[buy] * pr) / nb[buy]).astype(np.float32)
        T[u[buy]] = ((t0[buy] * b0[buy] + net[buy] * dn) / nb[buy]).astype(np.float32)
        Bal[u] = np.maximum(nb, 0).astype(np.float32)
        if d >= dt.date(2016, 1, 1):
            sell = np.minimum(np.maximum(-net, 0), b0); o5 = (sell > 0) & (dn - t0 >= 1826)
            if o5.any(): np.savez(f"{W}/old/{d}.npz", ids=u[o5].astype(np.int32), sell=sell[o5].astype(np.float32), age=(dn - t0[o5]).astype(np.float32))
        if d >= dt.date(2024, 9, 1):
            if len(WIN) == WIN.maxlen: agg_add(*WIN[0], -1)
            WIN.append((u, net)); agg_add(u, net, +1)
    if d >= dt.date(2024, 11, 1) and len(AGG[0]):
        U2, D2 = AGG[0], AGG[1]; Be = Bal[U2].astype(np.float64); Bs = np.maximum(Be - D2, 0); bg = np.maximum(Be, Bs) >= BIG
        gb = np.maximum(D2, 0); gs = np.minimum(np.maximum(-D2, 0), Be)
        np.savez(f"{W}/ats/{d}.npz", ids=U2[bg].astype(np.int32), D=D2[bg].astype(np.float32), Be=Be[bg].astype(np.float32), small=np.array([gb[~bg].sum(), gs[~bg].sum()]))
    st["done"] = d.isoformat(); n += 1
    if d == dt.date(2024, 8, 31):
        save_state()
        for x in ("B", "P", "T"): shutil.copy(f"{W}/{x}.npy", f"{W}/ckpt_{x}.npy")
        shutil.copy(SF, f"{W}/ckpt_state.json"); log("checkpoint 2024-08-31")
    if (d >= dt.date(2016, 1, 1) and d.weekday() == 6) or d >= dt.date(2026, 4, 1) or (d.day == 1 and d.month in (1, 4, 7, 10) and d.year >= 2012): snapshot(d)
    if n % 200 == 0 or (d > IDX_END and n % 5 == 0): save_state(); log("at", d, "overflow entities", NX)
    d += dt.timedelta(days=1)
save_state()
if d > END:
    if not os.path.exists(f"{W}/snap/{END}.npz"): snapshot(END)
    log("done to", END)
    import subprocess, sys; subprocess.run([sys.executable, os.path.join(REPO, "scripts", "chain", "ea2_post.py")])
else:
    log("paused at", st["done"])
