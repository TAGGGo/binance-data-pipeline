"""
Coinbase taker flow (who is hitting the market on Coinbase), aggregated to 15-minute buckets.

Coinbase candles carry no buy/sell split, so we page the public trades endpoint (1000 trades per call, newest
first, `cb-after` cursor) back to the last stored bucket and aggregate. Coinbase's `side` is the MAKER side:
side == 'sell' means the taker bought. Binance's taker split already comes with its klines (bn_kline_1h,
binance_1h spot_/futures_taker_buy_quote_volume), so Binance needs nothing here.

Table
  cb_taker_15m (symbol, ts, buy_usd, sell_usd, delta_usd, buy_qty, sell_qty, trades, vwap)
  ts = bucket start (UTC). First run backfills settings.CB_TRADES_BACKFILL_HOURS; each later run redoes the
  newest stored bucket (it was partial) and everything after it. BTC-USD is ~30k trades/hour = ~30 calls.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from mdh import settings
from mdh.core import db

URL = "https://api.exchange.coinbase.com/products/{p}/trades"
TABLE = "cb_taker_15m"


def aggregate(trades: pd.DataFrame, symbol: str) -> pd.DataFrame:
    t = trades.copy()
    t["ts"] = pd.to_datetime(t["time"], format="ISO8601", utc=True).dt.tz_localize(None).dt.floor("15min")
    t["qty"] = t["size"].astype(float)
    t["usd"] = t["qty"] * t["price"].astype(float)
    tb = t["side"] == "sell"          # maker sold -> taker bought
    t["buy_usd"], t["sell_usd"] = t.usd.where(tb, 0.0), t.usd.where(~tb, 0.0)
    t["buy_qty"], t["sell_qty"] = t.qty.where(tb, 0.0), t.qty.where(~tb, 0.0)
    g = t.groupby("ts").agg(buy_usd=("buy_usd", "sum"), sell_usd=("sell_usd", "sum"), buy_qty=("buy_qty", "sum"),
                            sell_qty=("sell_qty", "sum"), trades=("usd", "size"), usd=("usd", "sum"), qty=("qty", "sum"))
    g["delta_usd"] = g.buy_usd - g.sell_usd
    g["vwap"] = g.usd / g.qty
    g = g.drop(columns=["usd", "qty"]).reset_index()
    g.insert(0, "symbol", symbol)
    return g


class coinbase_trades:
    NAME = "coinbase_trades"

    @staticmethod
    def run(ctx, full=False):
        now = pd.Timestamp(datetime.now(timezone.utc).replace(tzinfo=None))
        total = 0
        for sym in settings.CB_TRADES_PRODUCTS:
            last = None if full else db.max_value(ctx.con, TABLE, "ts", "symbol = ?", [sym])
            start = pd.Timestamp(last) if last is not None else \
                (now - pd.Timedelta(hours=settings.CB_TRADES_BACKFILL_HOURS)).floor("15min")
            rows, after, capped = [], None, False
            for i in range(settings.CB_TRADES_MAX_PAGES):
                r = ctx.http.get(URL.format(p=f"{sym}-USD"), params={"limit": 1000, **({"after": after} if after else {})})
                page = r.json()
                if not page:
                    break
                rows += page
                after = r.headers.get("cb-after")
                if pd.Timestamp(page[-1]["time"]).tz_localize(None) < start or not after:
                    break
            else:
                capped = True
            if not rows:
                continue
            g = aggregate(pd.DataFrame(rows), sym)
            g = g[g.ts >= start]
            if capped and len(g):           # oldest bucket is incomplete when we hit the page cap
                g = g[g.ts > g.ts.min()]
            total += db.upsert(ctx.con, TABLE, g, ["symbol", "ts"])
        return {TABLE: total}
