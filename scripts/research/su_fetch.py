import pandas as pd, requests, os
from concurrent.futures import ThreadPoolExecutor
def kl(sym,start):
  out=[];s=start
  while True:
    r=requests.get("https://data-api.binance.vision/api/v3/klines",params=dict(symbol=sym,interval='1d',startTime=s,limit=1000),timeout=30).json()
    if not isinstance(r,list) or not r: break
    out+=r; s=r[-1][0]+1
    if len(r)<1000: break
  if not out: return None
  df=pd.DataFrame(out).iloc[:,[0,3,4,7]]; df.columns=['t','l','c','qv']
  df['t']=pd.to_datetime(df.t,unit='ms',utc=True)
  for k in ['l','c','qv']: df[k]=df[k].astype(float)
  return df.set_index('t')
def job(s):
  f=f'su/{s}.pkl'
  if os.path.exists(f): return s,'cached'
  try:
    d=kl(s,1606780800000)  # 2020-12-01
    if d is None: return s,'empty'
    d.to_pickle(f); return s,len(d)
  except Exception as e: return s,'err '+str(e)[:50]
S=open('allusdt.txt').read().split()
with ThreadPoolExecutor(8) as ex: res=list(ex.map(job,S))
import collections; print(collections.Counter(type(x[1]).__name__ if not isinstance(x[1],str) else x[1].split()[0] for x in res))
print([x for x in res if isinstance(x[1],str) and x[1].startswith('err')][:5])
