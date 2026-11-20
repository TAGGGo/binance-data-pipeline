"""Do alts weakening first (alt median lags BTC while BTC holds) lead a BTC drop? Avalon 2026-10-29:
"今天山寨币先撑不住，然后比特币才跳水。山寨币撑不住说明流动性在撤出". Data: bn_kline_1d spot 2021-01..2026-10-28."""
import duckdb, pandas as pd, numpy as np, matplotlib
matplotlib.use('Agg'); import matplotlib.pyplot as plt
c = duckdb.connect('data/market.duckdb', read_only=True)
df = c.sql("select symbol, ts::date d, low, close from bn_kline_1d where market='spot' and ts>='2021-01-01' and ts < current_date").df()
cl = df.pivot(index='d', columns='symbol', values='close'); lo = df.pivot(index='d', columns='symbol', values='low')
cl.index = pd.to_datetime(cl.index); lo.index = cl.index
STABLE = {'USDC','FDUSD','TUSD','USD1','DAI','USDP','BUSD','PAXG','XAUT','EUR'}
alts = [s for s in cl.columns if s not in STABLE | {'BTC'}]
r = cl.pct_change(fill_method=None)
btc = r['BTC']; am = r[alts].median(axis=1); n = r[alts].notna().sum(axis=1)
gap = am - btc
fwd1 = cl['BTC'].shift(-1) / cl['BTC'] - 1
fwd3 = cl['BTC'].shift(-3) / cl['BTC'] - 1
min3 = pd.concat([lo['BTC'].shift(-k) for k in (1, 2, 3)], axis=1).min(axis=1) / cl['BTC'] - 1
D = pd.DataFrame({'btc': btc, 'gap': gap, 'n': n, 'f1': fwd1, 'f3': fwd3, 'm3': min3}).dropna()
D = D[D.n >= 15]
def summ(x, lab):
    print(f"{lab:48s} n={len(x):4d}  next1d med {x.f1.median()*100:+.2f}%  next3d med {x.f3.median()*100:+.2f}%  "
          f"P(3d low <= -3%) {(x.m3 <= -0.03).mean()*100:.0f}%  P(3d close<0) {(x.f3 < 0).mean()*100:.0f}%")
summ(D, 'all days')
summ(D[(D.btc > -0.015)], 'BTC day > -1.5%')
for g in (-0.01, -0.015, -0.02, -0.03):
    summ(D[(D.btc > -0.015) & (D.gap <= g)], f'BTC > -1.5% & alts lag BTC by >= {abs(g)*100:.1f}pp')
summ(D[(D.btc > -0.015) & (D.gap >= 0.015)], 'BTC > -1.5% & alts BEAT BTC by >= 1.5pp')
sig = D[(D.btc > -0.015) & (D.gap <= -0.015)]
for y in range(2021, 2027):
    s = sig[sig.index.year == y]; b = D[(D.index.year == y) & (D.btc > -0.015)]
    print(y, 'signal n', len(s), 'P(3d low<=-3%)', round((s.m3 <= -0.03).mean() * 100) if len(s) else None, 'vs base', round((b.m3 <= -0.03).mean() * 100))
print('recent signal days:', [(d.date().isoformat(), round(g*100,1), round(m*100,1)) for d, g, m in zip(sig.index[-8:], sig.gap[-8:], sig.m3[-8:])])
fig, ax = plt.subplots(figsize=(10, 5))
bins = [-1, -0.03, -0.02, -0.015, -0.01, -0.005, 0.005, 0.01, 0.015, 0.02, 0.03, 1]
B = D[D.btc > -0.015].copy(); B['bin'] = pd.cut(B.gap, bins)
g = B.groupby('bin', observed=True).agg(p=('m3', lambda x: (x <= -0.03).mean() * 100), n=('m3', 'size'), f3=('f3', 'median'))
ax.bar(range(len(g)), g.p, color=['#d1495b' if i.right <= -0.01 else '#2a9d8f' if i.left >= 0.01 else '#888' for i in g.index])
for i, (p, nn) in enumerate(zip(g.p, g.n)): ax.text(i, p + 0.5, f'n={nn}', ha='center', fontsize=8)
ax.axhline((B.m3 <= -0.03).mean() * 100, ls='--', color='k', lw=.8, label='base (all BTC>-1.5% days)')
ax.set_xticks(range(len(g))); ax.set_xticklabels([f'{i.left*100:.1f}..{i.right*100:.1f}' for i in g.index], rotation=40, fontsize=8)
ax.set_xlabel('alt median daily return minus BTC (pp), on days BTC > -1.5%'); ax.set_ylabel('% of days BTC low next 3d <= -3%')
ax.set_title('Do alts weakening first lead a BTC drop?  Binance spot daily 2021-01 .. 2026-10-28'); ax.legend(fontsize=8)
plt.tight_layout(); plt.savefig('data/outputs/alts_weak_first_btc_next_2026-10-29.png', dpi=100)
print(g.round(3).to_string())
