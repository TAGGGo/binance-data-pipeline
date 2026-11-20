#!/usr/bin/env python3
"""Backfill Coinbase BTC-USD taker flow (cb_taker_15m columns) backwards in time, resumably.

Stdlib only, no DuckDB: writes one CSV per UTC day to data/backfill/cb_taker/{SYM}_{YYYY-MM-DD}.csv;
merge_cb_backfill.py (called from run_update.sh, which holds the DB) loads finished days into cb_taker_15m.
Pages /products/{SYM}-USD/trades (1000/call, newest first, cb-after cursor) starting just before the oldest
bucket already in the DB, down to --until (default 2023-01-01). Coinbase `side` = MAKER side -> side=='sell' is a
taker BUY (same rule as mdh/sources/flows.py). State: data/state/cb_backfill_{SYM}.json (cursor + current day).
launchd runs it hourly for --minutes (default 55); it exits at once when done. ~30k trades/hour in 2026 = ~720 calls/day.
"""
import argparse, csv, json, time, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "data" / "backfill" / "cb_taker"
URL = "https://api.exchange.coinbase.com/products/{p}/trades?limit={n}"
COLS = ["symbol", "ts", "buy_usd", "sell_usd", "buy_qty", "sell_qty", "trades", "delta_usd", "vwap"]


def page(sym, after=None, n=1000):
    url = URL.format(p=f"{sym}-USD", n=n) + (f"&after={after}" if after else "")
    for i in range(8):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "datahub-cb-backfill/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r), r.headers.get("cb-after")
        except urllib.error.HTTPError as e:
            time.sleep(2 ** i if e.code == 429 else 3)
        except Exception:
            time.sleep(3)
    raise RuntimeError(f"coinbase trades failed after retries: {url}")


def ptime(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)


def find_cursor(sym, target):
    """Smallest trade_id window: return an `after` cursor whose trades are just older than target (binary search)."""
    newest, _ = page(sym, n=1)
    lo, hi = 1, int(newest[0]["trade_id"])
    while hi - lo > 1000:
        mid = (lo + hi) // 2
        t, _ = page(sym, after=mid, n=1)
        if not t or ptime(t[0]["time"]) < target:
            lo = mid
        else:
            hi = mid
        time.sleep(0.15)
    return hi


def oldest_in_db(sym):
    """Oldest stored bucket (read-only; DuckDB lock may be held by the hourly job -> retry)."""
    import duckdb
    for _ in range(30):
        try:
            c = duckdb.connect(str(REPO / "data" / "market.duckdb"), read_only=True)
            v = c.execute("select min(ts) from cb_taker_15m where symbol=?", [sym]).fetchone()[0]
            c.close()
            return v
        except Exception:
            time.sleep(10)
    raise RuntimeError("cannot open market.duckdb read-only")


def flush(sym, day, buckets):
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for ts in sorted(buckets):
        b = buckets[ts]
        rows.append({"symbol": sym, "ts": ts.strftime("%Y-%m-%d %H:%M:%S"), "buy_usd": round(b[0], 2), "sell_usd": round(b[1], 2),
                     "buy_qty": round(b[2], 8), "sell_qty": round(b[3], 8), "trades": b[4], "delta_usd": round(b[0] - b[1], 2),
                     "vwap": round(b[5] / b[6], 4) if b[6] else ""})
    tmp = OUT / f".{sym}_{day}.csv.tmp"
    with tmp.open("w", newline="") as fh:
        w = csv.DictWriter(fh, COLS); w.writeheader(); w.writerows(rows)
    tmp.rename(OUT / f"{sym}_{day}.csv")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTC")
    ap.add_argument("--until", default="2023-01-01")
    ap.add_argument("--minutes", type=float, default=55)
    ap.add_argument("--rps", type=float, default=6)
    a = ap.parse_args()
    sym, until = a.symbol, datetime.fromisoformat(a.until)
    st_f = REPO / "data" / "state" / f"cb_backfill_{sym}.json"
    st = json.loads(st_f.read_text()) if st_f.exists() else {}
    if st.get("done"):
        return
    if "after" not in st:
        stop = oldest_in_db(sym)  # backfill strictly before what the live job already has
        stop = stop or datetime.utcnow().replace(minute=0, second=0, microsecond=0)
        st = {"after": find_cursor(sym, stop), "stop": stop.isoformat(), "day": None}
        print(f"{sym}: start cursor {st['after']} (before {stop})", flush=True)
    stop = datetime.fromisoformat(st["stop"])
    deadline = time.time() + a.minutes * 60
    buckets, cur_day = {}, None
    after, calls, oldest = st["after"], 0, None
    while time.time() < deadline:
        t0 = time.time()
        trades, nxt = page(sym, after=after)
        calls += 1
        if not trades:
            st["done"] = True
            break
        for t in trades:
            tm = ptime(t["time"])
            if tm >= stop:
                continue
            day = tm.date().isoformat()
            if cur_day is None:
                cur_day = day
            if day != cur_day:              # crossed a day boundary -> the newer day is complete
                flush(sym, cur_day, buckets)
                # checkpoint: resume by re-reading THIS page, counting only trades older than the flushed day
                st["after"], st["stop"] = after, cur_day + "T00:00:00"
                stop = datetime.fromisoformat(st["stop"])
                st_f.parent.mkdir(parents=True, exist_ok=True)
                st_f.write_text(json.dumps(st))
                buckets, cur_day = {}, day
            ts = tm.replace(minute=tm.minute - tm.minute % 15, second=0, microsecond=0)
            q, p = float(t["size"]), float(t["price"])
            b = buckets.setdefault(ts, [0.0, 0.0, 0.0, 0.0, 0, 0.0, 0.0])
            if t["side"] == "sell":          # maker sold -> taker bought
                b[0] += q * p; b[2] += q
            else:
                b[1] += q * p; b[3] += q
            b[4] += 1; b[5] += q * p; b[6] += q
        oldest = ptime(trades[-1]["time"])
        if oldest < until:
            st["done"] = True              # days older than --until are never flushed
            break
        if not nxt:
            st["done"] = True
            break
        after = nxt
        time.sleep(max(0, 1 / a.rps - (time.time() - t0)))
    # the partial (oldest, unfinished) day is dropped; next run resumes from the last checkpoint
    st_f.parent.mkdir(parents=True, exist_ok=True)
    st_f.write_text(json.dumps(st))
    done_days = len(list(OUT.glob(f"{sym}_*.csv")))
    print(f"{datetime.utcnow():%Y-%m-%d %H:%M} cb_backfill {sym}: {calls} calls, reached {oldest}, day files {done_days}, done={st.get('done', False)}", flush=True)


if __name__ == "__main__":
    main()
