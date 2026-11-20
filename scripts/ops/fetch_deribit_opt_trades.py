#!/usr/bin/env python3
"""Daily catch-up of Deribit BTC option trades -> data/onchain/deribit/opt_trades_daily.csv.

Same aggregation as the 2019+ backfill (built 2026-10-17 in the cloud with deribit_opt_hist.py):
one row per (day, cp, taker side, block, moneyness bucket, tenor bucket) with n, amount (BTC),
prem_btc, prem_usd, notional_usd, iv_x_amt. Source: history.deribit.com public API (no key).
Fetches every missing closed UTC day up to yesterday (max 10 per run). ~5-30 requests per day.
Murphy's "options buyer net call/put spending": sum(prem_usd | C, buy) - sum(prem_usd | P, buy).
Usage: python3 scripts/ops/fetch_deribit_opt_trades.py
"""
import csv, datetime as dt, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deribit_opt_hist import do_day, FIELDS  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data/onchain/deribit/opt_trades_daily.csv"


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    last = None
    if OUT.exists():
        with open(OUT) as f:
            for r in csv.reader(f):
                if r and r[0] != "d":
                    last = r[0] if last is None or r[0] > last else last
    else:
        with open(OUT, "w", newline="") as f:
            csv.writer(f).writerow(FIELDS)
    yday = dt.datetime.utcnow().date() - dt.timedelta(days=1)
    day = dt.date.fromisoformat(last) + dt.timedelta(days=1) if last else yday
    n = 0
    while day <= yday and n < 10:
        rows, trades = do_day(day)
        with open(OUT, "a", newline="") as f:
            csv.writer(f).writerows(rows)
        print("deribit_opt_trades: %s %d trades" % (day, trades))
        day += dt.timedelta(days=1); n += 1
    if n == 0:
        print("deribit_opt_trades: up to date (%s)" % last)


if __name__ == "__main__":
    main()
