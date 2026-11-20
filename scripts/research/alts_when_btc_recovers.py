"""Avalon 2026-10-30: "比特币如果回去，山寨币还能回去吗". After a BTC breakdown day (close <= -3.5% and below prior 20d
lowest close, 10d cooldown, 2021-01..2026-10-28), find the first day within 90d that BTC closes back at/above the close
the day BEFORE the breakdown. On that day: what share of alts are back at their own pre-breakdown close, and the median
alt's % vs its pre-breakdown close. Binance spot daily."""
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
    i = b.index.get_loc(t); pre = i - 1
    w = b.iloc[i + 1:i + 91]
    back = w[w >= b.iloc[pre]]
    if len(back) == 0:
        rows.append({'date': t.date(), 'days_to_recover': None}); continue
    j = b.index.get_loc(back.index[0])
    rel = cl[alts].iloc[j] / cl[alts].iloc[pre] - 1
    rel = rel[cl[alts].iloc[pre].notna() & cl[alts].iloc[j].notna()]
    rows.append({'date': t.date(), 'days_to_recover': j - i, 'alt_med_vs_pre': rel.median(), 'share_back': (rel >= 0).mean(), 'n': len(rel)})
R = pd.DataFrame(rows); pd.set_option('display.width', 200)
print(R.round(3).to_string())
ok = R.dropna(subset=['alt_med_vs_pre'])
print(f"\nBTC recovered within 90d: {len(ok)}/{len(R)}; median days {ok.days_to_recover.median():.0f}")
print(f"when BTC is back: median alt vs its pre-breakdown close {ok.alt_med_vs_pre.median()*100:.1f}%; share of alts back {ok.share_back.median()*100:.0f}% (median event); events where median alt also back: {(ok.alt_med_vs_pre>=0).sum()}/{len(ok)}")
fig, ax = plt.subplots(figsize=(11, 5))
x = np.arange(len(ok)); ax.bar(x, ok.alt_med_vs_pre * 100, color=['#2a9d8f' if v >= 0 else '#d1495b' for v in ok.alt_med_vs_pre])
ax.axhline(0, color='k', lw=.5); ax.set_xticks(x); ax.set_xticklabels([f"{d} ({int(k)}d)" for d, k in zip(ok.date, ok.days_to_recover)], rotation=60, fontsize=7)
ax.set_ylabel('median alt vs its pre-breakdown close, % (on the day BTC is back)')
ax.set_title(f'When BTC climbs back to its pre-breakdown close: are alts back too?  ({len(ok)} of {len(R)} breakdowns recovered within 90d, 2021-2026)')
plt.tight_layout(); plt.savefig('data/outputs/alts_when_btc_recovers_2026-10-30.png', dpi=100)
