#!/usr/bin/env python3
"""Download Deribit BTC option trades (history.deribit.com, public) and aggregate per UTC day.

Output (long format, one row per bucket per day) -> <out>/opt_trades_daily_<tag>.csv:
  d, cp (C/P), side (taker buy/sell), block (0/1), mny (strike/index bucket), tenor (days bucket),
  n, amount (BTC contracts), prem_btc, prem_usd, notional_usd, iv_x_amt (sum iv*amount)
Resumable: finished days listed in <out>/done_<tag>.txt. Retries with backoff on 429/5xx.
Usage: deribit_opt_hist.py START END TAG OUTDIR   (dates YYYY-MM-DD, END exclusive)
"""
import csv, datetime as dt, json, sys, time, urllib.request, urllib.error
from collections import defaultdict
from pathlib import Path

URL = ("https://history.deribit.com/api/v2/public/get_last_trades_by_currency_and_time"
       "?currency=BTC&kind=option&start_timestamp={s}&end_timestamp={e}&count=1000&sorting=asc")
MON = {m: i for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), 1)}
FIELDS = ["d", "cp", "side", "block", "mny", "tenor", "n", "amount", "prem_btc", "prem_usd", "notional_usd", "iv_x_amt"]
REQS = [0]


def fetch(s, e):
    wait = 2
    for _ in range(12):
        try:
            REQS[0] += 1
            with urllib.request.urlopen(URL.format(s=s, e=e), timeout=60) as r:
                return json.load(r)["result"]
        except urllib.error.HTTPError as ex:
            print("HTTP", ex.code, "sleep", wait, flush=True)
        except Exception as ex:
            print("ERR", type(ex).__name__, ex, "sleep", wait, flush=True)
        time.sleep(wait)
        wait = min(wait * 2, 120)
    raise RuntimeError("giving up at %s" % s)


def expiry(inst):
    d = inst.split("-")[1]  # e.g. 13JUN22 / 5OCT26
    return dt.datetime(2000 + int(d[-2:]), MON[d[-5:-2]], int(d[:-5]), 8, tzinfo=dt.timezone.utc)


def mny_bucket(x):
    for lim, lab in ((0.85, "<0.85"), (0.95, "0.85-0.95"), (1.05, "0.95-1.05"), (1.15, "1.05-1.15")):
        if x < lim:
            return lab
    return ">=1.15"


def tenor_bucket(days):
    return "<=7" if days <= 7 else ("8-30" if days <= 30 else ">30")


def do_day(day):
    s = int(dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc).timestamp() * 1000)
    e = s + 86_400_000 - 1
    seen, agg = set(), defaultdict(lambda: [0, 0.0, 0.0, 0.0, 0.0, 0.0])
    start = s
    while True:
        res = fetch(start, e)
        tr = res["trades"]
        new = [t for t in tr if t["trade_id"] not in seen]
        for t in new:
            seen.add(t["trade_id"])
            inst = t["instrument_name"]
            parts = inst.split("-")
            cp, strike = parts[3], float(parts[2])
            idx = t["index_price"]
            ts = dt.datetime.fromtimestamp(t["timestamp"] / 1000, dt.timezone.utc)
            days = (expiry(inst) - ts).total_seconds() / 86400
            k = (cp, t["direction"], 1 if t.get("block_trade_id") else 0,
                 mny_bucket(strike / idx), tenor_bucket(days))
            a = agg[k]
            amt, px = t["amount"], t["price"]
            a[0] += 1; a[1] += amt; a[2] += amt * px; a[3] += amt * px * idx
            a[4] += amt * idx; a[5] += (t.get("iv") or 0) * amt
        if not res.get("has_more") or not tr:
            break
        last = tr[-1]["timestamp"]
        start = last if new else last + 1  # stuck page of same-ms trades -> step 1 ms
    return [[day.isoformat(), *k, a[0], round(a[1], 4), round(a[2], 6), round(a[3], 2), round(a[4], 2), round(a[5], 3)]
            for k, a in sorted(agg.items())], len(seen)


def main():
    d0, d1, tag, out = sys.argv[1:5]
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    f_csv, f_done = out / ("opt_trades_daily_%s.csv" % tag), out / ("done_%s.txt" % tag)
    done = set(f_done.read_text().split()) if f_done.exists() else set()
    if not f_csv.exists():
        with open(f_csv, "w", newline="") as f:
            csv.writer(f).writerow(FIELDS)
    day, end = dt.date.fromisoformat(d0), dt.date.fromisoformat(d1)
    t0 = time.time()
    while day < end:
        if day.isoformat() not in done:
            rows, n = do_day(day)
            with open(f_csv, "a", newline="") as f:
                csv.writer(f).writerows(rows)
            with open(f_done, "a") as f:
                f.write(day.isoformat() + "\n")
            print(day, n, "trades", REQS[0], "reqs", round(time.time() - t0), "s", flush=True)
        day += dt.timedelta(days=1)
    print("DONE", tag, flush=True)


if __name__ == "__main__":
    main()
