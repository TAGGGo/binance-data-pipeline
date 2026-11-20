"""Spend link: for every spent output, (creation day, spend day, BTC). Foundation for coin-age metrics (CDD, liveliness,
True Market Mean, LTH/STH SOPR, HODL waves). Added 2026-10-31 (Avalon: replace all BGeometrics metrics we can).
Raw tables (aws_extract): outputs(tx, n, ts, sats, ...), inputs(prev_tx, prev_n, ts, sats, ...); tx = int64 key.
Stages (all resumable, outputs under <chain>/derived/link):
 P  partition each complete month into 64 hash buckets of (tx) / (prev_tx):  o/m=YYYY-MM/b=XX/*.parquet (tx,n,cd,sats),
    i/m=YYYY-MM/b=XX/*.parquet (prev_tx,prev_n,sd,sats), created/m=YYYY-MM.parquet (cd, btc) ; cd/sd = days since 2009-01-03
 J  base at cutoff C (= end of the last complete month): per bucket join -> agg_C/b=XX.parquet (cd, sd, btc) and the unspent
    set utxo_C/b=XX.parquet (tx, n, cd, sats). Rebuilt when a new month completes (~monthly; heavy).
 I  days after C (to yesterday): inputs(C+1..) x (utxo_C + outputs(C+1..)) -> inc.parquet (cd, sd, btc)   (light, every run)
Run on the Mac (native duckdb, more memory) from run_update.sh via scripts/ops/spawn_heavy.py with a time budget.
  python3 scripts/chain/spend_link.py [--budget 3300] [--to 2010-12]   (--to = test cutoff month)"""
import argparse, datetime as dt, glob, json, os, shutil, time
import duckdb
ap = argparse.ArgumentParser(); ap.add_argument("--budget", type=float, default=3300); ap.add_argument("--to", default=None)
a = ap.parse_args(); T0 = time.time()
CH = os.environ.get("MDH_CHAIN_DIR", "/Volumes/chain/datahub/chain"); R = os.path.join(CH, "raw"); L = os.path.join(CH, "derived", "link")
NB = 64; G = dt.date(2009, 1, 3)
def log(*x): print("link:", *x, flush=True)
def left(): return a.budget - (time.time() - T0)
if not os.path.isdir(os.path.join(R, "inputs")):
    log("chain drive not mounted"); raise SystemExit(0)
