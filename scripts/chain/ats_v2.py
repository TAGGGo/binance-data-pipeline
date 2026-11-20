"""ATS v2 (Avalon 11-01: v1 did not match Glassnode/貝格 — often the opposite — "得解决").
Reuses ats_v1's address index + multi-input clusters (ats_v1/addrs_b*.npy, parent.npy, read-only) and replays flows from
genesis again with two changes:
 1. Glassnode's ATS is per entity over the LAST MONTH: participation (balance/supply) x balance change (capped +-100%)
    = clip(net_30d, -bal_30d_ago, +bal_30d_ago) / supply.  v1 summed DAILY clipped buys/sells, which counts every
    in-and-out of busy services (a custodian moving coins around looks like huge buying AND selling).
    Here: net per entity over a rolling 30 days (ring buffer of daily per-entity deltas), clipped by the balance 30
    days earlier; buys = sum of positive, sells = sum of negative.
 2. Exchange exclusion also uses our newer labels: OKX / Binance proof-of-reserve lists + the exchange clusters
    (labels/btc_por_*.csv, labels/btc_exch_cluster_v1.parquet) on top of DefiLlama/GraphSense.
Variants: L = drop exchanges + miners; M = also drop clusters with >= 10,000 addresses.
Score per day: x = buys - sells (BTC), min-max over the last 90 days -> 0..1 (1 = strongest accumulation), like v0.
Resumable (state ats_v2/state.json + bal.npy + ring.npz), one run at a time (lock), budget ATS_V2_BUDGET seconds.
Outputs: ats_v2/daily.csv, ats_v2/score.csv -> data/onchain/ats_v2_score.csv"""
import os, sys, json, time, shutil, fcntl, datetime as dt
import numpy as np, duckdb
T0 = time.time(); BUDGET = float(os.environ.get('ATS_V2_BUDGET', 1500))
C = os.environ.get('MDH_CHAIN_DIR', '/Volumes/chain/datahub/chain')
if not os.path.isdir(C):
    print('ats_v2: chain drive not found; skip'); sys.exit(0)
