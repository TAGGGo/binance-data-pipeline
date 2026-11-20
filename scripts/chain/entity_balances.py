"""Exchange and miner balances from the raw chain tables + public labels (added 2026-10-31; replaces BGeometrics
exchange-reserve-btc / miner-reserve as an APPROXIMATION).
  exchanges = DefiLlama bitcoin-book CEX addresses + GraphSense tagpack seeds with category 'exchange'
              (labels/*.csv; add exchanges' proof-of-reserve address lists to labels/ to improve coverage)
  miners    = every address that ever received a coinbase output (addr != 0)
              + early pay-to-pubkey coinbase coins (no address; Satoshi era) that are still unspent at the spend-link cutoff
balance(d) = cumulative (outputs paid to the set) - (inputs spent from the set). Per-year aggregates cached on the drive
(<chain>/derived/entity/), the current year redone each run. Output: data/onchain/self/entity_daily.csv
Coverage is partial (no clustering / unlabeled exchange wallets): use the TREND, not the level."""
import csv, datetime as dt, glob, json, os, time
import duckdb
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CH = os.environ.get("MDH_CHAIN_DIR", "/Volumes/chain/datahub/chain"); R = os.path.join(CH, "raw"); LB = os.path.join(CH, "labels")
D = os.path.join(CH, "derived", "entity"); os.makedirs(D, exist_ok=True)
OUT = os.path.join(REPO, "data", "onchain", "self"); os.makedirs(OUT, exist_ok=True)
T0 = time.time(); BUDGET = float(os.environ.get("ENTITY_BUDGET", 1500))
def log(*x): print("entity:", *x, flush=True)
if not os.path.isdir(os.path.join(R, "tx")): log("chain drive not mounted"); raise SystemExit(0)
con = duckdb.connect(); con.execute("set memory_limit='%s'" % os.environ.get("ENTITY_MEM", "6GB")); con.execute("set preserve_insertion_order=false")
tmp = os.environ.get("ENTITY_TMP", "/tmp/entity_ddb"); os.makedirs(tmp, exist_ok=True); con.execute(f"set temp_directory='{tmp}'")
years = sorted({os.path.basename(f)[5:9] for f in glob.glob(os.path.join(R, "tx", "date=*.parquet"))})
cur = str(dt.datetime.utcnow().year)
# 1) miner address set (coinbase recipients), per year then unioned
for y in years:
    f = os.path.join(D, f"miner_addrs_{y}.parquet")
    if os.path.exists(f) and y != cur: continue
    if time.time() - T0 > BUDGET: log("budget: stop in miner addrs", y); raise SystemExit(0)
    con.execute(f"""copy (select distinct o.addr from read_parquet('{R}/outputs/date={y}-*.parquet') o
      semi join (select tx from read_parquet('{R}/tx/date={y}-*.parquet') where coinbase) t on o.tx = t.tx where o.addr <> 0) to '{f}' (format parquet)""")
    log("miner addrs", y)
con.execute(f"create table miner as select distinct addr from read_parquet('{D}/miner_addrs_*.parquet')")
ex = []
for fn, cond in (("btc_cex_defillama.csv", "true"), ("btc_entity_seeds_graphsense.csv", "category = 'exchange'")):
    p = os.path.join(LB, fn)
    if os.path.exists(p): ex.append(f"select addr::bigint addr from read_csv('{p}', header=true, all_varchar=true) where {cond}")
extra = glob.glob(os.path.join(LB, "btc_por_*.csv"))   # proof-of-reserve lists, same format (addr column = aws_extract.addr_key)
ex += [f"select addr::bigint addr from read_csv('{p}', header=true, all_varchar=true)" for p in extra]
ex += [f"select addr from read_parquet('{p}')" for p in glob.glob(os.path.join(LB, "btc_exch_cluster*.parquet"))]   # exch_cluster_labels.py
con.execute("create table exch as select distinct addr from (" + " union all ".join(ex) + ") where addr <> 0")
nm, ne = con.execute("select count(*) from miner").fetchone()[0], con.execute("select count(*) from exch").fetchone()[0]
sig = f"{nm}:{ne}"   # past-year caches (< cur) only depend on exchange label set size (ne); miner_addrs_<y> for past years is immutable
# 2) daily credits/debits per set, per year
for y in years:
    f = os.path.join(D, f"flows_{y}.parquet"); sf = f + ".sig"
    if os.path.exists(f) and y != cur and os.path.exists(sf) and open(sf).read().strip().split(":")[-1] == str(ne): continue
    if time.time() - T0 > BUDGET: log("budget: stop in flows", y); raise SystemExit(0)
    t = time.time()
    con.execute(f"""copy (
      with o as (select ts::date d, addr, sats from read_parquet('{R}/outputs/date={y}-*.parquet') where addr <> 0),
           i as (select ts::date d, addr, sats from read_parquet('{R}/inputs/date={y}-*.parquet') where addr <> 0)
      select d, 'miner' s, sum(sats)::double / 1e8 btc from o semi join miner using (addr) group by 1
      union all select d, 'miner', -sum(sats)::double / 1e8 from i semi join miner using (addr) group by 1
      union all select d, 'exch', sum(sats)::double / 1e8 from o semi join exch using (addr) group by 1
      union all select d, 'exch', -sum(sats)::double / 1e8 from i semi join exch using (addr) group by 1) to '{f}' (format parquet)""")
    open(sf, "w").write(sig)
    log(f"flows {y} {time.time() - t:.0f}s")
