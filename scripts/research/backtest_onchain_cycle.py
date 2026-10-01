"""Medium/long-term BTC on-chain test: do overheated signals come before cycle tops / big drawdowns?
Data: BGeometrics CSVs. Unreviewed. See claude/backtests.md section 6.
  python3 scripts/research/backtest_onchain_cycle.py
"""
import pandas as pd, numpy as np
D="data/onchain/bgeometrics"
def col(n,c=None):
    m=pd.read_csv(f"{D}/{n}.csv",parse_dates=["d"]).set_index("d"); return pd.to_numeric(m[c or m.columns[0]],errors="coerce")
df=pd.DataFrame({"px":col("btc-price")})
for n in ["sth-mvrv","mvrv","mvrv-zscore","nupl","puell-multiple","nrpl-usd","realized-profit","realized-price","lth-realized-price","sth-realized-price","true-market-mean","sth-sopr","cdd","vdd-multiple"]: df[n]=col(n)
df["lthchg"]=col("accumulation-trend-score","lthChange30d")
df=df[(df.px>0)&(df.index>="2012-06-01")].sort_index()
df=df.asfreq("D").ffill()
df["nrpl30_pct"]=(df["nrpl-usd"].rolling(30).sum()/df["realized-price"]).expanding(365).rank(pct=True)
df["rp30_pct"]=(df["realized-profit"].rolling(30).sum()/df["realized-price"]).expanding(365).rank(pct=True)
px=df.px
# --- tops: local max followed by a drawdown >= X before price exceeds it
def tops(dd):
    out=[]; peak_i=0; arr=px.values; idx=px.index; i=0; n=len(arr)
    runmax=arr[0]; runmax_i=0
    for i in range(1,n):
        if arr[i]>runmax: runmax, runmax_i = arr[i], i
        elif arr[i] <= runmax*(1-dd):
            if not out or out[-1]!=runmax_i: out.append(runmax_i)
            # reset: wait for a new high above this peak? no: start new search from here
            runmax, runmax_i = arr[i], i
    return [idx[j] for j in out]
T30=tops(0.3)
T50=[pd.Timestamp(x) for x in ["2013-12-04","2017-12-16","2021-04-13","2021-11-08","2025-10-06"]]  # major cycle tops (>=50% drawdown after)
print("Major tops:", [(t.date(), round(px[t])) for t in T50]); print("n tops followed by >=30% drawdown:", len(T30))
S={
 "STH-MVRV>1.3":df["sth-mvrv"]>1.3, "STH-MVRV>1.5":df["sth-mvrv"]>1.5,
 "MVRV Z>3":df["mvrv-zscore"]>3, "MVRV Z>5":df["mvrv-zscore"]>5, "MVRV>2.5":df.mvrv>2.5, "MVRV>3":df.mvrv>3,
 "NUPL>0.6":df.nupl>0.6, "NUPL>0.5":df.nupl>0.5, "Puell>3":df["puell-multiple"]>3, "Puell>2":df["puell-multiple"]>2,
 "NRPL30 top10%":df.nrpl30_pct>0.9, "RealProfit30 top10%":df.rp30_pct>0.9, "VDD mult>2.5":df["vdd-multiple"]>2.5,
 "LTH sell >200K/30d":df.lthchg<-200_000,
}
print("\n(A) At each >=50% cycle top: value at top, and first day ON within the 365d run-up (days before top, % gain still ahead)")
cols=["sth-mvrv","mvrv","mvrv-zscore","nupl","puell-multiple","nrpl30_pct","rp30_pct","vdd-multiple"]
print("\npeak value in 120d before top:"); print(pd.DataFrame({t.date():df.loc[t-pd.Timedelta(days=120):t,cols].max() for t in T50}).T.round(2).to_string())
for name,c in S.items():
    row=[]
    for t in T50:
        w=c[t-pd.Timedelta(days=365):t]; on=w[w]
        if len(on): f=on.index[0]; l=on.index[-1]; row.append(f"{(t-f).days:>3}d {px[t]/px[f]-1:+5.0%} L{(t-l).days:>3}")
        else: row.append("   --          ")
    print(f"{name:<22}"," | ".join(row))
# (B) events: forward 365d: max gain, max drawdown from forward peak, does a >=30% top (T30) occur within 365d
print("\n(B) Declustered events (first ON after >=60d OFF): within next 365d, P(a >=30% drawdown top), P(a >=50% cycle top), median % gain to the max, median days to max")
base_days=df.index[df.index<=df.index[-1]-pd.Timedelta(days=365)]
def stat(ix):
    r=[]
    for d in ix:
        end=d+pd.Timedelta(days=365)
        if end>df.index[-1]: continue
        t30=any(d<=t<=end for t in T30); t50=any(d<=t<=end for t in T50)
        w=px[d:end]; mx=w.max(); r.append((t30,t50,mx/px[d]-1,(w.idxmax()-d).days, px[end]/px[d]-1))
    r=pd.DataFrame(r,columns=["t30","t50","gain","days","r365"])
    return r
b=stat(base_days[::7])
print(f"{'BASE (weekly sample)':<22} n={len(b):>3} top30 {b.t30.mean():.0%} top50 {b.t50.mean():.0%} gain-to-max {b.gain.median():+.0%} days {b.days.median():.0f} 365d-return win {(b.r365>0).mean():.0%} med {b.r365.median():+.0%}")
for name,c in S.items():
    c=c.fillna(False).astype(bool); prev=c.shift(1,fill_value=False).rolling(60,min_periods=1).max().astype(bool)
    ix=df.index[c&~prev]; r=stat(ix)
    if len(r)==0: print(f"{name:<22} n=0"); continue
    print(f"{name:<22} n={len(r):>3} top30 {r.t30.mean():.0%} top50 {r.t50.mean():.0%} gain-to-max {r.gain.median():+.0%} days {r.days.median():.0f} 365d-return win {(r.r365>0).mean():.0%} med {r.r365.median():+.0%}   ({', '.join(str(d.date()) for d in ix[-4:])})")
last=df.iloc[-1]; print("\nNOW", df.index[-1].date(), last[cols].round(2).to_dict())