R = f'{C}/raw'; V1 = f'{C}/ats_v1'; O = f'{C}/ats_v2'; os.makedirs(O, exist_ok=True)
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
_LK = open(f'{O}/run.lock', 'w')
try:
    fcntl.flock(_LK, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    print('ats_v2: another run is going; skip'); sys.exit(0)
v1 = json.load(open(f'{V1}/state.json'))
if v1.get('stage') != 'live':
    print('ats_v2: waiting for ats_v1 clusters'); sys.exit(0)
SF = f'{O}/state.json'; st = json.load(open(SF)) if os.path.exists(SF) else {}
def save(): json.dump(st, open(SF + '.tmp', 'w')); os.replace(SF + '.tmp', SF)
def left(): return BUDGET - (time.time() - T0)
def log(*a): print('ats_v2:', *a, flush=True)
NB = 16
B = [np.load(f'{V1}/addrs_b{b:02d}.npy', mmap_mode='r') for b in range(NB)]
OFF = np.cumsum([0] + [len(x) for x in B]); N = int(OFF[-1])
def ids(addr):
    addr = np.asarray(addr, np.int64); out = np.full(len(addr), -1, np.int64); bk = addr & (NB - 1)
    for b in range(NB):
        m = np.nonzero(bk == b)[0]
        if len(m) and len(B[b]):
            pos = np.minimum(np.searchsorted(B[b], addr[m]), len(B[b]) - 1)
            ok = B[b][pos] == addr[m]; out[m[ok]] = OFF[b] + pos[ok]
    return out
parent = np.load(f'{V1}/parent.npy', mmap_mode='r')
# ---- exclusion flags (bit 1 = L, bit 2 = M)
ff = f'{O}/excl.npy'
if not os.path.exists(ff):
    excl = np.zeros(N, np.uint8)
    for f in ('flag_exchange', 'flag_miner'):
        excl[np.load(f'{V1}/{f}.npy')] |= 3
    excl[np.load(f'{V1}/flag_big.npy')] |= 2
    lab = []
    for f in ('btc_por_okx.csv', 'btc_por_binance.csv'):
        p = f'{C}/labels/{f}'
        if os.path.exists(p):
            lab.append(duckdb.sql(f"select addr from read_csv_auto('{p}')").fetchnumpy()['addr'].astype(np.int64))
    p = f'{C}/labels/btc_exch_cluster_v1.parquet'
    if os.path.exists(p):
        lab.append(duckdb.sql(f"select addr from '{p}'").fetchnumpy()['addr'].astype(np.int64))
    k = ids(np.concatenate(lab)); roots = np.unique(np.asarray(parent[k[k >= 0]]))
    excl[roots] |= 3
    np.save(ff, excl); log(f'flags: +{len(roots):,} exchange roots from PoR / exchange clusters; excluded L {int((excl & 1).sum()):,}')
excl = np.load(ff)
def last_settled():
    today = dt.datetime.utcnow().date(); y = today - dt.timedelta(days=1)
    yf = f'{R}/inputs/date={y}.parquet'
    settle = dt.datetime.combine(today, dt.time(2, 0), tzinfo=dt.timezone.utc).timestamp()
    return y if os.path.exists(yf) and os.path.getmtime(yf) >= settle else y - dt.timedelta(days=1)
# ---- flows with a 30-day ring buffer of per-entity deltas
bf = f'{O}/bal.npy'; rf = f'{O}/ring.npz'; out = f'{O}/daily.csv'
bal = np.load(bf) if os.path.exists(bf) else np.zeros(N, np.float64)
ring = []
if os.path.exists(rf):
    z = np.load(rf); ring = [(z[f'u{i}'], z[f'v{i}']) for i in range(int(z['n']))]
if not os.path.exists(out):
    open(out, 'w').write('d,n_ent,buy_L,sell_L,buy_M,sell_M,net_L,net_M\n')
d = dt.date.fromisoformat(st['flows_done']) + dt.timedelta(days=1) if st.get('flows_done') else dt.date(2009, 1, 3)
E = last_settled(); rows = []
CHECKPOINTS = {'2023-12-31', '2024-12-31'}
from concurrent.futures import ThreadPoolExecutor
def read_day(ds):
    if not os.path.exists(f'{R}/inputs/date={ds}.parquet'):
        return None
    cc = duckdb.connect(); cc.execute('set threads=2')
    try:
        o_ = cc.execute(f"select addr, sats from '{R}/outputs/date={ds}.parquet' where addr<>0").fetchnumpy()
        i_ = cc.execute(f"select addr, sats from '{R}/inputs/date={ds}.parquet' where addr<>0").fetchnumpy()
    finally:
        cc.close()
    return o_, i_
pool = ThreadPoolExecutor(3); q = {}
def persist():
    np.save(bf + '.tmp.npy', bal); os.replace(bf + '.tmp.npy', bf)
    kw = {'n': np.array(len(ring))}
    for i, (u, v) in enumerate(ring):
        kw[f'u{i}'] = u; kw[f'v{i}'] = v
    np.savez(rf + '.tmp.npz', **kw); os.replace(rf + '.tmp.npz', rf); save()
while d <= E and left() > 90:
    for k_ in range(4):
        x_ = d + dt.timedelta(days=k_)
        if x_ <= E and x_.isoformat() not in q:
            q[x_.isoformat()] = pool.submit(read_day, x_.isoformat())
    ds = d.isoformat(); got = q.pop(ds).result()
    u = np.zeros(0, np.int64); net = np.zeros(0)
    if got is not None:
        o, i = got
        a = np.concatenate([o['addr'], i['addr']]); v = np.concatenate([o['sats'], -i['sats']]).astype(np.float64) / 1e8
        k = ids(a); m = k >= 0
        if m.any():
            r = np.asarray(parent[k[m]]).astype(np.int64); v = v[m]; od = np.argsort(r, kind='stable'); r = r[od]; v = v[od]
            u, s0 = np.unique(r, return_index=True); net = np.add.reduceat(v, s0)
            bal[u] += net
    ring.append((u, net))
    if len(ring) > 30:
        ring.pop(0)
    U = np.concatenate([x for x, _ in ring]); V = np.concatenate([y for _, y in ring])
    if len(U):
        od = np.argsort(U, kind='stable'); U = U[od]; V = V[od]
        uq, s0 = np.unique(U, return_index=True); n30 = np.add.reduceat(V, s0)
        b0 = np.maximum(bal[uq] - n30, 0.0)                 # balance 30 days ago
        cl = np.clip(n30, -b0, b0)
        vals = []
        for bit in (1, 2):
            keep = (excl[uq] & bit) == 0; c_ = cl[keep]; vals += [c_[c_ > 0].sum(), -c_[c_ < 0].sum()]
        nets = [n30[(excl[uq] & bit) == 0].sum() for bit in (1, 2)]
        rows.append(f'{ds},{len(uq)},' + ','.join(f'{x:.2f}' for x in vals + nets) + '\n')
    else:
        rows.append(f'{ds},0,0,0,0,0,0,0\n')
    st['flows_done'] = ds; d += dt.timedelta(days=1)
    if ds in CHECKPOINTS:   # 11-01: snapshot so a later change in labels / scoring only replays from here (~1 h, not 4-5 h)
        open(out, 'a').write(''.join(rows)); rows = []; persist()
        cp = f'{O}/ckpt_{ds}'; os.makedirs(cp, exist_ok=True)
        for f_ in ('bal.npy', 'ring.npz', 'state.json', 'daily.csv'):
            shutil.copyfile(f'{O}/{f_}', f'{cp}/{f_}')
        log(f'checkpoint {ds} saved')
    if len(rows) >= 200:
        open(out, 'a').write(''.join(rows)); rows = []; persist()
open(out, 'a').write(''.join(rows)); persist()
log(f'flows: to {st.get("flows_done")}')
if d <= E:
    sys.exit(0)
# ---- score
import csv
rr = list(csv.DictReader(open(out)))
res = ['d,score_L,score_M\n']
def mm(x):
    s = np.full(len(x), np.nan)
    for j in range(119, len(x)):
        w = x[j - 89:j + 1]; lo, hi = w.min(), w.max()
        s[j] = (x[j] - lo) / (hi - lo) if hi > lo else 0.5
    return s
SL = mm(np.array([float(r['buy_L']) - float(r['sell_L']) for r in rr]))
SM = mm(np.array([float(r['buy_M']) - float(r['sell_M']) for r in rr]))
for r, a_, b_ in zip(rr, SL, SM):
    if not np.isnan(a_):
        res.append(f"{r['d']},{a_:.4f},{b_:.4f}\n")
open(f'{O}/score.csv', 'w').write(''.join(res))
shutil.copyfile(f'{O}/score.csv', os.path.join(REPO, 'data', 'onchain', 'ats_v2_score.csv'))
log(f'score: {len(res) - 1} days, latest {res[-1].strip()}')
