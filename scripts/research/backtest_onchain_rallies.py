"""Ad hoc (2026-09-30): every +40% rally off a low >=50% below a cycle top, metrics on the day +40% is reached, and whether a lower low followed (bear rally) or not. Unreviewed."""
import pandas as pd, numpy as np
D="data/onchain/bgeometrics"
def col(n,c=None):
    m=pd.read_csv(f"{D}/{n}.csv",parse_dates=["d"]).set_index("d"); return pd.to_numeric(m[c or m.columns[0]],errors="coerce")
df=pd.DataFrame({"px":col("btc-price")})
for n in ["sth-mvrv","mvrv","nupl","true-market-mean","realized-price","sth-realized-price","lth-realized-price"]: df[n]=col(n)
df["lth"]=col("accumulation-trend-score","lthChange30d"); df["sth"]=col("accumulation-trend-score","sthChange30d")
df=df[df.px>0].asfreq("D").ffill()
tops=[pd.Timestamp(x) for x in ["2011-06-08","2013-12-04","2017-12-16","2021-11-08","2025-10-06"]]
nxt={tops[i]:(tops[i+1] if i+1<len(tops) else None) for i in range(len(tops))}
rows=[]
for t in tops[1:]:
    end=nxt[t] or df.index[-1]; w=df.px[t:end]; topv=df.px[t]
    runmin=w.cummin(); armed=True
    for d in w.index:
        lo=runmin[d]; 
        if w[d]<=lo: armed=True
        if armed and w[d]>=lo*1.4 and topv/lo-1>=1.0:   # rally +40% off a low that is >=50% below top
            lod=w[t:d].idxmin(); after=w[d:]
            lower=after[after<lo]
            outcome="BEAR RALLY (lower low later)" if len(lower) else "no lower low (bottom was in)"
            r=df.loc[d]
            rows.append(dict(top=t.date(),low=lod.date(),lowpx=round(lo),rally_day=d.date(),days_since_top=(d-t).days,dd_at_low=f"{lo/topv-1:.0%}",
               sth_mvrv=round(r['sth-mvrv'],2),mvrv=round(r.mvrv,2),nupl=round(r.nupl,2),px_tmm=round(r.px/r['true-market-mean'],2),
               px_rp=round(r.px/r['realized-price'],2),lth30k=round(r.lth/1e3),outcome=outcome, next_low=(lower.index[0].date() if len(lower) else None)))
            armed=False
out=pd.DataFrame(rows); pd.set_option("display.width",250); print(out.to_string(index=False))
