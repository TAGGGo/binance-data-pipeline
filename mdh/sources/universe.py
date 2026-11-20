"""
Auto top-50 discovery (added 2026-10-08, Avalon: "如果有新的币升到 50 名，也加进去 1 小时任务").

Every hourly run, before the market sources: one CoinGecko markets call (top 150 by market cap) -> drop
stablecoins / wrapped / staked / tokenized / pegged assets (name keywords + a peg check: price ~$1 with a tiny
24h range) -> keep coins with a Binance USDT spot pair -> the first 50 = today's top 50 (ranks saved).
A coin in that top 50 that we don't track yet is probed once:
  Binance perp     data.binance.vision archive listing for XUSDT / 1000XUSDT / 1000000XUSDT / 1MXUSDT
  Coinbase         GET /products/X-USD
  Hyperliquid      meta universe (X or kX)
and recorded in data/state/universe.json -> settings.apply_universe() appends it to BINANCE_UNIVERSE,
BINANCE_SYMBOLS (VPN step, plain perps only), BINANCE_FUNDING_ASSETS, COINBASE_EXTRA, TOP50_HL. The other sources
then backfill it themselves (binance_hist from the archive ~1-3 min/coin, Coinbase ~10 s, Hyperliquid ~1 min),
so at most settings.UNIVERSE_MAX_ADDS_PER_DAY coins are added per UTC day (the rest wait for the next day).
Coins are never removed: once tracked, they keep updating even if they fall out of the top 50.
Coins added without a perp (perp not in the archive yet) are re-probed once a day.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from mdh import settings
from mdh.core.http import HttpError

NAME = "universe"
CG_URL = "https://api.coingecko.com/api/v3/coins/markets"
SPOT_INFO = "https://data-api.binance.vision/api/v3/exchangeInfo"
LISTING = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
CB_PRODUCT = "https://api.exchange.coinbase.com/products/{}-USD"
HL_URL = "https://api.hyperliquid.xyz/info"

SKIP = {"USDT", "USDC", "USDS", "USDE", "DAI", "USD1", "PYUSD", "USDG", "RLUSD", "USYC", "USDY", "BUIDL", "USDF",
        "EURSAFO", "USDD", "U", "BFUSD", "USDGO", "OUSD", "USTB", "GHO", "USD0", "TUSD", "EURC", "XAUT", "PAXG", "KAU",
        "EUTBL", "JAAA", "BCAP", "YLDS", "FIGR_HELOC", "WBT", "LEO", "STABLE", "M", "FDUSD", "WBTC", "STETH", "WSTETH",
        "WETH", "WEETH", "CBBTC", "SUSDE", "SUSDS", "BSC-USD", "RETH", "METH", "EZETH", "LBTC", "SOLVBTC", "BTCB",
        "BNSOL", "JITOSOL", "MSOL", "WBETH", "FRXETH", "SFRXETH", "CLBTC", "TBTC", "USDX", "AEUR", "EURI", "XUSD"}
WORDS = ("usd", "dollar", "euro", "stablecoin", "wrapped", "staked", "restaked", "bridged", "tokenized", "treasury",
         "t-bill", "money market", "gold", "silver", "liquid staking", "yield-bearing", "savings")


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _load() -> dict:
    try:
        return json.loads(settings.UNIVERSE_FILE.read_text())
    except (FileNotFoundError, ValueError):
        return {"added": {}}


def _pegged_or_wrapped(x: dict) -> bool:
    sym, name = x["symbol"].upper(), (x.get("name") or "").lower()
    if sym in SKIP or any(w in name for w in WORDS):
        return True
    p, hi, lo = x.get("current_price") or 0, x.get("high_24h") or 0, x.get("low_24h") or 0
    return bool(0.9 < p < 1.1 and hi and lo and (hi - lo) / p < 0.006)   # $1 peg: real coins move more than 0.6%/day


def _perp(ctx, sym: str):
    ns = "{http://s3.amazonaws.com/doc/2006-03-01/}"
    for psym, mult in ((f"{sym}USDT", 1), (f"1000{sym}USDT", 1000), (f"1000000{sym}USDT", 1_000_000), (f"1M{sym}USDT", 1_000_000)):
        r = ctx.http.get(LISTING, params={"prefix": f"data/futures/um/daily/klines/{psym}/1h/", "max-keys": 1})
        if f"{ns}Key" in r.text or "<Key>" in r.text:
            return [psym, mult]
    return None


def _coinbase(ctx, sym: str) -> bool:
    try:
        d = ctx.http.get_json(CB_PRODUCT.format(sym))
        return bool(d) and not d.get("trading_disabled") and d.get("status", "online") == "online"
    except HttpError:
        return False


def run(ctx, full=False):
    u = _load()
    added = u.setdefault("added", {})
    h = {"x-cg-demo-api-key": os.environ["COINGECKO_DEMO_KEY"]} if os.environ.get("COINGECKO_DEMO_KEY") else {}
    mk = ctx.http.get_json(CG_URL, headers=h, params={"vs_currency": "usd", "order": "market_cap_desc", "per_page": 150, "page": 1})
    cands = [x for x in mk if not _pegged_or_wrapped(x)]
    now = _now()
    today = now.strftime("%Y-%m-%d")
    cache = u.get("binance_spot_usdt") or {}
    if cache.get("date") != today:   # the full spot exchangeInfo is a few MB: refresh once a UTC day
        info = ctx.http.get_json(SPOT_INFO, params={"permissions": "SPOT"})
        cache = {"date": today, "bases": sorted({s["baseAsset"] for s in info["symbols"]
                                                 if s["quoteAsset"] == "USDT" and s["status"] == "TRADING"})}
        u["binance_spot_usdt"] = cache
    spot = set(cache["bases"])
    top_items = [x for i, x in enumerate(cands)
                 if x["symbol"].upper().isascii() and x["symbol"].upper() in spot and x["symbol"].upper() != "BTC"
                 and ((x.get("market_cap_rank") or 999) <= 100 or i < 65)]
    top = [x["symbol"].upper() for x in top_items]
    logo_dir = settings.REPO_ROOT.parent.parent / "crypto-chat" / "public" / "static" / "logos"
    if logo_dir.is_dir():
        for x in top_items:
            sym = x["symbol"].upper()
            lf = logo_dir / f"{sym}.png"
            img_url = x.get("image")
            if img_url and not lf.exists():
                try:
                    r = ctx.http.get(img_url)
                    if r.content and r.content[:4] == b"\x89PNG":
                        lf.write_bytes(r.content)
                except Exception:
                    pass
    u["as_of"] = now.strftime("%Y-%m-%d %H:%M")
    u["rank"] = {c: i + 2 for i, c in enumerate(top)}   # BTC = 1, like the static list
    tracked = set(settings.BINANCE_UNIVERSE) | {"BTC"}
    new = [c for c in top if c not in tracked]
    adds_today = sum(1 for v in added.values() if v.get("added_on") == today)
    room = max(0, settings.UNIVERSE_MAX_ADDS_PER_DAY - adds_today)
    hl_names = None
    n_added = 0
    for c in new[:room]:
        if hl_names is None:
            hl_names = {x["name"] for x in ctx.http.post(HL_URL, json={"type": "meta"}).json()["universe"] if not x.get("isDelisted")}
        hl = c if c in hl_names else (f"k{c}" if f"k{c}" in hl_names else None)
        added[c] = {"added_on": today, "rank_when_added": u["rank"][c], "perp": _perp(ctx, c),
                    "coinbase": _coinbase(ctx, c), "hl": hl, "checked_on": today}
        ctx.log.info("universe: added %s (rank %d, perp %s, coinbase %s, hyperliquid %s)", c, u["rank"][c],
                     added[c]["perp"], added[c]["coinbase"], hl)
        n_added += 1
    # coins added before their perp reached the archive: look again once a day
    for c, v in added.items():
        if v.get("perp") is None and v.get("checked_on") != today:
            v["perp"], v["checked_on"] = _perp(ctx, c), today
            if v["perp"]:
                ctx.log.info("universe: %s perp now listed (%s)", c, v["perp"][0])
    u["waiting"] = new[room:]          # in the top 50, not tracked yet (daily add cap reached)
    settings.UNIVERSE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = settings.UNIVERSE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(u, indent=1, sort_keys=True))
    tmp.replace(settings.UNIVERSE_FILE)
    settings.apply_universe(u)          # later sources in this same run pick the new coins up
    return {"universe_added": n_added, "universe_tracked": len(settings.BINANCE_UNIVERSE)}
