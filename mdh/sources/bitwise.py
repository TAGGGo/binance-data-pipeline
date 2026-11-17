"""
Bitwise single-asset ETPs that SoSoValue doesn't carry (settings.ISSUER_ETFS entries with issuer='bitwise':
NEAR via NRR, HYPE via BHYP).

Source: the fund's own site (nrretf.com, bhypetf.com). Originally Next.js Pages router (__NEXT_DATA__);
migrated in 2026-11 to Next.js App Router flight payloads (self.__next_f.push) containing `details`
{asOfDate, netAssets, sharesOutstanding, marketPrice} and `holdings` {asOfDate, basket: [{marketValue,
assetInTrust, assetPerShare}]}. When `holdings.asOfDate` updates ahead of `details.asOfDate` (which
happens early in the morning), we derive shares_outstanding = round(assetInTrust / assetPerShare / 10000) * 10000
for the newer holdings date, and overwrite it with official `details` once `details.asOfDate` catches up.

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


def _extract_app_router(html: str) -> tuple[dict, dict] | None:
    chunks = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', html, re.S)
    if not chunks:
        return None
    joined = "".join(json.loads(f'"{c}"') for c in chunks)
    m = re.search(r'"details"\s*:\s*(?=\{\s*"asOfDate"\s*:)', joined)
    if not m:
        return None
    dec = json.JSONDecoder()
    det, pos = dec.raw_decode(joined, m.end())
    hm = re.search(r'"holdings"\s*:\s*(?=\{)', joined[pos:])
    if not hm:
        return det, {}
    holdings, _ = dec.raw_decode(joined, pos + hm.end())
    return det, holdings


def parse(html: str) -> list[dict]:
    """Return 1 or 2 snapshot dicts (details date, plus newer holdings date if holdings updated ahead of details)."""
    extracted = _extract_app_router(html)
    if extracted is not None:
        det, holdings = extracted
    else:
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
        if not m:
            raise ValueError("bitwise: neither Next.js flight payload nor __NEXT_DATA__ found")
        fd = json.loads(m.group(1))["props"]["pageProps"]["fundData"]["data"]
        det, holdings = fd["fundDetails"], fd.get("holdings") or {}

    shares, aum = float(det["sharesOutstanding"]), float(det["netAssets"])
    if not shares or not aum:
        raise ValueError(f"bitwise: empty fund details {det}")
    det_date = str(det["asOfDate"])[:10]
    mp = det.get("marketPrice")
    market_price = float(mp["value"]) if isinstance(mp, dict) and mp.get("value") is not None else np.nan

    basket = holdings.get("basket") or []
    hold_date = str(holdings.get("asOfDate") or det_date)[:10]
    b0 = basket[0] if basket else {}
    coins_raw = b0.get("assetInTrust") if "assetInTrust" in b0 else b0.get("shares")

    out = []
    if hold_date == det_date and coins_raw is not None:
        coins = float(coins_raw)
        cps = float(b0["assetPerShare"]) if b0.get("assetPerShare") else coins / shares
        out.append({
            "date": pd.Timestamp(det_date).date(),
            "shares_outstanding": int(round(shares)),
            "aum_usd": aum,
            "nav_per_share": aum / shares,
            "market_price": market_price,
            "token_holdings": coins,
            "coins_per_share": cps,
        })
    else:
        out.append({
            "date": pd.Timestamp(det_date).date(),
            "shares_outstanding": int(round(shares)),
            "aum_usd": aum,
            "nav_per_share": aum / shares,
            "market_price": market_price,
            "token_holdings": np.nan,
            "coins_per_share": np.nan,
        })
        if hold_date > det_date and coins_raw is not None and b0.get("assetPerShare") and b0.get("marketValue"):
            coins = float(coins_raw)
            cps = float(b0["assetPerShare"])
            aum_h = float(b0["marketValue"])
            shares_h = int(round(coins / cps / 10000.0)) * 10000
            if shares_h > 0 and aum_h > 0:
                out.append({
                    "date": pd.Timestamp(hold_date).date(),
                    "shares_outstanding": shares_h,
                    "aum_usd": aum_h,
                    "nav_per_share": aum_h / shares_h,
                    "market_price": np.nan,
                    "token_holdings": coins,
                    "coins_per_share": cps,
                })
    return out


def funds():
    return [(a, f) for a, fs in settings.ISSUER_ETFS.items() for f in fs if f["issuer"] == "bitwise"]


def run(ctx, full=False):
    n = 0
    has_table = db.table_exists(ctx.con, "etf_fund_daily")
    for asset, f in funds():
        snaps = parse(ctx.http.get(f["url"], headers={"User-Agent": "Mozilla/5.0"}).text)
        for snap in snaps:
            if pd.isna(snap["token_holdings"]) and has_table:
                prev = ctx.con.execute(
                    "SELECT token_holdings, coins_per_share FROM etf_fund_daily WHERE ticker=? AND date=?",
                    [f["ticker"], snap["date"]],
                ).fetchone()
                if prev and prev[0] is not None:
                    snap["token_holdings"], snap["coins_per_share"] = prev[0], prev[1]
            row = pd.DataFrame([{**snap, "ticker": f["ticker"], "asset": asset, "source": "bitwise"}])
            n += db.upsert(ctx.con, "etf_fund_daily", row, ["ticker", "date"])
    return {"etf_fund_daily": n}
