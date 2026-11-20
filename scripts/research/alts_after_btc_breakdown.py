"""Avalon 2026-10-29: "比特币这样一根破位大阴线，事后山寨币表现如何（破位之后一段时间）".
Event: BTC daily close <= -3.5% AND close below prior 20-day lowest close (breakdown); 10-day cooldown.
Outcome: median alt vs BTC and BTC itself over next 7/14/30 days. Binance spot daily 2021-01..2026-10-28 (10/29 shown separately)."""
import duckdb, pandas as pd, numpy as np, matplotlib
matplotlib.use('Agg'); import matplotlib.pyplot as plt
c = duckdb.connect('data/market.duckdb', read_only=True)
df = c.sql("select symbol, ts::date d, close from bn_kline_1d where market='spot' and ts>='2020-12-01' and ts<current_date").df()
cl = df.pivot(index='d', columns='symbol', values='close'); cl.index = pd.to_datetime(cl.index)
STABLE = {'USDC','FDUSD','TUSD','USD1','DAI','USDP','BUSD','PAXG','XAUT','EUR'}
alts = [s for s in cl.columns if s not in STABLE | {'BTC'}]
b = cl['BTC']; r = b.pct_change()
brk = (r <= -0.035) & (b < b.shift(1).rolling(20).min())
ev = []; last = None
for t in b.index[brk]:
    if t < pd.Timestamp('2021-01-01'): continue
    if last is None or (t - last).days >= 10: ev.append(t); last = t
rows = []
for t in ev:
    i = b.index.get_loc(t); row = {'date': t.date(), 'btc_day': r.iloc[i]}
    for h in (7, 14, 30):
        if i + h >= len(b): continue
        rel = (cl[alts].iloc[i + h] / cl[alts].iloc[i]) / (b.iloc[i + h] / b.iloc[i]) - 1
        row[f'alt_vs_btc_{h}'] = rel.median(); row[f'btc_{h}'] = b.iloc[i + h] / b.iloc[i] - 1
        row[f'beat_{h}'] = (rel > 0).mean()
    rows.append(row)
R = pd.DataFrame(rows)
pd.set_option('display.width', 220)
print((R.set_index('date') * 100).round(1).to_string())
print('\nn', len(R)); print('median %:', (R.drop(columns='date').median() * 100).round(1).to_dict())
for h in (7, 14, 30):
    x = R[f'alt_vs_btc_{h}'].dropna(); print(f'{h}d: alts beat BTC (median alt) in {(x>0).sum()}/{len(x)} events; BTC up in {(R[f"btc_{h}"].dropna()>0).sum()}/{len(x)}')
# baseline: all days
base = {}
for h in (7, 14, 30):
    rel = (cl[alts].shift(-h) / cl[alts]).div(b.shift(-h) / b, axis=0) - 1
    m = rel.median(axis=1)[b.index >= '2021-01-01'].dropna(); base[h] = m.median()
print('baseline all days median alt vs BTC %:', {h: round(v * 100, 1) for h, v in base.items()})
fig, ax = plt.subplots(figsize=(11, 5))
x = np.arange(len(R)); w = .38
ax.bar(x - w/2, R['alt_vs_btc_30'] * 100, w, color='#6a4c93', label='median alt vs BTC, next 30d')
ax.bar(x + w/2, R['btc_30'] * 100, w, color='#f2a900', label='BTC next 30d')
ax.axhline(base[30] * 100, ls='--', color='#6a4c93', lw=.8, label=f'all-days alt vs BTC 30d median {base[30]*100:.1f}%')
ax.axhline(0, color='k', lw=.5); ax.set_xticks(x); ax.set_xticklabels([str(d) for d in R.date], rotation=60, fontsize=7)
ax.set_title('After a BTC breakdown day (close <= -3.5% and below prior 20d low), 2021-2026, Binance spot'); ax.legend(fontsize=8)
plt.tight_layout(); plt.savefig('data/outputs/alts_after_btc_breakdown_2026-10-29.png', dpi=100)
