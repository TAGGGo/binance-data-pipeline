"""ATS v1: Glassnode-style Accumulation Trend Score from our own raw chain data (Avalon 10-30).

Why v1: v0 tracked balances only from 2025-07, so old holders looked like they had 0 BTC; their buys were
clipped to ~0 while their sells counted in full (the score came out roughly inverted vs Glassnode). v1 replays
the whole chain from the genesis block, so every balance is exact.

Glassnode method (docs.glassnode.com, metric guide "Accumulation Trend Score"):
  per entity per day: participation = balance / supply; change = delta / balance, capped to +-100%
  ratio = participation * change  ==  clip(delta, -balance, +balance) / supply
  -> daily buys = sum min(delta, balance_before) over buying entities, sells = sum -delta over selling entities
  30-day sums; each min-max scaled over the last 90 days; score = buys_scaled - sells_scaled.
  Exchanges and miners are excluded.

Scope choices (v1):
  * address universe = addresses that received >= 0.1 BTC in total over their life (about 10% of addresses,
    >99.9% of the value); smaller ones are ignored (they barely move a balance-weighted score).
  * entity = multi-input clusters (CoinJoin-like tx skipped); no change-address heuristic yet.
  * exchanges = DefiLlama CEX + GraphSense seed labels; miners = coinbase recipients. Variant M also drops
    clusters with >= 10,000 addresses (likely services we have no label for).

Stages (state in ats_v1/state.json, resumable, stops after ATS_V1_BUDGET seconds, default 1500):
  wait (raw must be complete from 2009-01-03) -> index (16 buckets) -> cluster (day by day) -> flags ->
  flows (day by day, exact balances) -> score. After the first full build, every run extends cluster + flows +
  score with new settled days. Rebuild: delete ats_v1/ (the raw data is untouched).
Outputs: ats_v1/daily.csv, ats_v1/score.csv; copy to data/onchain/ats_v1_score.csv for the dashboards.
Runs natively on the Mac from scripts/ops/run_update.sh (needs ~3-4 GB RAM; the Cowork VM has too little).
"""
import os, sys, json, time, shutil, datetime as dt
import numpy as np, duckdb

T0 = time.time(); BUDGET = float(os.environ.get('ATS_V1_BUDGET', 1500))
C = os.environ.get('MDH_CHAIN_DIR', '/Volumes/chain/datahub/chain')
if not os.path.isdir(C):
    print('ats_v1: chain drive not found; skip'); sys.exit(0)
