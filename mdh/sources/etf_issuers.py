"""
Daily net flows for ETPs read straight from the issuers (settings.ISSUER_ETFS: NEAR = NRR; HYPE = THYP + BHYP;
SUI = GSUI + SUIS + TSUI), built from etf_fund_daily (filled by grayscale.py / bitwise.py / twentyone.py / canary.py).
Runs after those.

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
from mdh.sources.sosovalue import cmc_fund_flows

NAME = "etf_issuers"


def fund_flows(con, ticker: str, listing: str, lag: int = 0) -> pd.DataFrame:
    """lag=0: shares on day t include day t's creations (21Shares, Canary, Bitwise).
    lag=1: shares on day t reflect the previous trading day's creations (Grayscale workbooks, T+1), so
    flow[t] = (shares[t+1] - shares[t]) x NAV[t] and the newest day stays pending until the next file."""
    d = con.execute("""SELECT date, shares_outstanding, nav_per_share, aum_usd FROM etf_fund_daily
                       WHERE ticker=? AND shares_outstanding IS NOT NULL ORDER BY date""", [ticker]).df()
    if d.empty:
        return d
    d["date"] = pd.to_datetime(d["date"]).dt.date
    so = d["shares_outstanding"].astype("float64")
    d["flow"] = (so.shift(-1) - so if lag else so - so.shift(1)) * d["nav_per_share"]
    return d[d["date"] >= pd.Timestamp(listing).date()].dropna(subset=["flow"])


def _latest_aum(con, ticker: str, day) -> float:
    row = con.execute(
        "SELECT aum_usd FROM etf_fund_daily WHERE ticker=? AND date<=? AND aum_usd IS NOT NULL ORDER BY date DESC LIMIT 1",
        [ticker, day],
    ).fetchone()
    return float(row[0]) if row and row[0] is not None else 0.0


def run(ctx, full=False):
    if not db.table_exists(ctx.con, "etf_fund_daily"):
        return {}
    n_fund = n_tot = 0
    cmc_cache: dict[tuple[str, str], float] | None = None

    def get_cmc() -> dict[tuple[str, str], float]:
        nonlocal cmc_cache
        if cmc_cache is None:
            cmc_cache = cmc_fund_flows(ctx)
        return cmc_cache

    for asset, fs in settings.ISSUER_ETFS.items():
        per = {}
        for f in fs:
            fl = fund_flows(ctx.con, f["ticker"], f["listing"], f.get("flow_lag", 0))
            if fl.empty:
                continue
            per[f["ticker"]] = fl.set_index("date")
            n_fund += db.upsert(ctx.con, "etf_flows_by_fund", pd.DataFrame({
                "date": fl["date"], "asset": asset, "ticker": f["ticker"], "net_flow_usd": fl["flow"].round(2),
                "source": "issuer"}), ["date", "ticker", "source"])
        if not per:
            continue
        live = [f["ticker"] for f in fs]
        all_days = sorted(set().union(*[set(p.index) for p in per.values()]))
        complete_days = [d for d in all_days if all(t in per and d in per[t].index for t in live)]
        max_complete = max(complete_days) if complete_days else None
        min_listing = max(pd.Timestamp(f["listing"]).date() for f in fs)
        rows = []
        for day in all_days:
            if day < min_listing:   # totals only once every fund exists
                continue
            missing = [t for t in live if t not in per or day not in per[t].index]
            if not missing:
                net_inflow = sum(float(per[t].at[day, "flow"]) for t in live)
                net_assets = sum(float(per[t].at[day, "aum_usd"]) for t in live)
            elif max_complete is None or day > max_complete:
                cmc = get_cmc()
                d_str = day.isoformat()
                if not all((d_str, t.upper()) in cmc for t in missing):
                    continue
                net_inflow = sum(
                    float(per[t].at[day, "flow"]) if (t in per and day in per[t].index) else cmc[(d_str, t.upper())]
                    for t in live
                )
                net_assets = sum(
                    float(per[t].at[day, "aum_usd"]) if (t in per and day in per[t].index) else _latest_aum(ctx.con, t, day)
                    for t in live
                )
                ctx.log.info("etf_issuers+cmc: %s %s filled missing %s from CMC", asset, d_str, ",".join(missing))
            else:
                continue
            rows.append({"date": day, "asset": asset, "source": "issuers",
                         "net_inflow_usd": round(net_inflow, 2),
                         "value_traded_usd": float("nan"),
                         "net_assets_usd": net_assets})
        if rows:
            df = pd.DataFrame(rows)
            df["cum_net_inflow_usd"] = df["net_inflow_usd"].cumsum().round(2)
            n_tot += db.upsert(ctx.con, "etf_flows_daily", df, ["date", "asset", "source"])
    return {"etf_flows_by_fund": n_fund, "etf_flows_daily": n_tot}
