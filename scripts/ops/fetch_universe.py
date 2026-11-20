"""Survivorship-free daily universe of Binance USDT spot pairs (incl. delisted), kept up to date.

Question it serves: backtests over "alts as a whole" must use the coins that existed AT THE TIME
(Avalon 2026-11-06: "可能要去找找当时存在的币，而不只是我们有的币"). Data: data-api.binance.vision
1d klines (serves delisted symbols too); symbol list from the data.binance.vision S3 archive.

Outputs (data/universe/):
  raw/{SYMBOL}.csv              per-symbol daily rows: t (UTC day open), l, c, qv (quote volume USDT)
  allusdt.txt                   symbols in scope (no UP/DOWN/BULL/BEAR, stablecoins, fiat, gold/wrapped)
  spot_usdt_1d_all.csv.gz       all raw rows combined, CLOSED UTC days only (columns t,l,c,qv,symbol)
  spot_usdt_1d_spliced.csv.gz   same after renames.csv (renames/merges/redenominations) -> use this for research
  splice_log.txt                what the splice step changed
  exclude.txt                   extra symbols to leave out (tokenized stocks like NVDAB listed from 2026-06); also auto rule:
                                base ending in "B" first listed on/after 2026-06-01 is treated as a tokenized stock (logged)
Runs once per UTC day from run_update.sh (state in data/state/universe.json); --force to rerun, --seconds budget.
Added 2026-11-06. See claude/universe-pit.md.
"""
import argparse, json, re, sys, time, math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pandas as pd, requests

REPO = Path(__file__).resolve().parents[2]
U = REPO / "data" / "universe"; RAW = U / "raw"; STATE = REPO / "data" / "state" / "universe_pit.json"   # NOT universe.json (that one belongs to the board's coin list)
API = "https://data-api.binance.vision/api/v3/klines"
S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
START_MS = 1606780800000  # 2020-12-01
EXCLUDE = {"USDC","BUSD","TUSD","FDUSD","USDP","DAI","PAX","USDS","EUR","GBP","AUD","TRY","BRL","UST","USTC","SUSD","EURI",
           "AEUR","XUSD","USD1","BFUSD","PAXG","XAUT","WBTC","WBETH","BKRW","IDRT","BIDR","RLUSD","USDE","U"}
LEV = re.compile(r"(UP|DOWN|BULL|BEAR)USDT$")
log = lambda *a: print("universe:", *a, flush=True)

def list_symbols():
    syms, marker = [], ""
    while True:
        r = requests.get(S3, params={"prefix": "data/spot/monthly/klines/", "delimiter": "/", "marker": marker}, timeout=30).text
        syms += re.findall(r"<Prefix>data/spot/monthly/klines/([^<]+)/</Prefix>", r)
        m = re.search(r"<NextMarker>([^<]+)</NextMarker>", r)
        if not m: break
        marker = m.group(1)
    return sorted(s for s in syms if s.endswith("USDT") and not LEV.search(s) and s[:-4] not in EXCLUDE)

def klines(sym, start_ms):
    out, s = [], start_ms
    while True:
        r = requests.get(API, params=dict(symbol=sym, interval="1d", startTime=s, limit=1000), timeout=30)
        j = r.json()
        if not isinstance(j, list) or not j: break
        out += j; s = j[-1][0] + 1
        if len(j) < 1000: break
    if not out: return None
    df = pd.DataFrame([[k[0], float(k[3]), float(k[4]), float(k[7])] for k in out], columns=["t","l","c","qv"])
    df["t"] = pd.to_datetime(df.t, unit="ms", utc=True)
    return df

def seed_from_combined():
    """first run on the Mac: split the combined file Claude delivered into raw/ so nothing is re-downloaded"""
    f = U / "spot_usdt_1d_all.csv.gz"
    if RAW.exists() and any(RAW.glob("*.csv")) or not f.exists(): return
    RAW.mkdir(parents=True, exist_ok=True)
    d = pd.read_csv(f)
    for s, g in d.groupby("symbol"):
        g.drop(columns="symbol").to_csv(RAW / f"{s}.csv", index=False)
    log(f"seeded raw/ from {f.name}: {d.symbol.nunique()} symbols")

def update_one(sym, today):
    f = RAW / f"{sym}.csv"
    old = pd.read_csv(f, parse_dates=["t"]) if f.exists() else None
    if old is not None and len(old):
        last = old.t.max()
        if last < today - pd.Timedelta(days=10):   # delisted / halted: nothing new to fetch
            return sym, "stale"
        new = klines(sym, int(last.timestamp() * 1000))   # refetch the last row (it may have been the unclosed day)
        if new is None: return sym, "none"
        d = pd.concat([old[old.t < last], new]).drop_duplicates("t", keep="last").sort_values("t")
    else:
        d = klines(sym, START_MS)
        if d is None: return sym, "empty"
    d.to_csv(f, index=False)
    return sym, "ok"