os.makedirs(L, exist_ok=True)
con = duckdb.connect()
con.execute("set memory_limit='%s'" % os.environ.get("LINK_MEM", "8GB")); con.execute("set preserve_insertion_order=false")
tmp = os.environ.get("LINK_TMP", "/tmp/link_ddb"); os.makedirs(tmp, exist_ok=True); con.execute(f"set temp_directory='{tmp}'")
today = dt.datetime.utcnow().date()
cut_m = dt.date(today.year, today.month, 1) - dt.timedelta(days=1)          # end of last complete month
if a.to:
    y, m = map(int, a.to.split("-")); cut_m = (dt.date(y, m, 28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
C = cut_m.isoformat(); CTAG = cut_m.strftime("%Y-%m")
def months(to):
    out, d = [], dt.date(2009, 1, 1)
    while d <= to:
        out.append(d); d = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return out
def mfiles(kind, m0):
    fs = sorted(glob.glob(os.path.join(R, kind, f"date={m0.strftime('%Y-%m')}-*.parquet")))
    return "[" + ",".join("'%s'" % f for f in fs) + "]" if fs else None
B = lambda col: f"((({col} % {NB}) + {NB}) % {NB})::int"

# ---- P: partition complete months ----
for m0 in months(cut_m):
    tag = m0.strftime("%Y-%m"); od = os.path.join(L, "o", f"m={tag}"); idir = os.path.join(L, "i", f"m={tag}")
    cf = os.path.join(L, "created", f"m={tag}.parquet")
    if os.path.exists(os.path.join(od, "_done")) and os.path.exists(os.path.join(idir, "_done")) and os.path.exists(cf):
        continue
    if left() < 120: log("budget: stop in P before", tag); raise SystemExit(0)
    of, inf = mfiles("outputs", m0), mfiles("inputs", m0)
    if not of: continue
    t = time.time()
    for d_ in (od, idir): shutil.rmtree(d_, ignore_errors=True); os.makedirs(os.path.dirname(d_), exist_ok=True)
    con.execute(f"copy (select tx, n, (ts::date - date '{G}')::int cd, sats, {B('tx')} b from read_parquet({of}) where sats > 0) "
                f"to '{od}' (format parquet, partition_by (b))")
    if inf:
        con.execute(f"copy (select prev_tx, prev_n, (ts::date - date '{G}')::int sd, sats, {B('prev_tx')} b from read_parquet({inf})) "
                    f"to '{idir}' (format parquet, partition_by (b))")
    else:
        os.makedirs(idir, exist_ok=True)
    os.makedirs(os.path.dirname(cf), exist_ok=True)
    con.execute(f"copy (select (ts::date - date '{G}')::int cd, sum(sats)::double / 1e8 btc from read_parquet({of}) where sats > 0 group by 1) to '{cf}' (format parquet)")
    open(os.path.join(od, "_done"), "w").close(); open(os.path.join(idir, "_done"), "w").close()
    log(f"P {tag} {time.time() - t:.0f}s")

# ---- J: base join at cutoff C ----
AG = os.path.join(L, f"agg_{CTAG}"); UT = os.path.join(L, f"utxo_{CTAG}"); os.makedirs(AG, exist_ok=True); os.makedirs(UT, exist_ok=True)
for b in range(NB):
    fa, fu = os.path.join(AG, f"b={b:02d}.parquet"), os.path.join(UT, f"b={b:02d}.parquet")
    if os.path.exists(fa) and os.path.exists(fu): continue
    if left() < min(300, a.budget / 4): log("budget: stop in J at bucket", b); raise SystemExit(0)
    t = time.time()
    og = os.path.join(L, "o", "m=*", f"b={b}", "*.parquet"); ig = os.path.join(L, "i", "m=*", f"b={b}", "*.parquet")
    mlim = f"and regexp_extract(filename, 'm=([0-9-]+)', 1) <= '{CTAG}'"
    con.execute(f"create or replace temp table o as select tx, n, cd, sats from read_parquet('{og}', filename=true) where true {mlim}")
    con.execute(f"create or replace temp table i as select prev_tx, prev_n, sd, sats from read_parquet('{ig}', filename=true) where true {mlim}")
    con.execute(f"copy (select o.cd, i.sd, sum(i.sats)::double / 1e8 btc from i join o on i.prev_tx = o.tx and i.prev_n = o.n group by 1, 2) to '{fa}.tmp' (format parquet)")
    con.execute(f"copy (select o.tx, o.n, o.cd, o.sats from o anti join i on i.prev_tx = o.tx and i.prev_n = o.n) to '{fu}.tmp' (format parquet)")
    os.rename(fa + ".tmp", fa); os.rename(fu + ".tmp", fu)
    log(f"J {CTAG} bucket {b:02d} {time.time() - t:.0f}s")
con.execute("drop table if exists o"); con.execute("drop table if exists i")
json.dump({"cutoff": C, "built_utc": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M")}, open(os.path.join(L, "base.json"), "w"))
# old bases can go once the new one is complete
for old in glob.glob(os.path.join(L, "agg_*")) + glob.glob(os.path.join(L, "utxo_*")):
    if not old.endswith(CTAG): shutil.rmtree(old, ignore_errors=True)

# ---- I: days after C ----
if not a.to:
    d0 = cut_m + dt.timedelta(days=1); fo, fi = [], []
    d = d0
    while d < today:
        for kind, lst in (("outputs", fo), ("inputs", fi)):
            f = os.path.join(R, kind, f"date={d}.parquet")
            if os.path.exists(f): lst.append(f)
        d += dt.timedelta(days=1)
    if fi:
        t = time.time(); q = lambda fs: "[" + ",".join("'%s'" % f for f in fs) + "]"
        con.execute(f"""copy (
          with u as (select tx, n, cd from read_parquet('{UT}/b=*.parquet')
                     union all select tx, n, (ts::date - date '{G}')::int from read_parquet({q(fo)}) where sats > 0),
          i as (select prev_tx, prev_n, (ts::date - date '{G}')::int sd, sats from read_parquet({q(fi)}))
          select u.cd, i.sd, sum(i.sats)::double / 1e8 btc from i join u on i.prev_tx = u.tx and i.prev_n = u.n group by 1, 2)
          to '{os.path.join(L, "inc.parquet")}.tmp' (format parquet)""")
        con.execute(f"copy (select (ts::date - date '{G}')::int cd, sum(sats)::double / 1e8 btc from read_parquet({q(fo)}) where sats > 0 group by 1) to '{os.path.join(L, 'inc_created.parquet')}.tmp' (format parquet)")
        os.replace(os.path.join(L, "inc.parquet.tmp"), os.path.join(L, "inc.parquet"))
        os.replace(os.path.join(L, "inc_created.parquet.tmp"), os.path.join(L, "inc_created.parquet"))
        json.dump({"cutoff": C, "inc_to": (today - dt.timedelta(days=1)).isoformat(), "built_utc": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M")},
                  open(os.path.join(L, "base.json"), "w"))
        log(f"I {d0}..{today - dt.timedelta(days=1)} {time.time() - t:.0f}s")
log(f"done; base {C}; {time.time() - T0:.0f}s")
