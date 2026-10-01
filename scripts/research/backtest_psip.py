"""PSIP (percent supply in profit) tests, 2026-09-30 (Murphy: "PSIP only 78%->71%, want <65% or <60%").
PSIP = BGeometrics supply-profit / supply (sum of hodl waves). Matches Murphy: 78.5% on 9/21, 71.6% on 9/29.
(1) Episodes: PSIP crosses above HI after >=30d below. Within 120d: did price rise >=10% above the
    cross-day close BEFORE PSIP fell below LO ("ran without flush"), or did PSIP flush first?
(2) Buy signal: PSIP crosses below LO while price > 200d SMA (declustered 30d) -> forward returns vs
    all bull days (price > 200d SMA)."""
import pandas as pd, numpy as np
D="data/onchain/bgeometrics/"
rd=lambda f: pd.read_csv(D+f+".csv",parse_dates=["d"]).set_index("d").apply(pd.to_numeric,errors="coerce")
px=rd("btc-price")["btcPrice"]; sp=rd("supply-profit")["supplyProfitBtc"]; sup=rd("hodl-waves-supply").sum(axis=1)
df=pd.concat({"px":px,"sp":sp,"sup":sup},axis=1).dropna(); df=df[(df.px>0)&(df.sup>0)&(df.index>="2012-01-01")]
df["psip"]=df.sp/df.sup; df["ma200"]=df.px.rolling(200).mean()
P=df.px.values; S=df.psip.values; idx=df.index
def fw(i,h): return P[i+h]/P[i]-1 if i+h<len(P) else np.nan

print("== (1) after PSIP pops above HI: run first or flush first? (120d window)")
for HI,LO in ((0.75,0.65),(0.75,0.60),(0.78,0.65)):
    rows=[]; below=0
    for i in range(1,len(S)):
        below = below+1 if S[i-1]<HI else 0
        if S[i]>=HI and below>=30:
            res="none"
            for j in range(i+1,min(i+121,len(S))):
                if P[j]>=P[i]*1.10: res="ran"; break
                if S[j]<LO: res="flush"; break
            if i+120>=len(S) and res=="none": res="open"
            after=""
            if res=="flush":
                k=j; after=f"then 90d {fw(k,90):+.0%}" if k+90<len(P) else "then 90d n/a"
            rows.append((idx[i].date(),round(P[i]),res,f"90d {fw(i,90):+.0%}",after))
    c=pd.Series([r[2] for r in rows]).value_counts().to_dict()
    print(f"HI={HI} LO={LO}: {c}")
    if (HI,LO)==(0.75,0.65):
        for r in rows: print("  ",*r)

print("\n== (2) PSIP crosses below LO in a bull (price>200d SMA), declustered 30d")
bull=(df.px>df.ma200).values
def stats(ii,name):
    d={h:pd.Series([fw(i,h) for i in ii]).dropna() for h in (14,30,60,90)}
    dd=pd.Series([P[i+1:i+31].min()/P[i]-1 for i in ii if i+31<len(P)])
    print(f"{name:26s} n={len(ii):4d} "+" ".join(f"{h}d med {d[h].median():+.1%} up {(d[h]>0).mean():.0%}" for h in d)+f" | dd30 med {dd.median():+.1%}")
base=[i for i in range(200,len(P)) if bull[i]][::7]
stats(base,"BASE bull days (weekly)")
for LO in (0.70,0.65,0.60):
    ev=[];last=-999
    for i in range(201,len(S)):
        if bull[i] and S[i]<LO and S[i-1]>=LO:
            if i-last>=30: ev.append(i)
            last=i
    stats(ev,f"PSIP<{LO:.0%} in bull")
    stats([i for i in ev if idx[i]>=pd.Timestamp("2020-01-01")],f"  same, since 2020")
print("\nPSIP vs price lately (for the price that PSIP 65/60% maps to):")
print(df["2026-08-15":][["px","psip"]].iloc[::3].round(3).to_string())
