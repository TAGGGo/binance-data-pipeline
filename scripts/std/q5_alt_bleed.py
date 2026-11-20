"""Q5 v0 山寨失血检查 — reproducible answer to "山寨是不是又在失血 / BTC 再涨山寨能不能跑赢".
Usage: python3 q5_alt_bleed.py <alt.json from alt_legs_build.py> <market.snapshot.duckdb>
Sections: A 现在的状态  B 熊底对齐(2019/2023)  C BTC横盘山寨失血有多常见  D 失血后BTC再涨,山寨能否跑赢(历史条件概率)"""
import json, sys, datetime as dt, statistics as st, numpy as np, duckdb
VERSION="Q5 v0"
altj, db = sys.argv[1:3]
S={r[0]:r for r in json.load(open(altj))['series']}; D=sorted(S); N=len(D); T=D[-1]
b=np.array([S[d][1] for d in D]); a=np.array([S[d][3]/S[d][2] for d in D]); m=np.array([S[d][4] for d in D])/b
ix={d:i for i,d in enumerate(D)}
def add(d,n): return (dt.date.fromisoformat(d)+dt.timedelta(n)).isoformat()
c=duckdb.connect(db,read_only=True); px={}
for s,d,cl in c.sql("select symbol, ts::date::varchar, close from bn_kline_1d where market='spot'").fetchall(): px.setdefault(s,{})[d]=cl
eb={d:px['ETH'][d]/px['BTC'][d] for d in px['ETH'] if d in px['BTC']}
# 熊底 = lowest close in US midterm year (2026 provisional until year end)
def bear(y): ds=[d for d in D if d[:4]==str(y)]; return min(ds,key=lambda d:S[d][1]) if ds else None
B0={y:bear(y) for y in (2018,2022,2026)}
print(f"{VERSION}  data to {T}")
# A
i=N-1; hi=max(range(i-60,i+1),key=lambda k:a[k]/a[ix[B0[2026]]])
print("\n[A] 现在")
for lab,k in (("20天",20),("60天",60)):
    print(f"  近{lab}: BTC {b[i]/b[i-k]-1:+.1%}  山寨总市值/BTC {a[i]/a[i-k]-1:+.1%}  中位币/BTC {m[i]/m[i-k]-1:+.1%}  ETH/BTC {eb[T]/eb[D[i-k]]-1:+.1%}")
hd=D[hi]; print(f"  近60天山寨/BTC最强日 {hd}: 之后 山寨总市值/BTC {a[i]/a[hi]-1:+.1%}  中位币/BTC {m[i]/m[hi]-1:+.1%}  BTC {b[i]/b[hi]-1:+.1%}")
co=[s for s in px if s!='BTC' and hd in px[s] and T in px[s]]; r=[px[s][T]/px[s][hd]-1 for s in co]; bt=b[i]/b[hi]-1
print(f"  自{hd}起 {len(co)}个币安山寨: 中位 {st.median(r):+.1%}, 跌得比BTC少的 {sum(x>bt for x in r)/len(r):.0%}, 收回{hd}价位的 {sum(x>=0 for x in r)}个")
# B
n=(dt.date.fromisoformat(T)-dt.date.fromisoformat(B0[2026])).days
print(f"\n[B] 熊底对齐 (熊底=中期选举年最低收盘; 2026底暂定) 今天=熊底后第{n}天")
for y in (2018,2022,2026):
    s=B0[y]; t=add(s,n); f=add(t,90)
    if t not in S: continue
    i0,it=ix[s],ix[t]
    line=f"  {s} 底{S[s][1]:,} → {t}: BTC {b[it]/b[i0]-1:+.0%}  山寨总市值/BTC {a[it]/a[i0]-1:+.0%}  中位币/BTC {m[it]/m[i0]-1:+.0%}  ETH/BTC {eb[t]/eb[s]-1:+.0%}"
    if f in S: jf=ix[f]; line+=f" | 之后90天: BTC {b[jf]/b[it]-1:+.0%} 山寨/BTC {a[jf]/a[it]-1:+.0%} 中位币/BTC {m[jf]/m[it]-1:+.0%}"
    print(line)
# C
print("\n[C] BTC 20天涨跌<=5% 且 山寨总市值/BTC 跌>=10% 的20天窗口数(重叠计)")
cnt={}
for k in range(N-20):
    if abs(b[k+20]/b[k]-1)<=0.05 and a[k+20]/a[k]-1<=-0.10: cnt[D[k][:4]]=cnt.get(D[k][:4],0)+1
print("  "+"  ".join(f"{y}:{cnt.get(y,0)}" for y in sorted({d[:4] for d in D})))
print(f"  中位币90天跑输BTC的比例(2021起): {np.mean([m[k+90]<m[k] for k in range(N-90) if D[k]>='2021-01-01']):.0%}")
# D
print("\n[D] 历史: 之前20天山寨总市值/BTC 跌>=5%, 之后BTC涨>=10% 时, 山寨能否跑赢 (2019起, 重叠窗口, 事后条件)")
for F in (30,60):
    R=np.array([(a[k+F]/a[k]-1,m[k+F]/m[k]-1) for k in range(20,N-F) if D[k]>='2019-01-15' and b[k+F]/b[k]-1>=0.10 and a[k]/a[k-20]-1<=-0.05])
    print(f"  未来{F}天 n={len(R)}: 山寨总市值跑赢 {np.mean(R[:,0]>0):.0%} (大幅>=+20%: {np.mean(R[:,0]>=0.2):.0%}) | 中位币跑赢 {np.mean(R[:,1]>0):.0%} (大幅: {np.mean(R[:,1]>=0.2):.0%})")
