"""Dynamic alt index vs BTC from CoinMarketCap weekly snapshots (2026-09-30).
Input: data/backfill/cmc_weekly_top300.csv.gz (CMC data-api listings/historical, top 300 each Sunday
2018-01-07..2026-09-27, fetched from the cloud session; refresh = same endpoint:
https://api.coinmarketcap.com/data-api/v3/cryptocurrency/listings/historical?date=YYYY-MM-DD&limit=300&start=1&convertId=2781).
Indices, rebuilt each week from the top-N by market cap ex BTC/ETH/stables/wrapped/LST:
 cwN = cap-weighted, ewN = equal-weighted, chain-linked weekly returns (what a holder of the top N gets;
 no new-listing inflation). TOTAL3raw = sum of top-100 caps / BTC cap (TradingView-TOTAL3 style, includes
 new listings and supply growth = the "水分").
Prints position vs 29w (~200d) / 7w (~50d) MAs and past reclaims of the 200d after >= 17 weeks below."""
import pandas as pd, numpy as np
EXCL=set("""USDT USDC BUSD DAI TUSD PAX USDP GUSD HUSD USDK SUSD FRAX LUSD USDD FDUSD PYUSD USDE SUSDE USDS USD1 BSC-USD USDB USDX USDN UST USTC EURS EURT XAUT PAXG DGX WBTC WETH STETH WSTETH WEETH CBBTC BTCB RENBTC HBTC TBTC LBTC SOLVBTC RETH METH CBETH JITOSOL BNSOL MSOL EZETH RSETH OSETH WBETH BETH SAVAX STSOL BUIDL USDTB USYC OUSG USD0 RLUSD USDF USDG CLBTC WBNB WTRX WHYPE BGB LEO""".split())
d=pd.read_csv("data/backfill/cmc_weekly_top300.csv.gz"); d["symbol"]=d.symbol.str.upper()
snaps={t:g.set_index("cmc_id") for t,g in d.groupby("date")}; dates=sorted(snaps)
def elig(s):
    btc=s[s.symbol=="BTC"].price_usd.iloc[0]; eth=s[s.symbol=="ETH"].price_usd.iloc[0]
    e=s[~s.symbol.isin(EXCL|{"BTC","ETH"}) & (s.market_cap_usd>0) & (s.price_usd>0)]
    e=e[~e.price_usd.between(0.97,1.03) & ((e.price_usd/btc-1).abs()>=0.05) & ((e.price_usd/eth-1).abs()>=0.05)]
    return e.sort_values("market_cap_usd",ascending=False)
rows=[]
for a,b in zip(dates[:-1],dates[1:]):
    sa,sb=snaps[a],snaps[b]; el=elig(sa); rec={"date":b,"btc":sb[sb.symbol=="BTC"].price_usd.iloc[0]/sa[sa.symbol=="BTC"].price_usd.iloc[0]-1}
    for N in (20,50,100):
        t=el.head(N); t=t[t.index.isin(sb.index)]; r=(sb.loc[t.index,"price_usd"]/t.price_usd-1).clip(-0.95,5)
        rec[f"cw{N}"]=(r*t.market_cap_usd).sum()/t.market_cap_usd.sum(); rec[f"ew{N}"]=r.mean()
    rec["t3raw"]=elig(sb).head(100).market_cap_usd.sum()/sb[sb.symbol=="BTC"].market_cap_usd.iloc[0]
    rows.append(rec)
df=pd.DataFrame(rows).set_index("date"); df.index=pd.to_datetime(df.index)
S={c:np.log(1+df[c]).cumsum()-np.log(1+df.btc).cumsum() for c in ("cw20","ew20","cw100","ew100")}; S["TOTAL3raw"]=np.log(df.t3raw)
for c,s in S.items():
    ma=s.rolling(29).mean(); m7=s.rolling(7).mean(); ab=(s>ma)[ma.notna()]
    print(f"{c:10s} vs200d {np.exp(s.iloc[-1]-ma.iloc[-1])-1:+.1%} vs50d {np.exp(s.iloc[-1]-m7.iloc[-1])-1:+.1%} 50d-200d {np.exp(m7.iloc[-1]-ma.iloc[-1])-1:+.1%} since2018 {np.exp(s.iloc[-1]-s.iloc[0])-1:+.0%} ATL {s.idxmin().date()}")
    below=0
    for i in range(1,len(ab)):
        below=below+1 if not ab.iloc[i-1] else 0
        if ab.iloc[i] and below>=17:
            j=s.index.get_loc(ab.index[i]); f=lambda h: f"{np.exp(s.iloc[j+h]-s.iloc[j])-1:+.0%}" if j+h<len(s) else "n/a"
            print(f"   reclaim {ab.index[i].date()} after {below}w: 13w {f(13)} 26w {f(26)} 52w {f(52)}")
