#!/usr/bin/env python3
"""Live spot prices, every minute (added 2026-11-08, Avalon: "只更新价格可以" — the board's price cards were
hourly and Claude quoted a 45-minute-old price right after a crash).

Run by scripts/ops/jobq.sh (launchd com.market-data-hub.jobq, every 60 s). Stdlib only, no DuckDB.
One request to Binance spot 24h tickers (data-api.binance.vision) -> data/live/prices.json, written atomically:
  {"asof_utc": "2026-11-08T22:40:05Z", "src": "binance spot 24hr",
   "p": {"BTC": {"last": 74500.1, "chg24": -1.6, "open24": 75700.0, "high24": ..., "low24": ..., "t": "...Z"}, ...}}
chg24 = Binance rolling 24 h change (last vs price 24 h ago). Readers must check asof_utc (stale if the Mac slept).
"""
import json, os, sys, time, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "data" / "live" / "prices.json"
SYMS = ("AAVE,ADA,AERO,ALGO,APT,ARB,ASTER,ATOM,AVAX,BCH,BNB,BTC,CAKE,DASH,DOGE,DOT,ENA,ETC,ETH,ETHFI,FET,FIL,GRAM,HBAR,"
        "HYPE,ICP,INJ,JST,JUP,LDO,LINK,LTC,MORPHO,NEAR,NEXO,NIGHT,ONDO,PEPE,POL,PUMP,PYTH,QNT,RENDER,SHIB,SKY,SOL,STRK,"
        "STX,SUI,TAO,TIA,TRX,UNI,WLD,WLFI,XLM,XRP,ZEC,ZEN,ZRO").split(",")
BASE = "https://data-api.binance.vision/api/v3/ticker/24hr"


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "datahub-live/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def fetch():
    want = {s + "USDT": s for s in SYMS}
    try:
        rows = get(BASE + "?symbols=" + urllib.parse.quote(json.dumps(list(want), separators=(",", ":"))))
    except Exception:  # one delisted symbol makes the batch call fail: fall back to all tickers, filtered
        rows = [r for r in get(BASE + "?type=FULL") if r.get("symbol") in want]
    out = {}
    for r in rows:
        s = want.get(r.get("symbol"))
        if not s:
            continue
        try:
            last = float(r["lastPrice"])
            if last <= 0:
                continue
            out[s] = {"last": last, "chg24": round(float(r["priceChangePercent"]), 2), "open24": float(r["openPrice"]),
                      "high24": float(r["highPrice"]), "low24": float(r["lowPrice"]),
                      "t": datetime.fromtimestamp(int(r["closeTime"]) / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        except Exception:
            continue
    return out


def main():
    p = fetch()
    if not p.get("BTC"):
        print(datetime.now(timezone.utc).isoformat(), "live_prices: no BTC in reply, not written", flush=True)
        return
    doc = {"asof_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "src": "binance spot 24hr", "p": p}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, separators=(",", ":")))
    os.replace(tmp, OUT)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(datetime.now(timezone.utc).isoformat(), "live_prices failed:", repr(e)[:300], flush=True)
        sys.exit(0)
