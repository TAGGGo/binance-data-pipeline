"""BTC vs alts by BTC 全景 leg (segmentation accepted 2026-10-17). Daily.
Series: BTC price (Binance), BTC cap, alt cap ex stables (CoinGecko global via v_crypto_market_daily),
median coin = within each part, median over coins listed at the part start of price/price_at_start, chained across parts."""
import duckdb, json, statistics as st, sys
db, segs_f, out = sys.argv[1:4]
c=duckdb.connect(db,read_only=True)
cap={str(d):(b,a) for d,b,a in c.sql("select date, btc_mcap, total_mcap-btc_mcap-coalesce(stablecoin_mcap,0) from v_crypto_market_daily where btc_mcap is not null and total_mcap is not null").fetchall()}
px={}
for s,d,cl in c.sql("select symbol, ts::date, close from bn_kline_1d where market='spot'").fetchall(): px.setdefault(s,{})[str(d)]=cl
segs=json.load(open(segs_f))
import datetime as dt
today=dt.datetime.utcnow().date().isoformat()  # today's UTC candle is still open -> drop it
days=sorted(d for d in px["BTC"] if segs[0]["a"]<=d<today and d in cap)
last=days[-1]
for g in segs:
    if g is segs[-1]: g["b"]=last
med={}; M=1.0
for g in segs:
    a=g["a"]; coins=[s for s in px if s!="BTC" and a in px[s]]
    for d in days:
        if a<d<=g["b"]:
            r=[px[s][d]/px[s][a] for s in coins if d in px[s]]
            med[d]=M*st.median(r)
    M=med[g["b"]]
    b=g["b"]; rs=[px[s][b]/px[s][a]-1 for s in coins if b in px[s]]; btc=px["BTC"][b]/px["BTC"][a]-1
    g.update(btc=round((cap[b][0]/cap[a][0]-1)*100), alt=round((cap[b][1]/cap[a][1]-1)*100), med=round(st.median(rs)*100), beat=sum(r>btc for r in rs), n=len(rs), bp_a=round(px["BTC"][a]), bp_b=round(px["BTC"][b]))
med[days[0]]=1.0
S=[[d, round(px["BTC"][d]), round(cap[d][0]/1e9,1), round(cap[d][1]/1e9,1), round(med[d],4)] for d in days]
json.dump(dict(data_to=last, series=S, segs=[{k:g[k] for k in ("no","name","part","a","b","btc","alt","med","beat","n","bp_a","bp_b")} for g in segs]), open(out,"w"), ensure_ascii=False, separators=(",",":"))
for g in segs: print(g["no"],g["part"],g["a"],g["b"],g["btc"],g["alt"],g["med"],f'{g["beat"]}/{g["n"]}')
