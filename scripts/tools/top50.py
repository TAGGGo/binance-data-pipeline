#!/usr/bin/env python3
"""Compare settings.TOP50 (fixed list, see mdh/settings.py) with today's CoinGecko top 50.

Same filter as when the list was made: CoinGecko market-cap order, stablecoins / wrapped / tokenized assets out,
only coins with a Binance USDT spot pair. Prints who entered / left; edit settings.TOP50 (and _TOP50_EXTRA for
the VPN step) by hand if you want to follow the change. New coins then backfill on the next hourly run
(binance_hist; a full backfill per coin takes ~1-3 minutes from the archive).
    python3 scripts/tools/top50.py
"""
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from mdh import settings  # noqa: E402

SKIP = {"USDT", "USDC", "USDS", "USDE", "DAI", "USD1", "PYUSD", "USDG", "RLUSD", "USYC", "USDY", "BUIDL", "USDF",
        "EURSAFO", "USDD", "U", "BFUSD", "USDGO", "OUSD", "USTB", "GHO", "USD0", "TUSD", "EURC", "XAUT", "PAXG", "KAU",
        "EUTBL", "JAAA", "BCAP", "YLDS", "FIGR_HELOC", "WBT", "LEO", "STABLE", "M", "FDUSD", "WBTC", "STETH", "WSTETH",
        "WETH", "WEETH", "CBBTC", "SUSDE", "SUSDS", "BSC-USD"}


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return json.load(urllib.request.urlopen(req, timeout=30))


cg = [x["symbol"].upper() for x in get("https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd"
                                       "&order=market_cap_desc&per_page=150&page=1")]
info = get("https://data-api.binance.vision/api/v3/exchangeInfo?permissions=SPOT")
ok = {s["baseAsset"] for s in info["symbols"] if s["quoteAsset"] == "USDT" and s["status"] == "TRADING"}
now = [s for s in cg if s not in SKIP and s in ok and s != "BTC"][:50]
old = settings.TOP50
print(f"settings.TOP50 is from {settings.TOP50_AS_OF}")
print("entered:", [c for c in now if c not in old] or "none")
print("left:   ", [c for c in old if c not in now] or "none")
print("today:  ", " ".join(now))
