"""
Daily net flows for ETPs read straight from the issuers (settings.ISSUER_ETFS: NEAR = NRR; HYPE = THYP + BHYP),
built from etf_fund_daily (filled by bitwise.py / twentyone.py). Runs after those two.

  fund flow on NY trading day t = (shares[t] - shares[previous row]) x NAV per share[t]
    (units on day t include that day's creations: verified for 21Shares THYP against SoSoValue on 2026-05-20;
     Bitwise assumed the same, its site says the holdings figure is on trade date and on-chain lags T+1 - unverified)
  The first row of a fund (its seed) is not a flow. If snapshots skip days (Mac off), the change lands on the next row.
  Asset total for day t (from the last fund's listing on) is written only when every fund has a flow that day, so a partial sum
  (e.g. HYPE before BHYP snapshots began on 2026-09-30) never shows up as the asset total.

Tables
  etf_flows_by_fund  per-fund rows, source='issuer'
  etf_flows_daily    per-asset rows, source='issuers' (net_assets_usd = sum of fund AUM)
"""
from __future__ import annotations

import pandas as pd

from mdh import settings
from mdh.core import db

NAME = "etf_issuers"


def fund_flows(con, ticker: str, listing: str) -> pd.DataFrame:
    d = con.execute("""SELECT date, shares_outstanding, nav_per_share, aum_usd FROM etf_fund_daily
                       WHERE ticker=? AND shares_outstanding IS NOT NULL ORDER BY date""", [ticker]).df()
    if d.empty:
        return d
    d["date"] = pd.to_datetime(d["date"]).dt.date
    d["flow"] = (d["shares_outstanding"] - d["shares_outstanding"].shift(1)) * d["nav_per_share"]
    return d[d["date"] >= pd.Timestamp(listing).date()].dropna(subset=["flow"])


def run(ctx, full=False):
    if not db.table_exists(ctx.con, "etf_fund_daily"):
        return {}
    n_fund = n_tot = 0
    for asset, fs in settings.ISSUER_ETFS.items():
        per = {}
        for f in fs:
            fl = fund_flows(ctx.con, f["ticker"], f["listing"])
            if fl.empty:
                continue
            per[f["ticker"]] = fl.set_index("date")
            n_fund += db.upsert(ctx.con, "etf_flows_by_fund", pd.DataFrame({
                "date": fl["date"], "asset": asset, "ticker": f["ticker"], "net_flow_usd": fl["flow"].round(2),
                "source": "issuer"}), ["date", "ticker", "source"])
        if not per:
            continue
        rows = []
        for day in sorted(set().union(*[set(p.index) for p in per.values()])):
            if day < max(pd.Timestamp(f["listing"]).date() for f in fs):   # totals only once every fund exists
                continue
            live = [f["ticker"] for f in fs]
            if not all(t in per and day in per[t].index for t in live):
                continue
            rows.append({"date": day, "asset": asset, "source": "issuers",
                         "net_inflow_usd": round(sum(float(per[t].at[day, "flow"]) for t in live), 2),
                         "value_traded_usd": float("nan"),
                         "net_assets_usd": sum(float(per[t].at[day, "aum_usd"]) for t in live)})
        if rows:
            df = pd.DataFrame(rows)
            df["cum_net_inflow_usd"] = df["net_inflow_usd"].cumsum().round(2)
            n_tot += db.upsert(ctx.con, "etf_flows_daily", df, ["date", "asset", "source"])
    return {"etf_flows_by_fund": n_fund, "etf_flows_daily": n_tot}
