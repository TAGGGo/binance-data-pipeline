#!/usr/bin/env python3
"""Binance USDT-M perp order-book depth history (data.binance.vision futures/um/daily/bookDepth), added 2026-10-30.

Raw files: one snapshot ~every 30 s with cumulative depth at +-0.2/1/2/3/4/5 % of mid (base qty + USD notional;
negative % = bids). Spot has NO such archive. Files appear ~1-2 days late; first day available 2023-01-01.
We keep 5-minute rows (mean of the snapshots in the bucket) per day:
  data/bookdepth/{SYM}/{YYYY-MM-DD}.csv.gz  columns ts, pct, depth_mean, notional_mean, notional_min, n
Query with DuckDB: read_csv('data/bookdepth/BTCUSDT/*.csv.gz').  Stdlib only, never touches market.duckdb.
  python3 scripts/ops/fetch_bn_bookdepth.py [--max-days N] [--seconds S]     (run_update.sh calls it hourly)
"""
import argparse, csv, gzip, io, time, urllib.request, urllib.error, zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "data" / "bookdepth"
SYMS = ["BTCUSDT", "ETHUSDT", "ZECUSDT"]
FIRST = date(2023, 1, 1)
URL = "https://data.binance.vision/data/futures/um/daily/bookDepth/{s}/{s}-bookDepth-{d}.zip"


def fetch(sym, d):
    try:
        with urllib.request.urlopen(urllib.request.Request(URL.format(s=sym, d=d), headers={"User-Agent": "datahub"}), timeout=60) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    z = zipfile.ZipFile(io.BytesIO(raw))
    text = z.read(z.namelist()[0]).decode()
    agg = {}
    for row in csv.reader(io.StringIO(text)):
        if not row or not row[0][:1].isdigit():
            continue                      # header (some files have none)
        ts = datetime.strptime(row[0][:19], "%Y-%m-%d %H:%M:%S")
        ts = ts.replace(minute=ts.minute - ts.minute % 5, second=0)
        k = (ts, float(row[1]))
        dep, no = float(row[2]), float(row[3])
        a = agg.setdefault(k, [0.0, 0.0, float("inf"), 0])
        a[0] += dep; a[1] += no; a[2] = min(a[2], no); a[3] += 1
    return agg


def write(sym, d, agg):
    p = OUT / sym; p.mkdir(parents=True, exist_ok=True)
    tmp = p / f".{d}.tmp"
    with gzip.open(tmp, "wt", newline="") as fh:
        w = csv.writer(fh); w.writerow(["ts", "pct", "depth_mean", "notional_mean", "notional_min", "n"])
        for (ts, pct), (dep, no, mn, n) in sorted(agg.items()):
            w.writerow([ts.strftime("%Y-%m-%d %H:%M:%S"), pct, round(dep / n, 3), round(no / n), round(mn), n])
    tmp.rename(p / f"{d}.csv.gz")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-days", type=int, default=60, help="per symbol per run (initial backfill goes in chunks)")
    ap.add_argument("--seconds", type=float, default=600)
    a = ap.parse_args()
    t_end = time.time() + a.seconds
    last = datetime.now(timezone.utc).date() - timedelta(days=1)
    for sym in SYMS:
        have = {p.name[:10] for p in (OUT / sym).glob("*.csv.gz")} if (OUT / sym).exists() else set()
        miss_f = OUT / sym / ".missing"      # days that 404'd and are older than 7 days = never coming
        never = set(miss_f.read_text().split()) if miss_f.exists() else set()
        todo = [d for d in ((last - timedelta(days=i)).isoformat() for i in range((last - FIRST).days + 1))
                if d not in have and d not in never]   # newest first
        done = 0
        for d in todo[: a.max_days]:
            if time.time() > t_end:
                break
            agg = fetch(sym, d)
            if agg is None:
                if (last - date.fromisoformat(d)).days > 7:
                    (OUT / sym).mkdir(parents=True, exist_ok=True)
                    with miss_f.open("a") as fh: fh.write(d + "\n")
                continue
            write(sym, d, agg); done += 1
        left = len(todo) - done
        if done or left:
            print(f"bookdepth {sym}: +{done} days, {left} still missing", flush=True)


if __name__ == "__main__":
    main()
