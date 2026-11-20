"""Exchange wallet scanner (Avalon 11-03: "以后我们要怎么能找到新的地址？…自己的 scanning system 持续保持更新" -> "好的").
Daily, incremental, on ea2 entities (ats_v1 clusters + overflow entities of addresses newer than the index).
SHADOW by default: keeps candidates, applies nothing. EXCH_SCAN_APPLY=1 -> promoted entities are written to
<chain>/labels/auto_ents.csv (ent, exch, kind), which ea2_post reads (overflow ids resolved with ea2 xidx PAR).
Per day D (after the last scanned day, up to ea2 done):
  funding : tx with an input entity labeled exchange X (custody excluded) and an output >= 50 BTC to an UNLABELED entity
            -> candidate (ent, X): n_fund, v_fund, first day.
  behaviour: tx where a candidate of X is an input: value to X-labeled entities / other X candidates = back, else = out.
  migration: one tx from X inputs with >= 3 outputs of >= 100 BTC each to NEW (overflow) entities, amounts within 20%
            -> those candidates get mig=1 (Coinbase 2025-11 pattern: hundreds of equal sweeps into new wallets).
Promotion : mig -> exch_migr ; spent >= 25 % of funding and back share >= 0.9 -> exch_cold ; >= 50 fundings and back share >= 0.5\n            -> exch_warm ; n_fund >= 3, never spent, age >= 7 d -> exch_hold. labels/not_exch.csv (Tether …) never promoted.
Rejected  : out > 0 and back share < 0.5 -> client (a customer withdrawal).  Single funding + never spent stays candidate.
Also: day-over-day jump alarm on data/onchain/self/ea2_exch.csv (> 5 % for exchanges >= 5k BTC).
Files: data/research/exch_scan_cand.csv, data/state/exch_scan.json, log data/logs/exch_scan.log. EXCH_SCAN_FROM=YYYY-MM-DD restarts."""
import os, json, time, datetime as dt, numpy as np, pandas as pd, duckdb
T0 = time.time(); CH = os.environ.get("MDH_CHAIN_DIR", "/Volumes/chain/datahub/chain"); V1 = f"{CH}/ats_v1"; R = f"{CH}/raw"; W = f"{CH}/ea2"; WE = f"{CH}/ea_urpd"
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")); SF = f"{REPO}/data/state/exch_scan.json"; CF = f"{REPO}/data/research/exch_scan_cand.csv"
APPLY = os.environ.get("EXCH_SCAN_APPLY") == "1"; MINF, MIGV = 50.0, 100.0
def log(*x): print("exch_scan:", f"{time.time()-T0:.0f}s", *x, flush=True)
import fcntl; _lk = open(f"{REPO}/data/state/exch_scan.lock", "w")
try: fcntl.flock(_lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError: log("another run; skip"); raise SystemExit
if os.path.exists(SF) and not os.environ.get("EXCH_SCAN_FROM") and not os.environ.get("EXCH_SCAN_FORCE") and json.load(open(SF)).get("done") == json.load(open(f"{W}/state.json"))["done"]: raise SystemExit
NB = 16; B_ = [np.load(f"{V1}/addrs_b{b:02d}.npy", mmap_mode="r") for b in range(NB)]; OFF = np.cumsum([0] + [len(x) for x in B_])
def ids(addr):
    out = np.full(len(addr), -1, np.int64); bk = addr & (NB - 1)
    for b in range(NB):
        m = np.nonzero(bk == b)[0]
        if len(m) and len(B_[b]):
            pos = np.minimum(np.searchsorted(B_[b], addr[m]), len(B_[b]) - 1); ok = B_[b][pos] == addr[m]; out[m[ok]] = OFF[b] + pos[ok]
    return out
ENT = np.load(f"{WE}/ent.npy", mmap_mode="r"); NE = len(np.load(f"{WE}/root_of_ent.npy", mmap_mode="r"))
_x = np.load(f"{W}/xidx.npz"); XK = _x["k"]; XE = _x["e"]; PAR = _x["par"]
def find(e):
    e = np.asarray(e, np.int64).copy()
    for _ in range(64):
        m_ = e >= NE
        if not m_.any(): break
        nx_ = PAR[e[m_] - NE]
        if (nx_ == e[m_]).all(): break
        e[m_] = nx_
    return e
def ents(addr):
    addr = np.asarray(addr, np.int64); k = ids(addr); e = np.full(len(addr), -1, np.int64); m = k >= 0
    e[m] = np.asarray(ENT[k[m]]).astype(np.int64)
    if len(XK) and (~m).any():
        a = addr[~m]; pos = np.minimum(np.searchsorted(XK, a), len(XK) - 1); ok = XK[pos] == a
        ee = np.full(len(a), -1, np.int64); ee[ok] = find(XE[pos[ok]]); e[~m] = ee
    return e
# labels, same rules as ea2_post (+ auto_ents)
L = pd.read_parquet(f"{CH}/labels/exch_roots_v2.parquet")[["root", "exch", "kind"]]
xf = f"{CH}/labels/extra_roots.csv"
if os.path.exists(xf): L = pd.concat([pd.read_csv(xf)[["root", "exch", "kind"]], L])
L = L.drop_duplicates("root"); L["grp"] = L.exch.str.replace("krakenbtc", "kraken").str.replace("gatebtc", "gate")
L["ent"] = np.asarray(ENT[L.root.values.astype(np.int64)]).astype(np.int64)
cust = L.kind.astype(str).str.startswith("cold") & (L.grp == "coinbase") | (L.kind.astype(str) == "etf")
af = f"{CH}/labels/auto_ents.csv"
A = pd.read_csv(af) if os.path.exists(af) else pd.DataFrame(columns=["ent", "exch", "kind"])
if len(A): A["ent"] = find(A.ent.values)
EXG = pd.concat([L[~cust].drop_duplicates("ent").set_index("ent").grp, A.drop_duplicates("ent").set_index("ent").exch]); EXG = EXG[~EXG.index.duplicated()]
ALL = set(L.ent) | set(A.ent)
st = json.load(open(SF)) if os.path.exists(SF) else {}
done = dt.date.fromisoformat(json.load(open(f"{W}/state.json"))["done"])
d = dt.date.fromisoformat(os.environ["EXCH_SCAN_FROM"]) if os.environ.get("EXCH_SCAN_FROM") else (dt.date.fromisoformat(st["done"]) + dt.timedelta(days=1) if st.get("done") else done - dt.timedelta(days=6))
C = pd.read_csv(CF) if (os.path.exists(CF) and not os.environ.get("EXCH_SCAN_FROM")) else pd.DataFrame(columns=["ent", "exch", "first", "last", "n_fund", "v_fund", "back", "out", "mig"])
C = C.set_index("ent") if len(C) else C.set_index("ent")
while d <= done:
    ds = str(d); fo, fi = f"{R}/outputs/date={ds}.parquet", f"{R}/inputs/date={ds}.parquet"
    if not (os.path.exists(fo) and os.path.exists(fi)): d += dt.timedelta(days=1); continue
    td = time.time(); O = duckdb.sql(f"select tx, addr, sats from '{fo}' where addr<>0 and sats >= 1000000").df(); I = duckdb.sql(f"select tx, addr from '{fi}' where addr<>0").df()
    O["e"] = ents(O.addr.values); O["btc"] = O.sats / 1e8; I["e"] = ents(I.addr.values)
    tm = time.time()
    if len(C): C.index = find(C.index.values); C = C.groupby(level=0).agg({"exch": "first", "first": "min", "last": "max", "n_fund": "sum", "v_fund": "sum", "back": "sum", "out": "sum", "mig": "max"})
    I["x"] = I.e.map(EXG); I["c"] = I.e.map(C.exch) if len(C) else np.nan
    # funding from exchange X
    tx_x = I.dropna(subset=["x"]).drop_duplicates("tx").set_index("tx").x
    f = O[O.tx.isin(tx_x.index) & (O.e >= 0) & ~O.e.isin(ALL)].copy(); f["x"] = f.tx.map(tx_x)
    f = f[~f.e.isin(set(I[I.tx.isin(f.tx)].e))]   # change back to the sender is not a new wallet
    fe = f.groupby(["e", "x"]).agg(v=("btc", "sum"), n=("tx", "nunique")).reset_index(); fe = fe[fe.v >= MINF]
    mig = set()
    for tx, g in f[(f.btc >= MIGV) & (f.e >= NE)].groupby("tx"):
        if len(g) >= 3 and g.btc.max() <= 1.2 * g.btc.min(): mig |= set(g.e)
    fe = fe.drop_duplicates("e")
    nw = pd.DataFrame({"exch": fe.x.values, "first": ds, "last": ds, "n_fund": fe.n.values, "v_fund": fe.v.values, "back": 0.0, "out": 0.0,
                       "mig": fe.e.isin(mig).astype(int).values}, index=pd.Index(fe.e.values, name="ent"))
    C = pd.concat([C, nw]) if len(C) else nw
    C = C.groupby(level=0).agg({"exch": "first", "first": "min", "last": "max", "n_fund": "sum", "v_fund": "sum", "back": "sum", "out": "sum", "mig": "max"})
    tl = time.time()
    # behaviour of candidates
    if len(C):
        tx_c = I[I.e.isin(C.index)].drop_duplicates("tx").set_index("tx").e
        o = O[O.tx.isin(tx_c.index)].copy(); o["src"] = o.tx.map(tx_c); o = o[o.e != o.src]
        o["sx"] = o.src.map(C.exch); o["dx"] = o.e.map(EXG).fillna(o.e.map(C.exch))
        o["back"] = np.where(o.dx == o.sx, o.btc, 0.0); o["out"] = np.where(o.dx == o.sx, 0.0, o.btc)
        g = o.groupby("src")[["back", "out"]].sum()
        C.loc[g.index, "back"] += g.back.values; C.loc[g.index, "out"] += g.out.values
    log(ds, "fund-cands", len(fe), "mig", len(mig), "cands", len(C), "| io+map %.0fs, cand %.0fs" % (tm - td, time.time() - tl)); d += dt.timedelta(days=1)
# classify
if len(C):
    sh = C.back / (C.back + C.out).replace(0, np.nan); age = (done - pd.to_datetime(C["first"]).dt.date).apply(lambda x: x.days)
    sp = C.back + C.out; nf = f"{CH}/labels/not_exch.csv"   # known non-exchange entities (Tether, …): never promoted
    NX_ = set(find(pd.read_csv(nf).ent.values)) if os.path.exists(nf) else set()
    C["status"] = np.select([C.index.isin(NX_), C.mig == 1, (sp >= 0.25 * C.v_fund) & (sh >= 0.9), (C.n_fund >= 50) & (sh >= 0.5),
                             (C.n_fund >= 3) & (sp == 0) & (age >= 7), (sp > 0) & (sh < 0.5)],
                            ["not_exch", "exch_migr", "exch_cold", "exch_warm", "exch_hold", "client"], "pending")
    try: Bal = np.load(f"{W}/B.npy", mmap_mode="r"); C["bal"] = np.asarray(Bal[C.index.values]).astype(float)
    except Exception: C["bal"] = np.nan
    C.reset_index().rename(columns={"index": "ent"}).to_csv(CF, index=False)
    s = C.groupby(["exch", "status"]).agg(n=("bal", "size"), bal=("bal", "sum")).round(0); log("candidates by exchange/status:\n" + s.to_string())
    if APPLY:   # 11-03 Avalon "交易所新地址自动加": auto for migr/cold/warm under 10k BTC; exch_hold (could be a client's own
                # cold storage fed by regular withdrawals) and anything >= 10k BTC go to the weekly review list instead
        ok = C.status.isin(["exch_migr", "exch_cold", "exch_warm"]) & (C.bal.fillna(0) < 10000) & ~C.index.isin(set(A.ent))
        rv = C[(C.status.isin(["exch_migr", "exch_cold", "exch_warm"]) & (C.bal.fillna(0) >= 10000) | (C.status == "exch_hold") & (C.bal.fillna(0) >= 100)) & ~C.index.isin(set(A.ent))]
        rv.reset_index().rename(columns={"index": "ent"}).to_csv(f"{REPO}/data/research/exch_scan_review.csv", index=False)
        P_ = C[ok]; new = pd.DataFrame({"ent": P_.index.values, "exch": P_.exch.values, "kind": "auto_" + P_.status.values})
        if len(new):
            import shutil; shutil.copy(af, af + ".bak") if os.path.exists(af) else None
            A2 = pd.concat([A, new]).drop_duplicates("ent"); A2.to_csv(af, index=False)
            log("applied: +%d entities, %.0f BTC now:" % (len(new), P_.bal.sum()), P_.groupby("exch").bal.sum().round(0).to_dict())
            import subprocess, sys; subprocess.run([sys.executable, os.path.join(REPO, "scripts", "chain", "ea2_post.py")])
        log("review list (weekly, not applied): %d entities, %.0f BTC" % (len(rv), rv.bal.sum()))
json.dump({"done": str(done)}, open(SF, "w"))
# jump alarm
X = pd.read_csv(f"{REPO}/data/onchain/self/ea2_exch.csv").set_index("d")
if len(X) >= 2:
    a, b = X.iloc[-2], X.iloc[-1]; j = ((b - a) / a.replace(0, np.nan)); j = j[(a >= 5000) & (j.abs() > 0.05)]
    for k, v in j.items(): log("ALARM jump", X.index[-1], k, "%+.1f%%" % (v * 100), round(a[k]), "->", round(b[k]))
log("done", "APPLY" if APPLY else "SHADOW")
