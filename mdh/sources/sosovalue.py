"""
US spot crypto ETF daily net flows (BTC, ETH, SOL, XRP) from SoSoValue.

Free-tier reality (tested 2026-09-28):
  * v1 /etfs/summary-history   -> last 30 days, all four assets, 10 req/min, 10k/month
  * v2 historicalInflowChart   -> last 300 days, BTC/ETH/SOL only (XRP empty)
Older history lives in seeds/etf/ (collected once from Farside + SoSoValue's site) and is
loaded by `seed_etf.py`. A daily run of this module keeps everything current.
"""
import os

import pandas as pd

from mdh import settings
from mdh.core import db

NAME = "sosovalue"
TABLE = "etf_flows_daily"
V1 = "https://openapi.sosovalue.com/openapi/v1/etfs/summary-history"
V2 = "https://api.sosovalue.xyz/openapi/v2/etf/historicalInflowChart"
V2_TYPES = {"BTC": "us-btc-spot", "ETH": "us-eth-spot", "SOL": "us-sol-spot"}


def _frame(asset, rows, date_key, m):
    df = pd.DataFrame(rows)
    return pd.DataFrame({
        "date": pd.to_datetime(df[date_key].str[:10]).dt.date,
        "asset": asset,
        "source": "sosovalue",
        "net_inflow_usd": pd.to_numeric(df[m["flow"]], errors="coerce"),
        "value_traded_usd": pd.to_numeric(df[m["traded"]], errors="coerce"),
        "net_assets_usd": pd.to_numeric(df[m["assets"]], errors="coerce"),
        "cum_net_inflow_usd": pd.to_numeric(df[m["cum"]], errors="coerce"),
    })


def run(ctx, full=False):
    key = os.environ.get("SOSOVALUE_API_KEY")
    if not key:
        raise RuntimeError("SOSOVALUE_API_KEY missing from .env")
    total = 0
    if full:   # 300-day window where available
        for asset, t in V2_TYPES.items():
            j = ctx.http.post(V2, json={"type": t}, headers={"x-soso-api-key": key}).json()
            if j.get("data"):
                total += db.upsert(ctx.con, TABLE, _frame(asset, j["data"], "date", {
                    "flow": "totalNetInflow", "traded": "totalValueTraded",
                    "assets": "totalNetAssets", "cum": "cumNetInflow"}), ["date", "asset", "source"])
    for asset in settings.ETF_ASSETS:   # last 30 days, every asset
        j = ctx.http.get_json(V1, params={"symbol": asset, "country_code": "US"},
                              headers={"x-soso-api-key": key})
        if j.get("code") != 0 or not j.get("data"):
            ctx.log.warning("sosovalue %s: %s", asset, j.get("message"))
            continue
        total += db.upsert(ctx.con, TABLE, _frame(asset, j["data"], "date", {
            "flow": "total_net_inflow", "traded": "total_value_traded",
            "assets": "total_net_assets", "cum": "cum_net_inflow"}), ["date", "asset", "source"])
    return {TABLE: total}
