"""Fast-test kit for accumulation-score methods (Avalon 11-01: "每次改方法都要四五个小时? 要怎么样能快速检验? 同时跑多个方法?").
Builds once (about 1-2 h), then any scoring / exclusion / extra-merging variant runs on it in minutes:
  ats_kit/addr_days/YYYY-MM.npz : per day, per address (ats_v1 index id, int32) net BTC change (float32), from KIT_START
  ats_kit/bal0.npy              : entity balance (ats_v1 clusters) at the day before KIT_START, float32
                                  = ats_v1 bal.npy (live, now) minus all window changes
Resumable by month; lock ats_kit/run.lock; budget ATS_KIT_BUDGET seconds. Runner: scripts/chain/ats_lab.py."""
import os, sys, json, time, fcntl, glob, datetime as dt
import numpy as np, duckdb
T0 = time.time(); BUDGET = float(os.environ.get('ATS_KIT_BUDGET', 1500))
C = os.environ.get('MDH_CHAIN_DIR', '/Volumes/chain/datahub/chain')
if not os.path.isdir(C): print('ats_kit: no chain drive'); sys.exit(0)
R = f'{C}/raw'; V1 = f'{C}/ats_v1'; K = f'{C}/ats_kit'; os.makedirs(f'{K}/addr_days', exist_ok=True)
START = dt.date.fromisoformat(os.environ.get('KIT_START', '2024-10-01'))
lk = open(f'{K}/run.lock', 'w')
try: fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError: print('ats_kit: another run is going; skip'); sys.exit(0)
def log(*a): print('ats_kit:', *a, flush=True)
def run_lab():
    import subprocess
    lab = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ats_lab.py')
    r = subprocess.run([sys.executable, lab], capture_output=True, text=True)
    for line in (r.stdout + r.stderr).strip().splitlines()[-40:]:
        log('lab:', line)
if os.path.exists(f'{K}/done.json'):
    # kit built: run the variant lab when asked (touch ats_kit/rerun_lab from anywhere, e.g. the Cowork VM)
    if os.path.exists(f'{K}/rerun_lab'):
        os.remove(f'{K}/rerun_lab'); run_lab()
    sys.exit(0)
v1 = json.load(open(f'{V1}/state.json'))
if v1.get('stage') != 'live': log('waiting for ats_v1'); sys.exit(0)
END = dt.date.fromisoformat(v1['flows_done'])          # the v1 balance below is as of this day
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
def read_day(ds):
    if not os.path.exists(f'{R}/inputs/date={ds}.parquet'): return None
    cc = duckdb.connect(); cc.execute('set threads=2')
    try:
        o = cc.execute(f"select addr, sats from '{R}/outputs/date={ds}.parquet' where addr<>0").fetchnumpy()
        i = cc.execute(f"select addr, sats from '{R}/inputs/date={ds}.parquet' where addr<>0").fetchnumpy()
    finally: cc.close()
    return o, i
from concurrent.futures import ThreadPoolExecutor
pool = ThreadPoolExecutor(3)
m0 = dt.date(START.year, START.month, 1)
while m0 <= END and time.time() - T0 < BUDGET - 300:
    m1 = (m0.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    f = f'{K}/addr_days/{m0:%Y-%m}.npz'
    full = m1 <= END + dt.timedelta(days=1)
    if os.path.exists(f) and full:
        m0 = m1; continue
    days = [m0 + dt.timedelta(days=k) for k in range((min(m1, END + dt.timedelta(days=1)) - m0).days)]
    days = [x for x in days if x >= START]
    futs = {x: pool.submit(read_day, x.isoformat()) for x in days}
    kw = {}
    for x in days:
        got = futs[x].result()
        if got is None: continue
        o, i = got
        a = np.concatenate([o['addr'], i['addr']]); v = np.concatenate([o['sats'], -i['sats']]).astype(np.float64) / 1e8
        k = ids(a); mm = k >= 0; k = k[mm]; v = v[mm]
        od = np.argsort(k, kind='stable'); k = k[od]; v = v[od]
        u, s0 = np.unique(k, return_index=True); net = np.add.reduceat(v, s0) if len(u) else np.zeros(0)
        nz = net != 0
        kw[f'i{x:%d}'] = u[nz].astype(np.int32); kw[f'v{x:%d}'] = net[nz].astype(np.float32)
    np.savez(f + '.tmp.npz', **kw); os.replace(f + '.tmp.npz', f)
    log(f'{m0:%Y-%m}: {len(days)} days')
    m0 = m1
if m0 <= END:
    sys.exit(0)
# entity balance before START = v1 balance at END - every window change (mapped to v1 clusters)
parent = np.load(f'{V1}/parent.npy', mmap_mode='r')
bal = np.load(f'{V1}/bal.npy').astype(np.float64)
for f in sorted(glob.glob(f'{K}/addr_days/*.npz')):
    z = np.load(f)
    for key in z.files:
        if key[0] == 'i':
            u = z[key]; v = z['v' + key[1:]].astype(np.float64)
            np.subtract.at(bal, np.asarray(parent[u]).astype(np.int64), v)
np.save(f'{K}/bal0.npy', bal.astype(np.float32))
json.dump({'start': START.isoformat(), 'end': END.isoformat(), 'built_utc': dt.datetime.utcnow().isoformat()[:16]}, open(f'{K}/done.json', 'w'))
log(f'kit done: {START}..{END}; bal0 sum {bal[bal > 0].sum():,.0f} BTC')
del bal
run_lab()
