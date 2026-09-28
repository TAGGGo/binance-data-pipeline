"""Stablecoin circulating supply (USD) from DefiLlama — total, USDT, USDC. Free, no key."""
import pandas as pd

from mdh import settings
from mdh.core import db

NAME = "defillama"
TABLE = "stablecoin_supply"
URL = "https://stablecoins.llama.fi/stablecoincharts/all"


def run(ctx, full=False):
    total = 0
    for name, sid in settings.DEFILLAMA_STABLES.items():
        params = {"stablecoin": sid} if sid else None
        j = ctx.http.get_json(URL, params=params)
        df = pd.DataFrame({
            "stablecoin": name,
            "date": pd.to_datetime([int(x["date"]) for x in j], unit="s").date,
            "circulating_usd": [(x.get("totalCirculatingUSD") or {}).get("peggedUSD") for x in j],
        }).dropna()
        if not full:
            df = df.tail(30)   # the endpoint always returns full history; only rewrite the recent tail
        total += db.upsert(ctx.con, TABLE, df, ["stablecoin", "date"])
    return {TABLE: total}
