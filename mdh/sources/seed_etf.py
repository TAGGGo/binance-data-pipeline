"""
Load hand-collected ETF flow history from seeds/etf/ (tracked in git):
  * farside_etf_flows_btc_eth.csv  per-fund daily flows in $m (Farside "all data" pages)
  * sosovalue_etf_flows_{sol,xrp}.csv  aggregate daily flows since launch
Idempotent: safe to run any time.
"""
import pandas as pd

from mdh import settings
from mdh.core import db

NAME = "seed_etf"


def run(ctx, full=False):
    d = settings.SEEDS_DIR / "etf"
    out = {}

    fs = pd.read_csv(d / "farside_etf_flows_btc_eth.csv", parse_dates=["date"])
    fs["date"] = fs["date"].dt.date
    fs["net_flow_usd"] = fs.pop("net_flow_usd_m") * 1e6
    by_fund = fs[fs.ticker != "Total"].dropna(subset=["net_flow_usd"]).assign(source="farside")
    out["etf_flows_by_fund"] = db.upsert(ctx.con, "etf_flows_by_fund", by_fund[
        ["date", "asset", "ticker", "net_flow_usd", "source"]], ["date", "asset", "ticker", "source"])

    tot = fs[fs.ticker == "Total"].copy()
    # Farside lists holidays as 0.0 totals with every fund '-': drop those non-trading rows
    traded = by_fund.groupby(["date", "asset"]).size().rename("n").reset_index()
    tot = tot.merge(traded, on=["date", "asset"], how="inner")
    daily = pd.DataFrame({"date": tot["date"], "asset": tot["asset"], "source": "farside",
                          "net_inflow_usd": tot["net_flow_usd"]})
    n = db.upsert(ctx.con, "etf_flows_daily", daily, ["date", "asset", "source"])

    for asset in ("sol", "xrp"):
        f = d / f"sosovalue_etf_flows_{asset}.csv"
        if not f.exists():
            continue
        s = pd.read_csv(f)
        s = s.rename(columns={"totalNetInflow": "net_inflow", "totalValueTraded": "value_traded",
                              "totalNetAssets": "net_assets", "cumNetInflow": "cum_net_inflow"})
        df = pd.DataFrame({
            "date": pd.to_datetime(s["date"]).dt.date, "asset": asset.upper(), "source": "sosovalue",
            "net_inflow_usd": s["net_inflow"], "value_traded_usd": s["value_traded"],
            "net_assets_usd": s["net_assets"], "cum_net_inflow_usd": s["cum_net_inflow"]})
        n += db.upsert(ctx.con, "etf_flows_daily", df, ["date", "asset", "source"])
    out["etf_flows_daily"] = n
    return out
