import pandas as pd, numpy as np, glob, os
C={};L={};Q={}
for f in glob.glob('su/*.pkl'):
  s=os.path.basename(f)[:-8]; d=pd.read_pickle(f)
  C[s]=d.c; L[s]=d.l; Q[s]=d.qv
C=pd.DataFrame(C); L=pd.DataFrame(L); Q=pd.DataFrame(Q)
cut=pd.Timestamp('2026-11-06',tz='UTC'); C=C[C.index<cut]; L=L[L.index<cut]; Q=Q[Q.index<cut]
idx=C.index
b=C.BTC; prev=L.rolling(20,min_periods=20).min().shift(1); broke=L<prev
vol30=Q.rolling(30,min_periods=25).mean().shift(1)
H=20
bb=broke.BTC.values; ev=[];last=-99
for i in range(30,len(idx)-H):
  if bb[i] and bb[i-10:i].any() and i-last>=5: ev.append(i); last=i
print('events',len(ev))
rows=[]
for i in ev:
  t=idx[i]; bf=b.iloc[i+H]/b.iloc[i]-1
  v=vol30.iloc[i].drop('BTC').dropna(); v=v[v>0]
  uni=v.sort_values(ascending=False).head(100).index
  for s in uni:
    if pd.isna(C[s].iloc[i]) or pd.isna(prev[s].iloc[i]): continue
    fut=C[s].iloc[i+1:i+H+1].dropna()
    if len(fut)==0: f=-1.0; dl=True
    else: f=fut.iloc[-1]/C[s].iloc[i]-1; dl=len(fut)<H
    dist=L[s].iloc[i]/prev[s].iloc[i]-1
    rows.append(dict(t=t,s=s,broke=bool(L[s].iloc[i]<prev[s].iloc[i]),dist=dist,f=f,rel=(1+f)/(1+bf)-1,bf=bf,delist=dl))
R=pd.DataFrame(rows); R['grp']=np.where(R.broke,'A破',np.where(R.dist>=0.10,'C强','B弱没破')); R['yr']=R.t.dt.year
R.to_csv('su_rows.csv',index=False)
def pe(x):
  s=x.groupby('t').rel.apply(lambda r:(r>0).mean()*100); return (round(s.mean()) if len(s) else None, len(s))
print('delisted within window rows:',int(R.delist.sum()))
out=[]
for lab,g in [('ALL',R)]+list(R.groupby('yr')):
  for c_,c in [('all',g),('BTCup',g[g.bf>0])]:
    out.append(dict(yr=lab,cond=c_,A=pe(c[c.grp=='A破']),B=pe(c[c.grp=='B弱没破']),C=pe(c[c.grp=='C强']),C_n=int((c.grp=='C强').sum())))
print(pd.DataFrame(out).to_string(index=False))
for era,g in [('2021-23',R[R.t<'2024-01-01']),('2024+',R[R.t>='2024-01-01'])]:
  for c_,c in [('all',g),('BTCup',g[g.bf>0]),('BTCup10',g[g.bf>=0.10])]:
    print(era,c_,'A',pe(c[c.grp=='A破']),'B',pe(c[c.grp=='B弱没破']),'C',pe(c[c.grp=='C强']),'C med rel',round(c[c.grp=='C强'].rel.median()*100,1))
