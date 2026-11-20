"""US / Europe / Asia session price changes (貝格先生's 美方資金情緒曲線 / 亞資 / 笨蛋錢指標, built on Glassnode's regional
30-day price change). Added 2026-11-01 (Avalon).
Per hour: change = close - open (USD, Binance BTCUSDT spot). Assigned to a region when the hour starts inside that
region's working hours 08:00-20:00 local time (America/New_York, Europe/Berlin, Asia/Shanghai, DST handled; Glassnode
documents 8am-8pm CST for Asia and CET for Europe). Overlapping hours count for both regions.
Curve = 30-day rolling sum of the region's hourly changes (USD), sampled at 00:00 UTC; bar = US - mean(EU, Asia).
Dumb-money signal (our own rule; his algorithm is not public): 14-day change of the US curve (as % of price) in the
lowest 15% of the past 365 days AND the Asia curve's 14-day change in the highest 15%.
History before 2023: data/onchain/self/hist/btcusdt_1h_2017_2022.csv (data.binance.vision monthly files).
Output: data/onchain/self/region_daily.csv"""
import os, duckdb, numpy as np, pandas as pd
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
OUT = os.path.join(REPO, 'data', 'onchain', 'self')
a = pd.read_csv(os.path.join(OUT, 'hist', 'btcusdt_1h_2017_2022.csv'))
a['ts'] = pd.to_datetime(a.t, unit='ms', utc=True); a = a[['ts', 'open', 'close']]
db = os.environ.get('MARKET_DB', os.path.join(REPO, 'data', 'market.duckdb'))
c = duckdb.connect(db, read_only=True)
b = c.execute("select ts, open, close from bn_kline_1h where market='spot' and symbol='BTC' order by ts").df()
b['ts'] = pd.to_datetime(b.ts).dt.tz_localize('UTC')
h = pd.concat([a, b]).drop_duplicates('ts', keep='last').sort_values('ts').reset_index(drop=True)
h['chg'] = h.close - h.open
for n, tz in (('us', 'America/New_York'), ('eu', 'Europe/Berlin'), ('asia', 'Asia/Shanghai')):
    lt = h.ts.dt.tz_convert(tz).dt.hour; h[n] = np.where((lt >= 8) & (lt < 20), h.chg, 0.0)
s = h.set_index('ts'); r = s[['us', 'eu', 'asia']].rolling('30D').sum(); r['close'] = s.close
d = r[r.index.hour == 0].copy(); d.index = d.index.tz_localize(None).normalize(); d = d[d.index >= '2017-09-20']
last = r.iloc[-1:].copy(); last.index = [r.index[-1].tz_localize(None)]
d['bar'] = d.us - (d.eu + d.asia) / 2
dus = (d.us - d.us.shift(14)) / d.close * 100; das = (d.asia - d.asia.shift(14)) / d.close * 100
d['dumb'] = ((dus < dus.rolling(365, min_periods=180).quantile(.15)) & (das > das.rolling(365, min_periods=180).quantile(.85))).astype(int)
d.index.name = 'd'; d.index = d.index.strftime('%Y-%m-%d')
d.round(1).to_csv(os.path.join(OUT, 'region_daily.csv'))
print(f"region: {d.index[0]}..{d.index[-1]} US {d.us.iloc[-1]:,.0f} EU {d.eu.iloc[-1]:,.0f} Asia {d.asia.iloc[-1]:,.0f} dumb {int(d.dumb.iloc[-1])}")