def splice(all_df):
    """apply data/universe/renames.csv; returns spliced frame + log lines"""
    lines, d = [], all_df.copy()
    rn = pd.read_csv(U / "renames.csv", comment="#") if (U / "renames.csv").exists() else pd.DataFrame()
    for _, r in rn.iterrows():
        old, new, ratio, kind = r.old, r.new, float(r.ratio), r.kind   # ratio = new tokens per 1 old token
        o = d[d.symbol == old]; n = d[d.symbol == new]
        if o.empty or n.empty: lines.append(f"skip {old}->{new}: missing"); continue
        if kind == "rename":      # old history becomes the new symbol's history (prices in new units)
            o = o[o.t < n.t.min()].copy(); o["l"] /= ratio; o["c"] /= ratio; o["symbol"] = new
            d = pd.concat([d[d.symbol != old], o]); lines.append(f"rename {old}->{new} x{ratio}: +{len(o)} rows before {n.t.min().date()}")
        elif kind == "merge":     # old coin keeps its own rows; after it stops, its value follows the new coin (no volume)
            ext = n[n.t > o.t.max()].copy(); ext["l"] *= ratio; ext["c"] *= ratio; ext["qv"] = float("nan"); ext["symbol"] = old
            d = pd.concat([d, ext]); lines.append(f"merge {old}->{new} x{ratio}: extended {len(ext)} rows after {o.t.max().date()}")
        elif kind == "split":     # one ticker holds two coins: rows before `cut` belong to `new` (e.g. old LUNA = LUNC)
            cut = pd.Timestamp(r.cut, tz="UTC"); mv = o[o.t < cut].copy(); mv["l"] /= ratio; mv["c"] /= ratio; mv["symbol"] = new
            d = pd.concat([d[~((d.symbol == old) & (d.t < cut))], mv[mv.t < n.t.min()]]); lines.append(f"split {old}: {len(mv)} rows before {cut.date()} -> {new}")
    # redenominations not listed: single-day UP jump >= 50x -> rescale earlier history (nearest power of 10 if within 2x)
    out = []
    for s, g in d.sort_values("t").groupby("symbol"):
        g = g.copy(); j = g.c / g.c.shift(1)
        for i in g.index[j >= 50]:
            f = float(j[i]); p = 10 ** round(math.log10(f)); f = p if 0.5 <= f / p <= 2 else f
            m = g.t < g.at[i, "t"]; g.loc[m, ["l", "c"]] *= f
            lines.append(f"redenom {s} {g.at[i,'t'].date()}: earlier prices x{f:g}")
        out.append(g)
    return pd.concat(out).sort_values(["symbol", "t"]), lines

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--force", action="store_true"); ap.add_argument("--seconds", type=int, default=600)
    a = ap.parse_args()
    now = datetime.now(timezone.utc); day = now.strftime("%Y-%m-%d")
    st = json.loads(STATE.read_text()) if STATE.exists() else {}
    if not a.force and (st.get("last_day") == day or now.hour == 0 and now.minute < 20):
        return
    U.mkdir(parents=True, exist_ok=True); RAW.mkdir(parents=True, exist_ok=True); seed_from_combined()
    t0 = time.time()
    try:
        syms = list_symbols(); (U / "allusdt.txt").write_text("\n".join(syms))
    except Exception as e:
        syms = (U / "allusdt.txt").read_text().split(); log(f"symbol list refresh failed ({e}); using saved list")
    today = pd.Timestamp(day, tz="UTC"); res = {}
    with ThreadPoolExecutor(6) as ex:
        futs = {ex.submit(update_one, s, today): s for s in syms}
        for fu in futs:
            left = a.seconds - (time.time() - t0)
            try: s, r = fu.result(timeout=max(left, 1)); res[r] = res.get(r, 0) + 1
            except Exception as e: res["err"] = res.get("err", 0) + 1
    frames = []
    for f in RAW.glob("*.csv"):
        g = pd.read_csv(f, parse_dates=["t"]); g["symbol"] = f.stem; frames.append(g)
    allr = pd.concat(frames); allr = allr[allr.t < today]           # closed UTC days only
    exc = set((U / "exclude.txt").read_text().split()) if (U / "exclude.txt").exists() else set()
    first = allr.groupby("symbol").t.min()
    auto = {s for s, t in first.items() if s[:-4].endswith("B") and t >= pd.Timestamp("2026-06-01", tz="UTC")} - exc
    if auto: log(f"auto-excluded as tokenized stocks (check): {sorted(auto)[:15]}{' ...' if len(auto) > 15 else ''}")
    allr = allr[~allr.symbol.isin(exc | auto)]
    allr.to_csv(U / "spot_usdt_1d_all.csv.gz", index=False)
    sp, lines = splice(allr); sp.to_csv(U / "spot_usdt_1d_spliced.csv.gz", index=False)
    (U / "splice_log.txt").write_text("\n".join(lines))
    complete = res.get("err", 0) == 0
    if complete: st["last_day"] = day
    st.update(last_run=now.isoformat(timespec="seconds"), result=res, symbols=allr.symbol.nunique(), last_closed=str(allr.t.max().date()))
    STATE.write_text(json.dumps(st))
    log(f"{res} | {allr.symbol.nunique()} symbols to {allr.t.max().date()} | spliced {sp.symbol.nunique()} | {round(time.time()-t0)}s" + ("" if complete else " | errors, will retry next hour"))

if __name__ == "__main__":
    main()