# 2b) coinbase outputs never moved = Glassnode "Miner Unspent Supply" ("the total amount sitting in coinbase outputs that
#     have never been moved since issuance"). Created per day from the tx/outputs tables; spends found by scanning each year's
#     inputs against the (small) set of coinbase outputs.
cbo = os.path.join(D, "cb_outputs.parquet")
if True:   # past years cached per file; the current year and the union are rebuilt every run (small)
    parts = []
    for y in years:
        f = os.path.join(D, f"cb_out_{y}.parquet")
        if not os.path.exists(f) or y == cur:
            con.execute(f"""copy (select o.tx, o.n, o.ts::date d, o.sats from read_parquet('{R}/outputs/date={y}-*.parquet', union_by_name=true) o
                semi join (select tx from read_parquet('{R}/tx/date={y}-*.parquet') where coinbase) t on o.tx = t.tx where o.sats > 0) to '{f}' (format parquet)""")
    con.execute(f"copy (select * from read_parquet('{D}/cb_out_*.parquet')) to '{cbo}' (format parquet)")
    open(cbo + ".upto", "w").write(years[-1])
con.execute(f"create or replace table cbk as select tx, n from read_parquet('{cbo}')")
for y in years:
    f = os.path.join(D, f"cb_spent_{y}.parquet")
    if os.path.exists(f) and y != cur: continue
    if time.time() - T0 > BUDGET: log("budget: stop in coinbase spends", y); raise SystemExit(0)
    con.execute(f"""copy (select i.ts::date d, sum(i.sats)::double / 1e8 btc from read_parquet('{R}/inputs/date={y}-*.parquet') i
        semi join cbk on i.prev_tx = cbk.tx and i.prev_n = cbk.n group by 1) to '{f}' (format parquet)""")
    log("coinbase spends", y)
cb = {}
for d, b in con.execute(f"select d, sum(sats)::double / 1e8 from read_parquet('{cbo}') group by 1").fetchall():
    cb[str(d)] = cb.get(str(d), 0.0) + b
for d, b in con.execute(f"select d, sum(btc) from read_parquet('{D}/cb_spent_*.parquet') group by 1").fetchall():
    cb[str(d)] = cb.get(str(d), 0.0) - b

# 3) early P2PK coinbase coins still unspent at the spend-link cutoff (no address -> invisible to the address sets)
p2pk = {}
L = os.path.join(CH, "derived", "link"); bj = os.path.join(L, "base.json")
if os.path.exists(bj):
    C = json.load(open(bj))["cutoff"][:7]
    for d, b in con.execute(f"""select (date '2009-01-03' + u.cd)::date d, sum(u.sats)::double / 1e8 from read_parquet('{L}/utxo_{C}/*.parquet') u
        semi join (select o.tx, o.n from read_parquet('{R}/outputs/date=201[0-2]-*.parquet', union_by_name=true) o
                   semi join (select tx from read_parquet('{R}/tx/date=201[0-2]-*.parquet') where coinbase) t on o.tx = t.tx where o.addr = 0
                   union all
                   select o.tx, o.n from read_parquet('{R}/outputs/date=2009-*.parquet', union_by_name=true) o
                   semi join (select tx from read_parquet('{R}/tx/date=2009-*.parquet') where coinbase) t on o.tx = t.tx where o.addr = 0) k
        on u.tx = k.tx and u.n = k.n group by 1""").fetchall():
        p2pk[str(d)] = b
rows = con.execute(f"""select d, sum(case when s = 'miner' then btc else 0 end), sum(case when s = 'exch' then btc else 0 end)
  from read_parquet('{D}/flows_*.parquet') group by d order by d""").fetchall()
mb = eb = pb = ub = 0.0; out = []
pk = sorted(p2pk); j = 0; ck = sorted(cb); jc = 0
for d, m, e in rows:
    ds = str(d)
    while j < len(pk) and pk[j] <= ds: pb += p2pk[pk[j]]; j += 1
    while jc < len(ck) and ck[jc] <= ds: ub += cb[ck[jc]]; jc += 1
    mb += m; eb += e
    if ds < dt.datetime.utcnow().date().isoformat():
        out.append([ds, round(eb, 2), round(mb, 2), round(pb, 2), round(mb + pb, 2), round(ub, 2)])
with open(os.path.join(OUT, "entity_daily.csv"), "w", newline="") as f:
    w = csv.writer(f); w.writerow(["d", "exchange_btc", "miner_addr_btc", "miner_p2pk_cb_btc", "miner_btc", "miner_unspent_cb_btc"]); w.writerows(out)
log(f"labels: {nm:,} miner addrs, {ne:,} exchange addrs; last {out[-1]}")
