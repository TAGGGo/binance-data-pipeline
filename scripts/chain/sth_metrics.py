"""Short-term-holder (STH) metrics from the local raw chain tables (data on /Volumes/chain; added 2026-10-31, replaces
BGeometrics sth-realized-price / sth-sopr / sth-mvrv / realized_profit_sth / realized_loss_sth).
STH = coins younger than 155 days (Glassnode convention). Cost = BTC daily avg price (o+h+l+c)/4 on the creation day
(same as our URPD); spend price = daily avg on the spend day. Raw outputs, no entity adjustment.
Method: per creation month M, join outputs(M) with inputs over [M, M_end+156d] on (tx,n)=(prev_tx,prev_n) ->
  events : +sats/+usd_cost on the creation day, - on min(spend day, creation day + 155d)   (cumsum = STH supply / realized cap)
  spends : coins spent before turning 155d, by spend day: sats, usd at spend, usd at cost, profit, loss  (STH-SOPR, realized P/L)
Per-month results in <chain>/derived/sth/{events,spends}_YYYY-MM.parquet; months whose 155-day window is closed are never
redone. Then data/onchain/self/sth_daily.csv (d, sth_supply_btc, sth_rp, sth_mvrv, sth_sopr, sth_profit_usd, sth_loss_usd)
+ self/sth_live.json (live price vs last complete day). Valid from 2018-01 (raw starts 2017-07-16).
  python3 scripts/chain/sth_metrics.py [--from 2017-08] [--budget 160]"""
import argparse, csv, datetime as dt, json, os, time
import duckdb
ap = argparse.ArgumentParser(); ap.add_argument("--from", dest="frm", default="2010-08"); ap.add_argument("--budget", type=float, default=1500)
a = ap.parse_args()
T0 = time.time()
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CH = os.environ.get("MDH_CHAIN_DIR", "/Volumes/chain/datahub/chain")
R = os.path.join(CH, "raw"); D = os.path.join(CH, "derived", "sth"); os.makedirs(D, exist_ok=True)
OUT = os.path.join(REPO, "data", "onchain", "self"); os.makedirs(OUT, exist_ok=True)
URPD_PX = os.environ.get("URPD_PRICE", os.path.expanduser("~/Projects/btc-volatility/urpd/price.csv"))
def log(*x): print("sth:", *x, flush=True)
if not os.path.isdir(os.path.join(R, "inputs")):
    log("chain drive not mounted:", R); raise SystemExit(0)

con = duckdb.connect()
con.execute("set memory_limit='%s'" % os.environ.get("STH_MEM", "3GB"))
tmp = os.environ.get("STH_TMP", "/tmp/sth_ddb"); os.makedirs(tmp, exist_ok=True); con.execute(f"set temp_directory='{tmp}'"); con.execute("set preserve_insertion_order=false")
con.execute(f"attach '{os.path.join(REPO, 'data', 'market.duckdb')}' as m (read_only)")
con.execute("create table px as select ts::date d, (open+high+low+close)/4 p, close from m.bn_kline_1d where symbol='BTC' and market='spot'")
if os.path.exists(URPD_PX):
    con.execute(f"insert into px select d::date, price, price from read_csv('{URPD_PX}', header=true) where d::date < (select min(d) from px) and price > 0")
lts, lp = con.execute("select ts, close from m.bn_kline_1h where symbol='BTC' and market='spot' order by ts desc limit 1").fetchone()
con.execute("detach m")
today = dt.datetime.utcnow().date()

def months(frm):
    y, mo = map(int, frm.split("-")); out = []
    while (y, mo) <= (today.year, today.month):
        out.append(dt.date(y, mo, 1)); y, mo = (y + 1, 1) if mo == 12 else (y, mo + 1)
    return out
def files(kind, d0, d1):
    fs = []; d = d0
    while d <= d1:
        f = os.path.join(R, kind, f"date={d}.parquet")
        if os.path.exists(f): fs.append(f)
        d += dt.timedelta(days=1)
    return fs

