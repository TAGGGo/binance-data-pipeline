"""Median alt/BTC after BTC cycle lows (2019, 2023, 2026). Rerun 2026-10-29 for Avalon:
"感觉9月25是个顶...已经越来越有23年的样子了吧". Data: bn_kline_1d spot (today's row = forming)."""
import duckdb, pandas as pd, numpy as np, matplotlib, sys
matplotlib.use('Agg'); import matplotlib.pyplot as plt
c = duckdb.connect('data/market.duckdb', read_only=True)
df = c.sql("select symbol, ts::date d, close from bn_kline_1d where market='spot'").df()
px = df.pivot(index='d', columns='symbol', values='close'); px.index = pd.to_datetime(px.index)
btc = px.pop('BTC')
STABLE = {'USDC','FDUSD','TUSD','USD1','DAI','USDP','BUSD','PAXG','XAUT','EUR'}
px = px[[s for s in px.columns if s not in STABLE]]
LOWS = {'2019': '2018-12-15', '2023': '2022-11-21', '2026': '2026-07-01'}
out = {}
for k, lo in LOWS.items():
    lo = pd.Timestamp(lo); sub = px[px.index >= lo]; b = btc[btc.index >= lo]
    have = [s for s in sub.columns if not np.isnan(sub[s].iloc[0])]
    rel = sub[have].div(b, axis=0); rel = rel / rel.iloc[0] * 100
    med = rel.median(axis=1); med.index = (med.index - lo).days
    bb = b / b.iloc[0] * 100; bb.index = (bb.index - lo).days
    out[k] = (med, bb, len(have))
now = (btc.index[-1] - pd.Timestamp(LOWS['2026'])).days
for k, (m, b, n) in out.items():
    pts = {d: (round(m[d]) if d in m.index else None) for d in (20, 60, 86, 100, now, 150, 200, 300)}
    pk = m[m.index <= 150]; print(k, 'n', n, pts, 'peak<=150', round(pk.max()), 'at d', pk.idxmax(), 'BTC at now/200', b.get(now), b.get(200))
fig, ax = plt.subplots(1, 2, figsize=(13, 5))
col = {'2019': '#888', '2023': '#d1495b', '2026': '#2a9d8f'}
for k, (m, b, n) in out.items():
    m = m[m.index <= 400]; b = b[b.index <= 400]
    ax[0].plot(m.index, m, color=col[k], lw=2.2 if k == '2026' else 1.3, label=f'{k} (n={n})')
    ax[1].plot(b.index, b, color=col[k], lw=2.2 if k == '2026' else 1.3, label=k)
for a in ax: a.axvline(now, ls='--', color='k', lw=.7); a.legend(fontsize=8); a.set_xlabel('days after BTC cycle low')
ax[0].axhline(100, color='k', lw=.5); ax[0].set_title('median alt / BTC (=100 at the low)'); ax[1].set_yscale('log'); ax[1].set_title('BTC (=100 at the low, log)')
fig.suptitle(f'After BTC cycle lows: 2018-12-15 / 2022-11-21 / 2026-07-01   (dashed = now, day {now}, data to {btc.index[-1].date()} incl. forming day)')
plt.tight_layout(); plt.savefig('data/outputs/alt_vs_btc_after_cycle_low_2026-10-29.png', dpi=100)
# 2026 detail: rel peak date and since
m26 = out['2026'][0]; lo = pd.Timestamp(LOWS['2026'])
pkd = m26.idxmax(); print('2026 rel peak', round(m26.max()), (lo + pd.Timedelta(days=int(pkd))).date(), 'now', round(m26.iloc[-1]))
