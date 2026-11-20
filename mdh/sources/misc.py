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


# ----------------------------------------------------------------------------- CNN Fear & Greed (US stocks)
class cnn_feargreed:
    """CNN's US-stock Fear & Greed index + its 7 inputs (daily since 2021-01-04).

    NOT the same as `feargreed` (alternative.me, crypto). The endpoint returns 418 to bare requests:
    it needs browser-like Referer/Origin headers. Raw input values per day go to cnn_fear_greed; the
    0-100 score of each input is only published for the latest day -> cnn_fg_components snapshot.
    """
    NAME = "cnn_feargreed"
    TABLE = "cnn_fear_greed"
    COMP_TABLE = "cnn_fg_components"
    URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata/{start}"
    HEADERS = {"Referer": "https://www.cnn.com/", "Origin": "https://www.cnn.com",
               "Accept-Language": "en-US,en;q=0.9"}
    RAW = {"market_momentum_sp500": "spx", "market_momentum_sp125": "spx_ma125",
           "stock_price_strength": "net_new_highs_pct", "stock_price_breadth": "mcclellan_vol_sum",
           "put_call_options": "put_call_5d", "market_volatility_vix": "vix", "market_volatility_vix_50": "vix_ma50",
           "junk_bond_demand": "junk_spread", "safe_haven_demand": "stock_bond_20d"}
    COMPONENTS = ["market_momentum_sp500", "stock_price_strength", "stock_price_breadth", "put_call_options",
                  "market_volatility_vix", "junk_bond_demand", "safe_haven_demand"]

    @staticmethod
    def run(ctx, full=False):
        last = None if full else db.max_value(ctx.con, cnn_feargreed.TABLE, "date")
        start = (pd.Timestamp(last) - pd.Timedelta(days=10)).strftime("%Y-%m-%d") if last else "2021-01-01"
        j = ctx.http.get_json(cnn_feargreed.URL.format(start=start), headers=cnn_feargreed.HEADERS)

        def series(key, col):
            a = (j.get(key) or {}).get("data") or []
            if not a:
                return None
            df = pd.DataFrame(a)
            # daily points sit at 00:00 UTC of the NY trading date; the extra 'now' point (not on midnight)
            # just repeats the latest value, so drop it (the latest score is also in cnn_fg_components)
            df = df[(df["x"].astype("int64") % 86_400_000) == 0]
            df["date"] = pd.to_datetime(df["x"], unit="ms", utc=True).dt.date
            df = df.groupby("date").last()
            out = df[["y"]].rename(columns={"y": col})
            if col == "score" and "rating" in df:
                out["rating"] = df["rating"]
            return out

        parts = [series("fear_and_greed_historical", "score")]
        parts += [series(k, c) for k, c in cnn_feargreed.RAW.items()]
        parts = [p for p in parts if p is not None]
        if not parts:
            return {cnn_feargreed.TABLE: 0}
        out = pd.concat(parts, axis=1).reset_index()
        n = db.upsert(ctx.con, cnn_feargreed.TABLE, out, ["date"])

        fg = j.get("fear_and_greed") or {}
        asof = pd.Timestamp(fg["timestamp"]).tz_convert("America/New_York").date() if fg.get("timestamp") else None
        rows = [{"date": asof, "component": "fear_and_greed", "score": fg.get("score"), "rating": fg.get("rating"),
                 "prev_close": fg.get("previous_close"), "prev_1w": fg.get("previous_1_week"),
                 "prev_1m": fg.get("previous_1_month"), "prev_1y": fg.get("previous_1_year")}]
        for k in cnn_feargreed.COMPONENTS:
            v = j.get(k) or {}
            rows.append({"date": asof, "component": k, "score": v.get("score"), "rating": v.get("rating"),
                         "prev_close": None, "prev_1w": None, "prev_1m": None, "prev_1y": None})
        m = db.upsert(ctx.con, cnn_feargreed.COMP_TABLE, pd.DataFrame(rows), ["date", "component"]) if asof else 0
        return {cnn_feargreed.TABLE: n, cnn_feargreed.COMP_TABLE: m}
