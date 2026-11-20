"""Avalon 2026-10-29: "zec一般会在比特币没有很弱的时候继续创低。比特币大跌的时候，反而zec并不会马上同步创新低".
Test on Binance spot daily (2021-01 .. yesterday): (A) on days ZEC makes a new 20d low, what is BTC doing;
(B) on BTC drop days, does ZEC make a new 20d low the same day vs within the next 5 days. Compare with other alts."""
import duckdb, pandas as pd, numpy as np, matplotlib
matplotlib.use('Agg'); import matplotlib.pyplot as plt
c = duckdb.connect('data/market.duckdb', read_only=True)
df = c.sql("select symbol, ts::date d, low, close from bn_kline_1d where market='spot' and ts>='2021-01-01' and ts<current_date").df()
cl = df.pivot(index='d', columns='symbol', values='close'); lo = df.pivot(index='d', columns='symbol', values='low')
STABLE = {'USDC','FDUSD','TUSD','USD1','DAI','USDP','BUSD','PAXG','XAUT','EUR'}
alts = [s for s in cl.columns if s not in STABLE | {'BTC'}]
btc_r = cl['BTC'].pct_change()
W = 20
newlow = lo.lt(lo.shift(1).rolling(W).min())          # today's low below prior 20d low
def stats(sym, since='2021-01-01'):
    m = newlow[sym] & lo[sym].notna() & (lo.index >= pd.Timestamp(since))
    days = newlow.index[m]
    br = btc_r.loc[days].dropna()
    big = btc_r[(btc_r <= -0.03) & (btc_r.index >= pd.Timestamp(since)) & lo[sym].notna()].index
    same = newlow.loc[big, sym].mean()
    idx = [newlow.index.get_loc(d) for d in big]
    later = np.mean([(not newlow[sym].iloc[i]) and newlow[sym].iloc[i+1:i+6].any() for i in idx if i + 6 <= len(newlow)])
    return dict(n_newlow=len(br), btc_weak_share=(br <= -0.02).mean(), btc_notweak_share=(br > -0.01).mean(),
                n_bigdrop=len(big), same_day=same, later_1_5d_only=later)
for since in ('2021-01-01', '2024-01-01'):
    rows = {s: stats(s, since) for s in alts if lo[s].loc[pd.Timestamp(since):].notna().sum() > 300}
    T = pd.DataFrame(rows).T.astype(float)
    print(f'\n== since {since}: {len(T)} alts ==')
    print('ZEC      ', T.loc['ZEC'].round(2).to_dict())
    print('alt median', T.median().round(2).to_dict())
    print('ZEC rank (same_day, low=better resistance):', int((T.same_day < T.loc['ZEC','same_day']).sum()) + 1, '/', len(T),
          '| btc_notweak_share rank (high=more lows on calm BTC days):', int((T.btc_notweak_share > T.loc['ZEC','btc_notweak_share']).sum()) + 1, '/', len(T))
    if since == '2024-01-01': T24 = T
fig, ax = plt.subplots(1, 2, figsize=(12, 5))
ax[0].scatter(T24.btc_notweak_share * 100, T24.same_day * 100, color='#aaa')
for s in ['ZEC', 'ETH', 'SOL', 'HYPE', 'DASH', 'XRP', 'DOGE']:
    if s in T24.index: ax[0].scatter(T24.loc[s, 'btc_notweak_share'] * 100, T24.loc[s, 'same_day'] * 100, color='#d1495b' if s == 'ZEC' else '#2a9d8f'); ax[0].annotate(s, (T24.loc[s, 'btc_notweak_share'] * 100, T24.loc[s, 'same_day'] * 100), fontsize=8)
ax[0].set_xlabel('% of its new 20d lows made on days BTC > -1%'); ax[0].set_ylabel('% of BTC <= -3% days it makes a new 20d low SAME day')
ax[0].set_title('2024-01..2026-10-28, each dot = one alt')
# ZEC timeline 2026: ZEC new lows vs BTC daily return
z = newlow['ZEC'][newlow.index >= pd.Timestamp("2025-10-01")]
d = [pd.Timestamp(x) for x in z.index]
ax[1].bar(d, btc_r.loc[z.index] * 100, color=['#d1495b' if v else '#ddd' for v in z], width=1.0)
ax[1].set_title('BTC daily return %, red = ZEC new 20d low that day (2025-10..)'); ax[1].axhline(0, color='k', lw=.5)
for t in ax[1].get_xticklabels(): t.set_rotation(30)
plt.tight_layout(); plt.savefig('data/outputs/zec_lows_vs_btc_2026-10-29.png', dpi=100)
zl = [(str(i.date()), round(btc_r.loc[i]*100,1)) for i in z.index[z]]
print('\nZEC new 20d lows since 2025-10 (date, BTC day %):', zl)
