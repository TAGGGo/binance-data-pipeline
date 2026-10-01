#!/usr/bin/env python3
"""BTC on-chain metrics from BGeometrics (bitcoin-data.com) -> data/onchain/bgeometrics/<metric>.csv

Free plan (no key): 10 requests/hour, 15/day, last 4 years, newest 7 days withheld.
One request returns a metric's whole series, so each metric costs 1 request per day.
Rows are merged into the CSV (never dropped), so our copy keeps growing past the free
4-year window. Runs from the hourly run_update.sh: each run fetches metrics not yet
refreshed today (UTC), newest-priority first, until the hourly/daily quota runs out.
Optional BGEOMETRICS_TOKEN in .env (paid plan) is sent as a Bearer token.

  python3 scripts/ops/fetch_bgeometrics.py            # refresh stale metrics
  python3 scripts/ops/fetch_bgeometrics.py --only nrpl-usd sopr --force
"""
import argparse, csv, datetime as dt, json, os, sys, urllib.request, urllib.error
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "data" / "onchain" / "bgeometrics"
STATE = REPO / "data" / "state" / "bgeometrics_state.json"
BASE = "https://bitcoin-data.com/v1/"

# core = kept on the free plan (15/day incl. URPD): cost bases + the cycle gauges (MVRV Z, NUPL, Puell).
# accumulation-trend-score (paid extra) is BGeometrics' own
# LTH/STH 30d-change score (not Glassnode's entity-based ATS); free though not on the plan pages.
# URPD (realized price distribution, 2K bins) is one snapshot per day -> urpd/<date>.csv, see fetch_urpd().
METRICS = [
    "nrpl-usd", "realized-profit", "realized-loss", "sth-realized-price", "true-market-mean", "realized-price",
    "mvrv", "mvrv-zscore", "nupl", "sth-mvrv", "sopr", "sth-sopr",
    "lth-realized-price", "puell-multiple",
]
# extra metrics fetched only with BGEOMETRICS_TOKEN (paid Advanced plan, 200/h 300/day, full history)
PAID_EXTRA = [
    "accumulation-trend-score", "realized_profit_sth", "realized_loss_sth", "realized_profit_lth", "realized_loss_lth", "lth-mvrv",
    "nupl-sth", "nupl-lth", "lth-sopr", "asopr", "profit-loss", "supply-profit",
    "cdd", "vdd-multiple", "hodl-waves-supply", "realized-cap-hodl-waves", "investor-price", "exchange-netflow-btc",
    "exchange-reserve-btc", "miner-net-flow", "miner-reserve", "m2global", "btc-price",
]


def load_state():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def fetch(ep, token=None):
    req = urllib.request.Request(BASE + ep, headers={"User-Agent": "mdh/1.0", "Accept": "application/json"})
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.status, dict(r.headers), json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        return e.code, dict(e.headers), {"error": body}


def merge(ep, rows):
    path = OUT / f"{ep}.csv"
    old = {}
    if path.exists():
        with path.open() as f:
            for r in csv.DictReader(f):
                old[r["d"]] = r
    cols = ["d"]
    for r in rows:
        for k in r:
            if k not in cols and k != "unixTs":
                cols.append(k)
    for r in old.values():
        for k in r:
            if k not in cols:
                cols.append(k)
    for r in rows:
        old[r["d"]] = {k: r.get(k) for k in cols if k in r}
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for d in sorted(old):
            w.writerow(old[d])
    tmp.replace(path)
    ds = sorted(old)
    return len(ds), ds[0], ds[-1]


def fetch_urpd(state, today, token):
    """Latest URPD snapshot (usually yesterday UTC) -> urpd/<date>.csv. 1 request."""
    day = (dt.date.fromisoformat(today) - dt.timedelta(days=1)).isoformat()
    status, hdr, data = fetch(f"urpd?day={day}", token)
    if status != 200 or not isinstance(data, list) or not data:
        print(f"bgeometrics: urpd {day} failed ({status}): {str(data)[:200]}")
        state["urpd"] = {**state.get("urpd", {}), "error": str(data)[:200], "tried": today}
        return
    d = OUT / "urpd"; d.mkdir(parents=True, exist_ok=True)
    cols = ["theDate", "priceLower", "priceUpper", "utxoCount", "btcSupply", "pctSupply"]
    with (d / f"{day}.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); w.writeheader(); w.writerows(data)
    state["urpd"] = {"fetched": today, "last": day, "bins": len(data)}
    print(f"bgeometrics: urpd {day} {len(data)} bins (left this hour {hdr.get('X-RateLimit-Remaining-Hour')})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--max", type=int, default=60)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    state = load_state()
    token = os.environ.get("BGEOMETRICS_TOKEN") or None
    todo = a.only or (METRICS + PAID_EXTRA if token else METRICS)
    if not a.force:
        todo = [m for m in todo if state.get(m, {}).get("fetched") != today]
    done = 0
    for ep in todo[: a.max]:
        if token is None and ep not in METRICS and not a.only:
            continue
        status, hdr, data = fetch(ep, token)
        if token and status in (401, 402, 403):
            # subscription ended / token rejected: continue on the free plan (core list only)
            print(f"bgeometrics: token rejected ({status}); using the free plan. Remove BGEOMETRICS_TOKEN from .env")
            token = None
            if ep not in METRICS:
                continue
            status, hdr, data = fetch(ep, None)
        if status == 429:
            print(f"bgeometrics: rate limit hit before {ep}; rest next run")
            break
        if status != 200 or not isinstance(data, list) or not data:
            print(f"bgeometrics: {ep} failed ({status}): {str(data)[:200]}")
            state[ep] = {**state.get(ep, {}), "error": str(data)[:200], "tried": today}
            continue
        n, first, last = merge(ep, data)
        state[ep] = {"fetched": today, "rows": n, "first": first, "last": last}
        done += 1
        left_h = hdr.get("X-RateLimit-Remaining-Hour")
        print(f"bgeometrics: {ep} {n} rows {first}..{last} (left this hour {left_h}, today {hdr.get('X-RateLimit-Remaining-Day')})")
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(state, indent=1, sort_keys=True))
        if left_h is not None and int(left_h) <= 0:
            print("bgeometrics: hourly quota used; rest next run")
            break
    if (a.only is None or "urpd" in a.only) and (a.force or state.get("urpd", {}).get("fetched") != today):
        fetch_urpd(state, today, token)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=1, sort_keys=True))
    if not todo:
        print("bgeometrics: all metrics fresh today")


if __name__ == "__main__":
    sys.exit(main())
