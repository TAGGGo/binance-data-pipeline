"""Run many accumulation-score variants at once on the ats_kit window (minutes, not hours) and grade each against
貝格's Glassnode ATS chart (data/research/beggar_ats_ref.csv, colours read off his 10/28 picture, weekly 0..1).
Added 2026-11-01. Output: data/research/ats_lab_results.csv (+ ats_lab_series.csv with every variant's daily score).
Variants = exclusion set x method:
  exclusion  E0 v1 labels (DefiLlama/GraphSense exchanges + coinbase miners)
             E1 E0 + OKX/Binance proof-of-reserve + exchange clusters (ats_v2/excl.npy bit 1 if present)
             E2 E1 + clusters >= 10,000 addresses
             E3 E2 + "busy" entities: >= 200 addresses moving on a median active day in the window (unlabelled services)
             E4 E3 + entities holding >= 20,000 BTC at the window start (custodians / ETFs we have no label for)
  method     daily  = v1: daily buys clipped by balance, daily sells, 30-day sums, scaled separately, buys - sells
             net30  = v2: per-entity 30-day net clipped by the balance 30 days ago, buys - sells, 90-day min-max
             raw30  = per-entity 30-day net, no clipping
Lock-free (reads only). Run: python3 scripts/chain/ats_lab.py   (on the Mac; needs ~4 GB RAM)"""
import os, glob, json, datetime as dt
import numpy as np, pandas as pd
C = os.environ.get('MDH_CHAIN_DIR', '/Volumes/chain/datahub/chain'); V1 = f'{C}/ats_v1'; K = f'{C}/ats_kit'
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
meta = json.load(open(f'{K}/done.json'))
parent = np.load(f'{V1}/parent.npy', mmap_mode='r')
bal = np.load(f'{K}/bal0.npy').astype(np.float64)
N = len(bal)
ex = np.zeros(N, np.uint8)                      # bit0 E0, bit1 E1, bit2 E2, bit3 E3, bit4 E4 (cumulative sets)
e0 = np.zeros(N, bool); e0[np.load(f'{V1}/flag_exchange.npy')] = True; e0[np.load(f'{V1}/flag_miner.npy')] = True
e1 = e0.copy()
if os.path.exists(f'{C}/ats_v2/excl.npy'):
    e1 |= (np.load(f'{C}/ats_v2/excl.npy') & 1).astype(bool)
e2 = e1.copy(); e2[np.load(f'{V1}/flag_big.npy')] = True
days = []
for f in sorted(glob.glob(f'{K}/addr_days/*.npz')):
    z = np.load(f); ym = os.path.basename(f)[:7]
    for key in sorted(k for k in z.files if k[0] == 'i'):
        d = f'{ym}-{key[1:]}'
        u = z[key]; v = z['v' + key[1:]].astype(np.float64)
        r = np.asarray(parent[u]).astype(np.int64)
        od = np.argsort(r, kind='stable'); r = r[od]; v = v[od]
        uq, s0, cnt = np.unique(r, return_index=True, return_counts=True)
        days.append((d, uq, np.add.reduceat(v, s0), cnt))
# busy entities: median number of moving addresses on active days
from collections import defaultdict
allr = np.concatenate([x[1] for x in days]); allc = np.concatenate([x[3] for x in days])
od = np.argsort(allr, kind='stable'); allr = allr[od]; allc = allc[od]
ur, s0 = np.unique(allr, return_index=True)
med = np.array([np.median(allc[a:b]) for a, b in zip(s0, list(s0[1:]) + [len(allc)])])
e3 = e2.copy(); e3[ur[med >= 200]] = True
e4 = e3.copy(); e4[bal >= 20000] = True
SETS = {'E0': e0, 'E1': e1, 'E2': e2, 'E3': e3, 'E4': e4}
print('excluded entities', {k: int(v.sum()) for k, v in SETS.items()})
# replay window: one pass, all variants
res = defaultdict(list); ring = []
for d, uq, net, _ in days:
    before = np.maximum(bal[uq], 0); bal[uq] += net
    ring.append((uq, net)); ring = ring[-30:]
    U = np.concatenate([x for x, _ in ring]); V = np.concatenate([y for _, y in ring])
    od = np.argsort(U, kind='stable'); U = U[od]; V = V[od]
    u30, s0 = np.unique(U, return_index=True); n30 = np.add.reduceat(V, s0)
    b30 = np.maximum(bal[u30] - n30, 0)
    c30 = np.clip(n30, -b30, b30)
    pos = np.maximum(net, 0); neg = np.maximum(-net, 0); clipb = np.minimum(pos, before)
    for name, m in SETS.items():
        k1 = ~m[uq]; k30 = ~m[u30]
        res[f'{name}_daily_b'].append(clipb[k1].sum()); res[f'{name}_daily_s'].append(neg[k1].sum())
        c = c30[k30]; res[f'{name}_net30'].append(c[c > 0].sum() + c[c < 0].sum())
        res[f'{name}_raw30'].append(n30[k30].sum())
idx = pd.to_datetime([d for d, *_ in days])
df = pd.DataFrame(res, index=idx)
def mm(x, w=90):
    lo = x.rolling(w, min_periods=60).min(); hi = x.rolling(w, min_periods=60).max(); return (x - lo) / (hi - lo)
out = {}
for name in SETS:
    b = df[f'{name}_daily_b'].rolling(30).sum(); s = df[f'{name}_daily_s'].rolling(30).sum()
    out[f'{name}|daily'] = (mm(b) - mm(s) + 1) / 2
    out[f'{name}|net30'] = mm(df[f'{name}_net30'])
    out[f'{name}|raw30'] = mm(df[f'{name}_raw30'])
S = pd.DataFrame(out)
ref = pd.read_csv(os.path.join(REPO, 'data', 'research', 'beggar_ats_ref.csv'), index_col=0, parse_dates=True).v
W = S.resample('W').mean()
rows = []
for col in S:
    j = pd.concat([ref, W[col]], axis=1).dropna()
    rows.append((col, round(j.corr().iloc[0, 1], 3), len(j)))
R = pd.DataFrame(rows, columns=['variant', 'corr_vs_beggar_weekly', 'n']).sort_values('corr_vs_beggar_weekly', ascending=False)
os.makedirs(os.path.join(REPO, 'data', 'research'), exist_ok=True)
R.to_csv(os.path.join(REPO, 'data', 'research', 'ats_lab_results.csv'), index=False)
S.to_csv(os.path.join(REPO, 'data', 'research', 'ats_lab_series.csv'))
print(R.to_string(index=False))
