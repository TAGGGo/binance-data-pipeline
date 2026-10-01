"""
Bitwise single-asset ETPs that SoSoValue doesn't carry (settings.ISSUER_ETFS entries with issuer='bitwise':
NEAR via NRR, HYPE via BHYP).

Source: the fund's own site (nrretf.com, bhypetf.com). It is a Next.js page whose embedded __NEXT_DATA__ JSON has
fundData.fundDetails {asOfDate, netAssets, sharesOutstanding} and fundData.holdings.basket[0].shares (coins held).
Only the latest day is published (no history download), so every hourly run stores a snapshot keyed by asOfDate
and history builds forward from the first run (2026-09-30). Note: the visible "As of" label in the staking section
lags a day; the JSON asOfDate matches the holdings value (checked against the 4 PM ET price on 2026-09-29).

Flows are computed in etf_issuers.py from these snapshots.
Table: etf_fund_daily rows with source='bitwise' (+ token_holdings).
"""
from __future__ import annotations

import json
import re

import numpy as np
import pandas as pd

from mdh import settings
from mdh.core import db

NAME = "bitwise"


def parse(html: str) -> dict:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise ValueError("bitwise: __NEXT_DATA__ not found")
    fd = json.loads(m.group(1))["props"]["pageProps"]["fundData"]["data"]
    det, basket = fd["fundDetails"], (fd.get("holdings") or {}).get("basket") or []
    shares, aum = float(det["sharesOutstanding"]), float(det["netAssets"])
    if not shares or not aum:
        raise ValueError(f"bitwise: empty fund details {det}")
    coins = float(basket[0]["shares"]) if basket else np.nan
    return {"date": pd.Timestamp(det["asOfDate"]).date(), "shares_outstanding": int(round(shares)), "aum_usd": aum,
            "nav_per_share": aum / shares, "token_holdings": coins, "coins_per_share": coins / shares}


def funds():
    return [(a, f) for a, fs in settings.ISSUER_ETFS.items() for f in fs if f["issuer"] == "bitwise"]


def run(ctx, full=False):
    n = 0
    for asset, f in funds():
        snap = parse(ctx.http.get(f["url"], headers={"User-Agent": "Mozilla/5.0"}).text)
        row = pd.DataFrame([{**snap, "market_price": np.nan, "ticker": f["ticker"], "asset": asset, "source": "bitwise"}])
        n += db.upsert(ctx.con, "etf_fund_daily", row, ["ticker", "date"])
    return {"etf_fund_daily": n}
