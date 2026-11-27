#!/usr/bin/env python3
"""One order-book snapshot per run (launchd runs it every minute; see install_book_scheduler.sh).

Stdlib only, no DuckDB (the hourly job owns the DB lock). Appends to data/orderbook/book_YYYY-MM.csv.
Venues: Binance spot (data-api.binance.vision, max 5000 levels/side = only ~±1.5% for BTC; `cov_*` says how far
it reaches, bands beyond coverage are left blank) and Coinbase Exchange level-2 (full book).
Per venue x symbol one row:
  mid, spread_bp, bid_usd_{0.5,1,2,5} / ask_usd_{...} = USD resting within that % of mid,
  wall_bid_px/usd, wall_ask_px/usd = biggest 0.1%-wide price bucket within 2% of mid (the "wall"),
  cov_bid_pct / cov_ask_pct = how far from mid the returned book reaches.
Added 2026-10-30 (Avalon: Murphy's Glassnode "订单簿深度变化" — we had no book data).
"""
import csv, json, time, urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# Run-now hook (10/31): `touch data/state/run_now` -> this minute job starts scripts/ops/run_update.sh once, detached,
# logging to data/logs/update.log like the hourly job (run.lock still prevents overlap).
_flag = REPO / "data" / "state" / "run_now"
if _flag.exists():
    import subprocess
    try:
        _flag.rename(_flag.with_name("run_now.started"))
        subprocess.Popen(["/bin/bash", str(REPO / "scripts/ops/run_update.sh")], cwd=REPO, start_new_session=True,
                         stdout=open(REPO / "data/logs/update.log", "a"), stderr=subprocess.STDOUT)
    except Exception as e:
        print("run_now failed", repr(e))
OUT = REPO / "data" / "orderbook"
BANDS = [0.5, 1, 2, 5]
SYMS = ["BTC", "ETH", "ZEC"]
SRC = {
    "binance": "https://data-api.binance.vision/api/v3/depth?symbol={s}USDT&limit=5000",
    "coinbase": "https://api.exchange.coinbase.com/products/{s}-USD/book?level=2",
}
COLS = (["ts", "venue", "symbol", "mid", "spread_bp"] + [f"bid_usd_{b}" for b in BANDS] + [f"ask_usd_{b}" for b in BANDS]
        + ["wall_bid_px", "wall_bid_usd", "wall_ask_px", "wall_ask_usd", "cov_bid_pct", "cov_ask_pct", "levels"])


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "datahub-book/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def row(venue, sym, book, ts):
    bids = [(float(p), float(q)) for p, q, *_ in book["bids"]]
    asks = [(float(p), float(q)) for p, q, *_ in book["asks"]]
    if not bids or not asks:
        return None
    bb, ba = bids[0][0], asks[0][0]
    mid = (bb + ba) / 2
    cov_b = (mid - bids[-1][0]) / mid * 100
    cov_a = (asks[-1][0] - mid) / mid * 100
    r = {"ts": ts, "venue": venue, "symbol": sym, "mid": round(mid, 4), "spread_bp": round((ba - bb) / mid * 1e4, 3),
         "cov_bid_pct": round(cov_b, 3), "cov_ask_pct": round(cov_a, 3), "levels": len(bids) + len(asks)}
    for b in BANDS:
        lo, hi = mid * (1 - b / 100), mid * (1 + b / 100)
        r[f"bid_usd_{b}"] = round(sum(p * q for p, q in bids if p >= lo)) if cov_b >= b else ""
        r[f"ask_usd_{b}"] = round(sum(p * q for p, q in asks if p <= hi)) if cov_a >= b else ""
    step = mid * 0.001
    for side, lv, sgn in (("bid", bids, -1), ("ask", asks, 1)):
        buckets = {}
        for p, q in lv:
            if abs(p - mid) / mid > 0.02:
                continue
            k = int((p - mid) / step) if sgn > 0 else -int((mid - p) / step)
            buckets[k] = buckets.get(k, 0.0) + p * q
        if buckets:
            k, usd = max(buckets.items(), key=lambda kv: kv[1])
            r[f"wall_{side}_px"] = round(mid + (k + 0.5 * sgn) * step, 2)
            r[f"wall_{side}_usd"] = round(usd)
        else:
            r[f"wall_{side}_px"] = r[f"wall_{side}_usd"] = ""
    return r


def main():
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    ts = now.strftime("%Y-%m-%d %H:%M:%S")
    OUT.mkdir(parents=True, exist_ok=True)
    f = OUT / f"book_{now:%Y-%m}.csv"
    new = not f.exists()
    rows, errs = [], []
    for venue, url in SRC.items():
        for s in SYMS:
            try:
                r = row(venue, s, get(url.format(s=s)), ts)
                if r:
                    rows.append(r)
            except Exception as e:  # one venue failing must not lose the other
                errs.append(f"{venue} {s}: {e}")
    with f.open("a", newline="") as fh:
        w = csv.DictWriter(fh, COLS)
        if new:
            w.writeheader()
        w.writerows(rows)
    if errs:
        print(f"{ts} book_snapshot errors: " + " | ".join(errs), flush=True)


if __name__ == "__main__":
    main()
