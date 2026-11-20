"""Avalon 2026-10-30: "之前集中度拐头下跌，是一天就跌完，还是跌一部分，过几天再继续跌？"
For every DOWN turn (standard v2 events in conc_dashboard_v2.json, 2017..2026-10-26), BTC daily close path after the turn day:
turn-day return, lowest close within 30 days (depth + which day), and how much of that 30d low was already reached by day 1/3/7.
Prices = Binance spot daily close (bn_kline_1d). Also lists 10/29 for reference."""
import json, duckdb, pandas as pd, numpy as np, matplotlib
matplotlib.use('Agg'); import matplotlib.pyplot as plt
j = json.load(open('data/outputs/conc_dashboard_v2.json'))
ev = [e for e in j['events'] if e['dir'] == 'DOWN']
c = duckdb.connect('data/market.duckdb', read_only=True)
b = c.sql("select ts::date d, close from bn_kline_1d where market='spot' and symbol='BTC' order by ts").df().set_index('d')['close']
b.index = pd.to_datetime(b.index)
rows = []
for e in ev:
    t = pd.Timestamp(e['turn'])
    if t not in b.index: continue
    i = b.index.get_loc(t); p0 = b.iloc[i - 1]   # close the day before the turn
    w = b.iloc[i:i + 31]
    if len(w) < 31: continue
    path = w / p0 - 1
    low = path.min(); dlow = int(np.argmin(path.values))
    rows.append(dict(turn=t.date(), turn_day=path.iloc[0], low30=low, day_of_low=dlow,
                     by_d1=path.iloc[:2].min() / low if low < 0 else np.nan,
                     by_d3=path.iloc[:4].min() / low if low < 0 else np.nan,
                     by_d7=path.iloc[:8].min() / low if low < 0 else np.nan, end30=path.iloc[30]))
R = pd.DataFrame(rows); pd.set_option('display.width', 200)
print((R.set_index('turn')).round(3).to_string())
print('\nn', len(R))
print('median: turn-day', round(R.turn_day.median()*100,1), '% | 30d low', round(R.low30.median()*100,1), '% on day', R.day_of_low.median(),
      '| share of 30d low reached by day1/3/7:', round(R.by_d1.median()*100), round(R.by_d3.median()*100), round(R.by_d7.median()*100), '%')
print('low on turn day itself (day 0):', (R.day_of_low == 0).sum(), '| low within 3 days:', (R.day_of_low <= 3).sum(), '| low after day 7:', (R.day_of_low > 7).sum())
fig, ax = plt.subplots(figsize=(11, 5))
for e in rows:
    t = pd.Timestamp(e['turn']); i = b.index.get_loc(t); p0 = b.iloc[i - 1]
    ax.plot(range(31), (b.iloc[i:i + 31] / p0 - 1).values * 100, color='#999', lw=.9)
i = b.index.get_loc(pd.Timestamp('2026-10-29')); p0 = b.iloc[i - 1]
ax.plot(range(len(b.iloc[i:i+31])), (b.iloc[i:i + 31] / p0 - 1).values * 100, color='#d1495b', lw=2.5, marker='o', label='2026-10-29 (now)')
ax.axhline(0, color='k', lw=.5); ax.set_xlabel('days after the down turn (day 0 = turn day)'); ax.set_ylabel('BTC % vs close before turn day')
ax.set_title(f'BTC after every concentration DOWN turn (standard v2), n={len(rows)}; grey = past'); ax.legend(fontsize=8)
plt.tight_layout(); plt.savefig('data/outputs/conc_down_turn_path_2026-10-30.png', dpi=100)
