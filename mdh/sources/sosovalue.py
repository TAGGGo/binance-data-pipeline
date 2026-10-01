"""
US spot crypto ETF daily net flows (BTC, ETH, SOL, XRP) from SoSoValue.

Free-tier reality (tested 2026-09-28):
  * v1 /etfs/summary-history   -> last 30 days, all four assets, 10 req/min, 10k/month
  * v2 historicalInflowChart   -> last 300 days, BTC/ETH/SOL only (XRP empty)
  * v2 currentEtfDataMetrics   -> today's per-fund numbers (net assets, daily flow, premium, value traded) and
                                  the total token holdings, BTC/ETH/SOL only. No history, so every run stores a
                                  snapshot keyed by SoSoValue's lastUpdateDate (tables etf_fund_snapshot,
                                  etf_holdings_daily); per-fund history builds up from 2026-09-29.
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
V2_CURRENT = "https://api.sosovalue.xyz/openapi/v2/etf/currentEtfDataMetrics"


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
    # per-fund snapshot of the latest session (+ total token holdings)
    snap_n = hold_n = 0
    num = lambda x: pd.to_numeric((x or {}).get("value"), errors="coerce")
    for asset, t in V2_TYPES.items():
        j = ctx.http.post(V2_CURRENT, json={"type": t}, headers={"x-soso-api-key": key}).json()
        dd = j.get("data") or {}
        day = (dd.get("dailyNetInflow") or {}).get("lastUpdateDate")
        if not day:
            continue
        hold = pd.DataFrame([{"date": pd.Timestamp(day).date(), "asset": asset, "source": "sosovalue",
                              "token_holdings": num(dd.get("totalTokenHoldings")),
                              "net_assets_usd": num(dd.get("totalNetAssets")),
                              "daily_net_inflow_usd": num(dd.get("dailyNetInflow"))}])
        hold_n += db.upsert(ctx.con, "etf_holdings_daily", hold, ["date", "asset", "source"])
        # FAST PATH: the current-metrics endpoint shows the new session hours before summary-history does,
        # but its total is PARTIAL until every fund has reported (BlackRock IBIT/ETHA/ETHB come last,
        # status "3" = pending). Only when every fund is in (status "1" for that day) write the total
        # into etf_flows_daily; the later summary-history row has the same key and simply replaces it.
        funds = dd.get("list") or []
        pending = [f.get("ticker") for f in funds
                   if (f.get("dailyNetInflow") or {}).get("status") != "1"
                   or (f.get("dailyNetInflow") or {}).get("lastUpdateDate") != day]
        ctx.log.info("sosovalue current %s %s: %d/%d funds in%s", asset, day, len(funds) - len(pending), len(funds),
                     f" (pending {','.join(map(str, pending))})" if pending else " (complete)")
        if funds and not pending:
            fast = pd.DataFrame([{"date": pd.Timestamp(day).date(), "asset": asset, "source": "sosovalue",
                                  "net_inflow_usd": num(dd.get("dailyNetInflow")),
                                  "value_traded_usd": num(dd.get("dailyTotalValueTraded")),
                                  "net_assets_usd": num(dd.get("totalNetAssets")),
                                  "cum_net_inflow_usd": num(dd.get("cumNetInflow"))}])
            have = ctx.con.execute(f"SELECT max(date) FROM {TABLE} WHERE asset=? AND source='sosovalue'", [asset]).fetchone()[0]
            if have is None or pd.Timestamp(have).date() < fast["date"].iloc[0]:
                total += db.upsert(ctx.con, TABLE, fast, ["date", "asset", "source"])
                ctx.log.info("sosovalue fast path: %s %s written from current metrics", asset, day)
        rows = [{"date": pd.Timestamp((f.get("netAssets") or {}).get("lastUpdateDate") or day).date(), "asset": asset,
                 "ticker": f.get("ticker"), "institute": f.get("institute"), "source": "sosovalue",
                 "net_assets_usd": num(f.get("netAssets")), "daily_net_inflow_usd": num(f.get("dailyNetInflow")),
                 "cum_net_inflow_usd": num(f.get("cumNetInflow")), "value_traded_usd": num(f.get("dailyValueTraded")),
                 "premium": num(f.get("discountPremiumRate")), "fee": num(f.get("fee"))}
                for f in dd.get("list") or []]
        if rows:
            snap_n += db.upsert(ctx.con, "etf_fund_snapshot", pd.DataFrame(rows), ["date", "asset", "ticker", "source"])
    return {TABLE: total, "etf_fund_snapshot": snap_n, "etf_holdings_daily": hold_n}
