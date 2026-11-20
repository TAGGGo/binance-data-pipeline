"""Daily miner revenue + Puell multiple + thermocap from the raw chain tx table (added 2026-10-31; replaces BGeometrics
puell-multiple). revenue = sum over the day's blocks of subsidy(height) + all fees paid that day.
USD at the day's avg price (o+h+l+c)/4 (same price as our URPD). Puell = revenue USD / its 365-day mean.
thermocap = cumulative revenue USD since genesis (cointime, used for the True Market Mean).
Output: data/onchain/self/miner_daily.csv  d, blocks, subsidy_btc, fees_btc, rev_btc, rev_usd, puell, thermocap_usd."""
import csv, os, datetime as dt
import duckdb
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CH = os.environ.get("MDH_CHAIN_DIR", "/Volumes/chain/datahub/chain"); R = os.path.join(CH, "raw")
URPD_PX = os.environ.get("URPD_PRICE", os.path.expanduser("~/Projects/btc-volatility/urpd/price.csv"))
OUT = os.path.join(REPO, "data", "onchain", "self"); os.makedirs(OUT, exist_ok=True)
def log(*x): print("miner:", *x, flush=True)
if not os.path.isdir(os.path.join(R, "tx")):
    log("chain drive not mounted"); raise SystemExit(0)
con = duckdb.connect()
con.execute(f"attach '{os.path.join(REPO, 'data', 'market.duckdb')}' as m (read_only)")
con.execute("create table px as select ts::date d, (open+high+low+close)/4 p from m.bn_kline_1d where symbol='BTC' and market='spot'")
if os.path.exists(URPD_PX):
    con.execute(f"insert into px select d::date, price from read_csv('{URPD_PX}', header=true) where d::date < (select min(d) from px)")
con.execute("detach m")
# per-year aggregates cached on the drive (only the current year is redone each run)
CD = os.path.join(CH, "derived", "miner"); os.makedirs(CD, exist_ok=True)
import glob, time
T0 = time.time(); BUDGET = float(os.environ.get("MINER_BUDGET", 1200))
years = sorted({os.path.basename(f)[5:9] for f in glob.glob(os.path.join(R, "tx", "date=*.parquet"))})
for y in years:
    f = os.path.join(CD, f"year={y}.parquet")
    if os.path.exists(f) and int(y) < dt.datetime.utcnow().year - (1 if dt.datetime.utcnow().month == 1 else 0) and y not in os.environ.get("MINER_REDO", ""):
        continue
    if time.time() - T0 > BUDGET:
        log("budget used before", y); break
    con.execute(f"""copy (
      with t as (select ts::date d, block, fee_sats, coinbase from read_parquet('{R}/tx/date={y}-*.parquet')),
      b as (select d, block from t where coinbase group by 1, 2),
      s as (select d, count(*) blocks, sum((5000000000 >> (block // 210000)::int))::double / 1e8 sub from b group by d),
      f as (select d, sum(fee_sats)::double / 1e8 fees from t where not coinbase group by d)
      select s.d, s.blocks, s.sub, coalesce(f.fees, 0) fees from s left join f using (d)) to '{f}' (format parquet)""")
    log("year", y, "done")
rows = con.execute(f"""select a.d, a.blocks, a.sub, a.fees, px.p from read_parquet('{CD}/year=*.parquet') a left join px using (d)
  where a.d < current_date order by a.d""").fetchall()
out, hist, cum = [], [], 0.0
for d, n, sub, fee, p in rows:
    rev = sub + fee; usd = rev * (p or 0.0); cum += usd
    hist.append(usd); w = hist[-365:]; ma = sum(w) / len(w)
    out.append([d, n, round(sub, 4), round(fee, 6), round(rev, 4), round(usd, 2), round(usd / ma, 4) if ma > 0 and len(w) == 365 else "", round(cum, 2)])
with open(os.path.join(OUT, "miner_daily.csv"), "w", newline="") as f:
    w = csv.writer(f); w.writerow(["d", "blocks", "subsidy_btc", "fees_btc", "rev_btc", "rev_usd", "puell", "thermocap_usd"]); w.writerows(out)
log(f"{out[0][0]}..{out[-1][0]} ({len(out)} days); last puell {out[-1][6]}, rev {out[-1][4]} BTC, thermocap ${out[-1][7]/1e9:.1f}B")
