"""
21Shares US ETPs that SoSoValue's free API doesn't carry (settings.ISSUER_ETFS entries with issuer='21shares':
HYPE via THYP).

Source: the JSON API behind 21shares.com product pages, full daily history since launch:
  https://api.primary.21shares.com/api/product_valuation_history/<TICKER>
  -> valuation_date, total_units_outstanding, nav_per_share, total_nav, market_price, underlying price.
Flows are computed in etf_issuers.py. Checked 2026-09-30: THYP 2026-05-20 = (1,280,000 - 730,000) x $30.28
= $16.65M, matching SoSoValue's reported $16.6M, so units on day t already include that day's creations.
Table: etf_fund_daily rows with source='21shares'.
"""
from __future__ import annotations

import pandas as pd

from mdh import settings
from mdh.core import db

NAME = "twentyone"
URL = "https://api.primary.21shares.com/api/product_valuation_history/{t}"


def run(ctx, full=False):
    n = 0
    for asset, fs in settings.ISSUER_ETFS.items():
        for f in fs:
            if f["issuer"] != "21shares":
                continue
            j = ctx.http.get_json(URL.format(t=f["ticker"]), headers={"User-Agent": "Mozilla/5.0"})
            d = pd.DataFrame(j["data"])
            out = pd.DataFrame({"date": pd.to_datetime(d["valuation_date"]).dt.date,
                                "shares_outstanding": pd.to_numeric(d["total_units_outstanding"]).astype("int64"),
                                "nav_per_share": pd.to_numeric(d["nav_per_share"], errors="coerce"),
                                "market_price": pd.to_numeric(d["market_price"], errors="coerce"),
                                "aum_usd": pd.to_numeric(d["total_nav"], errors="coerce"),
                                "ticker": f["ticker"], "asset": asset, "source": "21shares"})
            n += db.upsert(ctx.con, "etf_fund_daily", out, ["ticker", "date"])
    return {"etf_fund_daily": n}