R = f'{C}/raw'; O = os.environ.get('ATS_V1_DIR', f'{C}/ats_v1'); os.makedirs(O, exist_ok=True)
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
GENESIS = dt.date.fromisoformat(os.environ.get('ATS_V1_START', '2009-01-03'));  # override only for tests
MIN_SATS = int(float(os.environ.get('ATS_V1_MIN_BTC', 0.1)) * 1e8); NB = 16
SF = f'{O}/state.json'
import fcntl   # one run at a time (11-01: a long detached run + the hourly call must not both write state/bal)
_LK = open(f'{O}/run.lock', 'w')
try:
    fcntl.flock(_LK, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    print('ats_v1: another run is going; skip'); sys.exit(0)
st = json.load(open(SF)) if os.path.exists(SF) else {'stage': 'wait'}
def save(): json.dump(st, open(SF + '.tmp', 'w')); os.replace(SF + '.tmp', SF)
def left(): return BUDGET - (time.time() - T0)
def log(*a): print('ats_v1:', *a, flush=True)
def con():
    c = duckdb.connect(); c.execute('set threads=4')
    c.execute(f"set memory_limit='{os.environ.get('ATS_V1_MEM', '4GB')}'")
    os.makedirs(f'{O}/tmp', exist_ok=True); c.execute(f"set temp_directory='{O}/tmp'")
    return c
def have_days():
    return {p.name[5:15] for p in os.scandir(f'{R}/inputs') if p.name.startswith('date=')}
def last_settled():
    """newest day whose raw copy is final (aws_backfill re-pulls yesterday until it is settled)."""
    today = dt.datetime.utcnow().date(); y = today - dt.timedelta(days=1)
    yf = f'{R}/inputs/date={y}.parquet'
    settle = dt.datetime.combine(today, dt.time(2, 0), tzinfo=dt.timezone.utc).timestamp()
    return y if os.path.exists(yf) and os.path.getmtime(yf) >= settle else y - dt.timedelta(days=1)

# ---------------------------------------------------------------- wait: raw complete back to genesis
if st['stage'] == 'wait':
    days = have_days(); end = last_settled()
    need = (end - GENESIS).days + 1
    miss = [d for d in (GENESIS + dt.timedelta(n) for n in range(need)) if d.isoformat() not in days]
    miss = [d for d in miss if d >= dt.date(2009, 1, 10)]   # 2009-01-04..09 had no blocks at all
    if miss:
        log(f'waiting for raw backfill: {len(miss)} of {need} days missing (oldest missing {miss[-1]}, newest missing {miss[0]})'); sys.exit(0)
    st.update(stage='index', index_upto=end.isoformat()); save(); log('raw complete; building address index')

# ---------------------------------------------------------------- index: addresses with lifetime received >= MIN
if st['stage'] == 'index':
    c = con(); upto = st['index_upto']
    for b in range(NB):
        f = f'{O}/addrs_b{b:02d}.npy'
        if os.path.exists(f): continue
        if left() < min(600, BUDGET / 3): log(f'index: stopping before bucket {b} (budget)'); sys.exit(0)
        t = time.time()
        a = c.execute(f"""select addr from read_parquet('{R}/outputs/*.parquet', hive_partitioning=false, filename=true)
                          where addr<>0 and (addr & {NB-1})={b} and filename <= '{R}/outputs/date={upto}.parquet'
                            and filename >= '{R}/outputs/date={GENESIS}.parquet'
                          group by addr having sum(sats) >= {MIN_SATS} order by addr""").fetchnumpy()['addr']
        np.save(f + '.tmp.npy', a.astype(np.int64)); os.replace(f + '.tmp.npy', f)
        log(f'index: bucket {b} {len(a):,} addresses in {time.time()-t:.0f}s')
    st.update(stage='cluster'); save()

B = [np.load(f'{O}/addrs_b{b:02d}.npy', mmap_mode='r') for b in range(NB)]
OFF = np.cumsum([0] + [len(x) for x in B]); N = int(OFF[-1])
def ids(addr):
    """addr hashes -> dense ids (-1 = not in the universe)."""
    addr = np.asarray(addr, np.int64); out = np.full(len(addr), -1, np.int64); bk = addr & (NB - 1)
    for b in range(NB):
        m = np.nonzero(bk == b)[0]
        if len(m) and len(B[b]):
            pos = np.minimum(np.searchsorted(B[b], addr[m]), len(B[b]) - 1)
            ok = B[b][pos] == addr[m]; out[m[ok]] = OFF[b] + pos[ok]
    return out
def days_from(key, start):
    d = dt.date.fromisoformat(st[key]) + dt.timedelta(days=1) if st.get(key) else start
    return d, last_settled()

# ---------------------------------------------------------------- cluster: multi-input heuristic, genesis -> now
def cluster():
    pf = f'{O}/parent.npy'
    parent = np.load(pf) if os.path.exists(pf) else np.arange(N, dtype=np.int32)
    rootmap = np.arange(N, dtype=np.int32)
    bf = f'{O}/bal.npy'; bal = np.load(bf) if st['stage'] == 'live' and os.path.exists(bf) else None
    c = con(); d, E = days_from('cluster_done', GENESIS); n = 0; edges = 0
    while d <= E and left() > 120:
        ds = d.isoformat()
        if not os.path.exists(f'{R}/inputs/date={ds}.parquet'):
            st['cluster_done'] = ds; d += dt.timedelta(days=1); continue
        e = c.execute(f"""
          with cj as (select tx from (select tx, sats, count(*) k from '{R}/outputs/date={ds}.parquet' where sats>0 group by 1,2) group by tx having max(k)>=3),
          ins as (select distinct tx, addr from '{R}/inputs/date={ds}.parquet' where addr<>0 and tx not in (select tx from cj))
          select tx, addr from ins""").fetchnumpy()
        if len(e['tx']):
            k = ids(e['addr']); m = k >= 0; tx = e['tx'][m]; k = k[m]
            if len(k):
                o = np.lexsort((k, tx)); tx = tx[o]; k = k[o]
                first = np.r_[True, tx[1:] != tx[:-1]]
                grp = np.cumsum(first) - 1; root = k[first][grp]      # link every input address of a tx to the tx's first one
                a = parent[k[~first]]; b = parent[root[~first]]; keep = a != b; a, b = a[keep], b[keep]
                if len(a):
                    u = np.unique(np.concatenate([a, b])); ia = np.searchsorted(u, a); ib = np.searchsorted(u, b)
                    lab = np.arange(len(u))
                    while True:
                        old = lab.copy(); mm = np.minimum(lab[ia], lab[ib])
                        np.minimum.at(lab, ia, mm); np.minimum.at(lab, ib, mm)
                        while True:
                            l2 = lab[lab]
                            if np.array_equal(l2, lab): break
                            lab = l2
                        if np.array_equal(lab, old): break
                    newroot = u[lab].astype(np.int32); ch = newroot != u
                    if ch.any():
                        rootmap[u[ch]] = newroot[ch]; parent = rootmap[parent]; rootmap[u[ch]] = u[ch]
                        if bal is not None:   # live mode: merged entities pool their balances
                            np.add.at(bal, newroot[ch], bal[u[ch]]); bal[u[ch]] = 0
                    edges += len(a)
        st['cluster_done'] = ds; n += 1; d += dt.timedelta(days=1)
    np.save(pf + '.tmp.npy', parent); os.replace(pf + '.tmp.npy', pf)
    if bal is not None: np.save(bf + '.tmp.npy', bal); os.replace(bf + '.tmp.npy', bf)
    save()
    log(f'cluster: +{n} days to {st.get("cluster_done")}, {edges:,} merges, {N:,} addresses')
    return d > E

if st['stage'] == 'cluster':
    if not cluster(): sys.exit(0)
    st.update(stage='flags'); save()

# ---------------------------------------------------------------- flags: exchanges + miners (+ big clusters)
def flags():
    parent = np.load(f'{O}/parent.npy', mmap_mode='r')
    lab = []
    for f in ('btc_cex_defillama.csv', 'btc_entity_seeds_graphsense.csv'):
        p = f'{C}/labels/{f}'
        if os.path.exists(p):
            lab.append(duckdb.sql(f"select addr from read_csv_auto('{p}')").fetchnumpy()['addr'].astype(np.int64))
    k = ids(np.concatenate(lab) if lab else np.zeros(0, np.int64)); ex = np.unique(np.asarray(parent[k[k >= 0]]))
    c = con()
    mi = c.execute(f"""with t as (select tx, block from read_parquet('{R}/tx/*.parquet', filename=true)
                                  where coinbase and filename >= '{R}/tx/date={GENESIS}.parquet')
                       select distinct o.addr from read_parquet('{R}/outputs/*.parquet', filename=true) o join t on o.tx=t.tx and o.block=t.block
                       where o.addr<>0 and o.filename >= '{R}/outputs/date={GENESIS}.parquet'""").fetchnumpy()['addr']
    k = ids(mi); mn = np.unique(np.asarray(parent[k[k >= 0]]))
    size = np.bincount(np.asarray(parent), minlength=N)
    big = np.nonzero(size >= 10000)[0]
    np.save(f'{O}/flag_exchange.npy', ex); np.save(f'{O}/flag_miner.npy', mn); np.save(f'{O}/flag_big.npy', big)
    log(f'flags: {len(ex):,} exchange entities, {len(mn):,} miner entities, {len(big):,} clusters >= 10k addresses')

if st['stage'] == 'flags':
    flags(); st.update(stage='flows'); save()

# ---------------------------------------------------------------- flows: exact balances, Glassnode-style buys/sells
def flows():
    parent = np.load(f'{O}/parent.npy', mmap_mode='r')
    excl = np.zeros(N, np.uint8)
    for f in ('flag_exchange', 'flag_miner'):
        excl[np.load(f'{O}/{f}.npy')] |= 3          # bit1 variant L (exchanges + miners), bit2 variant M
    excl[np.load(f'{O}/flag_big.npy')] |= 2
    bf = f'{O}/bal.npy'; out = f'{O}/daily.csv'
    bal = np.load(bf) if os.path.exists(bf) else np.zeros(N, np.float64)
    if not os.path.exists(out):
        open(out, 'w').write('d,n_ent,buy_L,buyclip_L,sell_L,buy_M,buyclip_M,sell_M,supply_tracked\n')
    d, E = days_from('flows_done', GENESIS); rows = []
    # 11-01: read the next days' parquet files in background threads while this day is being processed
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
    while d <= E and left() > 60:
        for k_ in range(4):
            x_ = d + dt.timedelta(days=k_)
            if x_ <= E and x_.isoformat() not in q:
                q[x_.isoformat()] = pool.submit(read_day, x_.isoformat())
        ds = d.isoformat()
        got = q.pop(ds).result()
        if got is None:
            st['flows_done'] = ds; d += dt.timedelta(days=1); continue
        o, i = got
        a = np.concatenate([o['addr'], i['addr']]); v = np.concatenate([o['sats'], -i['sats']]).astype(np.float64) / 1e8
        k = ids(a); m = k >= 0
        if m.any():
            r = np.asarray(parent[k[m]]); v = v[m]; od = np.argsort(r, kind='stable'); r = r[od]; v = v[od]
            u, s0 = np.unique(r, return_index=True); net = np.add.reduceat(v, s0)
            before = np.maximum(bal[u], 0); bal[u] = bal[u] + net
            pos = np.maximum(net, 0); neg = np.maximum(-net, 0); clip = np.minimum(pos, before)
            vals = []
            for bit in (1, 2):
                keep = (excl[u] & bit) == 0; vals += [pos[keep].sum(), clip[keep].sum(), neg[keep].sum()]
            st['tracked'] = st.get('tracked', 0.0) + float(net.sum())
            rows.append(f'{ds},{len(u)},' + ','.join(f'{x:.2f}' for x in vals) + f",{st['tracked']:.0f}\n")
        else:
            rows.append(f'{ds},0,0,0,0,0,0,0,0\n')
        st['flows_done'] = ds; d += dt.timedelta(days=1)
        if len(rows) >= 200:
            open(out, 'a').write(''.join(rows)); rows = []
            np.save(bf + '.tmp.npy', bal); os.replace(bf + '.tmp.npy', bf); save()
    open(out, 'a').write(''.join(rows))
    np.save(bf + '.tmp.npy', bal); os.replace(bf + '.tmp.npy', bf); save()
    log(f'flows: to {st.get("flows_done")}')
    return d > E

# ---------------------------------------------------------------- score
def score():
    import csv
    rows = list(csv.DictReader(open(f'{O}/daily.csv')))
    out = ['d,score_L,score_M,ats_L,ats_M\n']
    def series(v):
        b = np.array([float(r['buyclip_' + v]) for r in rows]); s = np.array([float(r['sell_' + v]) for r in rows])
        cb = np.convolve(b, np.ones(30))[:len(b)]; cs = np.convolve(s, np.ones(30))[:len(s)]
        res = np.full(len(b), np.nan)
        for j in range(119, len(b)):
            wb = cb[j-89:j+1]; ws = cs[j-89:j+1]
            nb = (cb[j] - wb.min()) / (wb.max() - wb.min()) if wb.max() > wb.min() else 0.5
            ns = (cs[j] - ws.min()) / (ws.max() - ws.min()) if ws.max() > ws.min() else 0.5
            res[j] = nb - ns
        return res
    L, M = series('L'), series('M')
    for r, x, y in zip(rows, L, M):
        if not np.isnan(x):
            out.append(f"{r['d']},{x:.4f},{y:.4f},{(x+1)/2:.4f},{(y+1)/2:.4f}\n")   # ats_* = (score+1)/2, 0..1
    open(f'{O}/score.csv', 'w').write(''.join(out))
    dst = os.path.join(REPO, 'data', 'onchain', 'ats_v1_score.csv')
    if not os.environ.get('ATS_V1_DIR'): shutil.copyfile(f'{O}/score.csv', dst)
    log(f'score: {len(out)-1} days, latest {out[-1].strip() if len(out) > 1 else "-"}')

if st['stage'] == 'flows':
    if not flows(): sys.exit(0)
    st.update(stage='live'); save(); score()

if st['stage'] == 'live':   # keep up with new days
    end_s = last_settled().isoformat()
    if st.get('cluster_done') == end_s and st.get('flows_done') == end_s and os.path.exists(f'{O}/score.csv'):
        log(f'up to date ({end_s})'); sys.exit(0)
    if cluster() and flows(): score()
