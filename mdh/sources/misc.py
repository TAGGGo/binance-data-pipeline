"""Small free sources: Deribit DVOL, Fear & Greed index, CoinGecko global snapshot."""
import os
import time
from datetime import datetime, timezone

import pandas as pd

from mdh import settings
from mdh.core import db


# ----------------------------------------------------------------------------- Deribit DVOL
class deribit:
    NAME = "deribit"
    TABLE = "deribit_dvol"
    URL = "https://www.deribit.com/api/v2/public/get_volatility_index_data"

    @staticmethod
    def run(ctx, full=False):
        total = 0
        now = int(time.time() * 1000)
        day = 86_400_000
        for cur in settings.DERIBIT_DVOL:
            last = None if full else db.max_value(ctx.con, deribit.TABLE, "date", "currency = ?", [cur])
            start = (int(pd.Timestamp(last).timestamp() * 1000) - 5 * day) if last else \
                int(pd.Timestamp("2021-03-01").timestamp() * 1000)
            rows, end = [], now
            while end > start:   # API returns <=1000 points per call, walk backwards
                j = ctx.http.get_json(deribit.URL, params={"currency": cur, "resolution": "1D",
                                                           "start_timestamp": start, "end_timestamp": end})
                data = j["result"]["data"]
                if not data:
                    break
                rows += data
                first = min(r[0] for r in data)
                if first <= start or len(data) < 2:
                    break
                end = first - 1
            if not rows:
                continue
            df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close"])
            df.insert(0, "currency", cur)
            df["date"] = pd.to_datetime(df.pop("ts"), unit="ms").dt.date
            total += db.upsert(ctx.con, deribit.TABLE, df, ["currency", "date"])
        return {deribit.TABLE: total}


# ----------------------------------------------------------------------------- Fear & Greed
class feargreed:
    NAME = "feargreed"
    TABLE = "fear_greed"
    URL = "https://api.alternative.me/fng/"

    @staticmethod
    def run(ctx, full=False):
        j = ctx.http.get_json(feargreed.URL, params={"limit": 0 if full else 30})
        df = pd.DataFrame(j["data"])
        out = pd.DataFrame({"date": pd.to_datetime(df["timestamp"].astype(int), unit="s").dt.date,
                            "value": df["value"].astype(int),
                            "classification": df["value_classification"]})
        return {feargreed.TABLE: db.upsert(ctx.con, feargreed.TABLE, out, ["date"])}


# ----------------------------------------------------------------------------- CoinGecko snapshot
class coingecko:
    NAME = "coingecko"
    TABLE = "cg_global_snapshot"
    URL = "https://api.coingecko.com/api/v3/global"

    @staticmethod
    def run(ctx, full=False):
        h = {"x-cg-demo-api-key": os.environ["COINGECKO_DEMO_KEY"]} if os.environ.get("COINGECKO_DEMO_KEY") else {}
        d = ctx.http.get_json(coingecko.URL, headers=h)["data"]
        pct = d["market_cap_percentage"]
        row = {
            "ts": datetime.now(timezone.utc).replace(tzinfo=None, second=0, microsecond=0),
            "total_mcap_usd": d["total_market_cap"]["usd"],
            "total_volume_usd": d["total_volume"]["usd"],
            "active_cryptos": d.get("active_cryptocurrencies"),
        }
        for k in ("btc", "eth", "usdt", "usdc", "sol", "xrp", "bnb"):
            row[f"{k}_pct"] = pct.get(k)
        return {coingecko.TABLE: db.upsert(ctx.con, coingecko.TABLE, pd.DataFrame([row]), ["ts"])}


# ----------------------------------------------------------------------------- Cboe VIX
class cboe:
    """Official daily VIX OHLC from Cboe (1990+). Fresher than FRED's VIXCLS, which lags days."""
    NAME = "cboe"
    TABLE = "vix_daily"
    URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"

    @staticmethod
    def run(ctx, full=False):
        import io
        df = pd.read_csv(io.StringIO(ctx.http.get(cboe.URL).text))
        df.columns = [c.strip().lower() for c in df.columns]
        df["date"] = pd.to_datetime(df["date"], format="%m/%d/%Y").dt.date
        if not full:
            df = df.tail(30)
        return {cboe.TABLE: db.upsert(ctx.con, cboe.TABLE, df[["date", "open", "high", "low", "close"]], ["date"])}