done = 0
for m0 in months(a.frm):
    m1 = (m0.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
    wend = m1 + dt.timedelta(days=156)
    tag = m0.strftime("%Y-%m"); ev = os.path.join(D, f"events_{tag}.parquet"); sp = os.path.join(D, f"spends_{tag}.parquet")
    closed = wend < today - dt.timedelta(days=1)
    if closed and os.path.exists(ev) and os.path.exists(sp):
        continue
    if time.time() - T0 > a.budget:
        log("budget used; stopping before", tag); break
    of = files("outputs", m0, m1); inf = files("inputs", m0, min(wend, today))
    if not of:
        continue
    t = time.time()
    lst = lambda fs: "[" + ",".join("'%s'" % f for f in fs) + "]"
    J = f"""(select o.ts::date cd, o.sats, i.ts::date sd, o.ts cts, i.ts sts
      from (select tx, n, ts, sats from read_parquet({lst(of)}) where sats > 0) o
      left join (select prev_tx, prev_n, ts from read_parquet({lst(inf)})) i on o.tx = i.prev_tx and o.n = i.prev_n)"""
    # events: aggregate per (creation day, end day) first (small), then price it
    con.execute(f"""copy (
      with g as (select cd, least(coalesce(sd, cd + 155), cd + 155) ed, sum(sats)::double / 1e8 btc from {J} j group by 1, 2),
      e as (select cd d, btc, btc * pc.p usd from g join px pc on pc.d = g.cd
            union all select ed, -btc, -btc * pc.p from g join px pc on pc.d = g.cd)
      select d, sum(btc) btc, sum(usd) usd from e group by d order by d) to '{ev}' (format parquet)""")
    con.execute(f"""copy (
      with g as (select cd, sd, sum(sats)::double / 1e8 btc from {J} j
                 where sd is not null and sts < cts + interval 155 day and sd > cd group by 1, 2)
      select g.sd d, sum(btc) btc, sum(btc * ps.p) usd_spent, sum(btc * pc.p) usd_cost,
             sum(greatest(btc * (ps.p - pc.p), 0)) profit, sum(greatest(btc * (pc.p - ps.p), 0)) loss
      from g join px pc on pc.d = g.cd join px ps on ps.d = g.sd group by 1 order by 1) to '{sp}' (format parquet)""")
    done += 1
    log(f"{tag}: {len(of)} output days, {len(inf)} input days, {time.time() - t:.0f}s{'' if closed else ' (window open)'}")

# assemble: only days whose whole 155-day lookback is covered by computed months (contiguous run ending this month)
have = {f[7:14] for f in os.listdir(D) if f.startswith("events_")} & {f[7:14] for f in os.listdir(D) if f.startswith("spends_")}
ms = months(a.frm); k = len(ms) - 1
while k >= 0 and ms[k].strftime("%Y-%m") in have: k -= 1
if k == len(ms) - 1:
    log("current month not computed yet; nothing to assemble"); raise SystemExit(0)
first_m = ms[k + 1]; valid_from = first_m + dt.timedelta(days=156)
use = [os.path.join(D, f"{kind}_{m.strftime('%Y-%m')}.parquet") for m in ms[k + 1:] for kind in ("events",)]
uses = [os.path.join(D, f"spends_{m.strftime('%Y-%m')}.parquet") for m in ms[k + 1:]]
evs = "[" + ",".join("'%s'" % f for f in use) + "]"; sps = "[" + ",".join("'%s'" % f for f in uses) + "]"
rows = con.execute(f"""
  with e as (select d, sum(btc) btc, sum(usd) usd from read_parquet({evs}) group by d),
  c as (select d, sum(btc) over w sup, sum(usd) over w rcap from e window w as (order by d rows between unbounded preceding and current row)),
  s as (select d, sum(btc) btc, sum(usd_spent) us, sum(usd_cost) uc, sum(profit) pr, sum(loss) lo from read_parquet({sps}) group by d)
  select c.d, c.sup, c.rcap / c.sup rp, px.close / (c.rcap / c.sup), s.us / s.uc, s.pr, s.lo, px.close
  from c join px on px.d = c.d left join s on s.d = c.d
  where c.d >= '{valid_from}' and c.d < current_date order by c.d""").fetchall()
with open(os.path.join(OUT, "sth_daily.csv"), "w", newline="") as f:
    w = csv.writer(f); w.writerow(["d", "close", "sth_supply_btc", "sth_rp", "sth_mvrv", "sth_sopr", "sth_profit_usd", "sth_loss_usd"])
    for d, sup, rp, mv, so, pr, lo, c in rows:
        w.writerow([d, round(c, 2), round(sup, 2), round(rp, 2), round(mv, 4), round(so, 5) if so else "", round(pr or 0), round(lo or 0)])
last = rows[-1]
json.dump({"computed_utc": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M"), "price": round(lp, 2), "price_hour_utc": str(lts)[:16],
           "sth_rp_day": str(last[0]), "sth_rp": round(last[2], 2), "sth_mvrv_live": round(lp / last[2], 4),
           "sth_supply_btc": round(last[1], 2), "sth_sopr_day": round(last[4], 5) if last[4] else None},
          open(os.path.join(OUT, "sth_live.json"), "w"), indent=1)
log(f"{done} months computed; daily to {last[0]}: STH-RP {last[2]:.0f}, STH supply {last[1]:,.0f} BTC, SOPR {last[4]}; live MVRV {lp / last[2]:.3f}")
