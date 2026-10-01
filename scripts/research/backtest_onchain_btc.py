"""Ad hoc BTC on-chain read (2026-09-30): BGeometrics CSVs + Binance daily closes.
Snapshot, metric values at local tops (121d window), forward 30/60/90d returns by condition
(overlapping days, NOT declustered), reclaim-of-STH-RP-and-TMM events. Unreviewed."""
import duckdb, pandas as pd, numpy as np, glob, os
c=duckdb.connect('data/market.duckdb',read_only=True)
px=c.sql("select cast(ts as date) d, close from bn_kline_1d where symbol='BTC' and market='spot' order by ts").df()
px['d']=pd.to_datetime(px.d); px=px.set_index('d')
for f in glob.glob('data/onchain/bgeometrics/*.csv'):
    m=pd.read_csv(f,parse_dates=['d']).set_index('d'); col=m.columns[0]
    px[os.path.basename(f)[:-4]]=pd.to_numeric(m[col],errors='coerce')
df=px[px.index>='2022-09-30'].copy()
live=83294
df['ret30']=df.close.shift(-30)/df.close-1; df['ret60']=df.close.shift(-60)/df.close-1; df['ret90']=df.close.shift(-90)/df.close-1
df['nrpl30']=df['nrpl-usd'].rolling(30).sum(); df['rp30']=df['realized-profit'].rolling(30).sum(); df['rl30']=df['realized-loss'].rolling(30).sum()
df['sopr7']=df.sopr.rolling(7).mean(); df['sthsopr7']=df['sth-sopr'].rolling(7).mean()
df['px_sthrp']=df.close/df['sth-realized-price']; df['px_tmm']=df.close/df['true-market-mean']
last=df.dropna(subset=['sth-mvrv']).iloc[-1]; print('LAST METRIC DAY',last.name.date()); print(last[['close','sth-realized-price','true-market-mean','realized-price','mvrv','sth-mvrv','sopr7','sthsopr7','nrpl30','rp30','rl30']].to_string())
print('rp30 latest (to 9/29):', df['rp30'].dropna().iloc[-1]/1e9, 'rl30', df['rl30'].dropna().iloc[-1]/1e9)
pct=lambda s,v:(s.dropna()<v).mean()*100
print('pct sth-mvrv',pct(df['sth-mvrv'],last['sth-mvrv']),'mvrv',pct(df.mvrv,last.mvrv),'nrpl30',pct(df.nrpl30,last.nrpl30),'rp30',pct(df.rp30,df.rp30.dropna().iloc[-1]))
print('live px/sthrp',live/last['sth-realized-price'],'px/tmm',live/last['true-market-mean'],'px/realized',live/last['realized-price'])
# cycle context
full=px.close; print('ATH', full.idxmax().date(), full.max()); post=full[full.index>full.idxmax()]; print('low after ATH',post.idxmin().date(),post.min(),'bounce high',post[post.index>post.idxmin()].max(), post[post.index>post.idxmin()].idxmax().date())
# local tops: 60-day centered max, and metric values there
w=df.close.rolling(121,center=True).max()
tops=df[(df.close==w)]
print('\nLOCAL TOPS (121d window): date close sth-mvrv mvrv rp30($B) nrpl30($B) drawdown next 90d')
for d,r in tops.iterrows():
    fut=df.close[d:d+pd.Timedelta(days=90)].min()/r.close-1
    print(d.date(), round(r.close), round(r['sth-mvrv'],2), round(r.mvrv,2), round(r.rp30/1e9,1), round(r.nrpl30/1e9,1), f'{fut:.0%}')
print('\nsth-mvrv max by quarter'); print(df['sth-mvrv'].resample('QE').max().round(2).to_string())
def cond(name,mask):
    s=df[mask]
    print(f'\n{name}: days {len(s)}', end=' ')
    for h in (30,60,90):
        r=s[f'ret{h}'].dropna(); print(f'| {h}d n={len(r)} win={ (r>0).mean():.0%} med={r.median():+.1%}',end=' ')
    print()
base=df.index==df.index
cond('ALL days',base)
cond('sth-mvrv 1.10-1.25',df['sth-mvrv'].between(1.10,1.25))
cond('sth-mvrv >1.25',df['sth-mvrv']>1.25)
cond('price >= sthrp & tmm',(df.px_sthrp>=1)&(df.px_tmm>=1))
cond('price < tmm',df.px_tmm<1)
cond('sth-sopr7 < 1',df.sthsopr7<1)
cond('nrpl30 top 20%',df.nrpl30>df.nrpl30.quantile(.8))
# first reclaim of both lines after 30+ days below
ab=((df.px_sthrp>=1)&(df.px_tmm>=1)).astype(int)
below=ab.rolling(30).sum().shift(1)==0
ev=df[(ab==1)&below]
print('\nRECLAIM both lines after >=30d below:')
for d,r in ev.iterrows(): print(d.date(), round(r.close), f"30d {r.ret30:+.0%} 90d {r.ret90:+.0%}", 'min next 90d', f"{df.close[d:d+pd.Timedelta(days=90)].min()/r.close-1:+.0%}")
