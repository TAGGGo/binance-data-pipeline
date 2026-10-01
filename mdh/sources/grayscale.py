"""
Grayscale single-asset ETPs whose flows SoSoValue doesn't carry (today: ZEC via The Zcash ETF, ZCSH).

Source: the fund's own daily performance workbook that thezcashetf.com links to ("Daily Performance" sheet:
Date, Shares Outstanding, NAV Per Share, Market Price Per Share, AUM; full history since the trust's OTC days).
It sits on a public S3 bucket, so no browser / bot check is involved.

Flows (checked 2026-09-29 against the figures press reports quote from Grayscale, e.g. 2026-09-08 +$112.1M,
2026-09-17 +$46.6M, 2026-09-21 +$2.4M, 2026-09-22 +$32.8M, 2026-09-23..25 zero):
  * Shares outstanding on day t reflects creations/redemptions traded the previous trading day (T+1 settlement),
    so the flow for trade date t = (shares[next trading day] - shares[t]) x NAV per share[t].
  * The newest day's flow is unknown until the next file (its shares appear a day later), so it is left out.
  * Share splits (ZCSH 3-for-1 effective 2026-09-30) are detected as shares x k with NAV / k on the same day and
    folded out before differencing.
  * Only days on/after the ETP listing date count; before that the trust had no creation/redemption program.

Tables
  etf_fund_daily   (ticker, date, asset, shares_outstanding, nav_per_share, market_price, aum_usd, source)
  etf_flows_daily  rows with source='grayscale' (net_inflow_usd, net_assets_usd, cum_net_inflow_usd)
"""
from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd

from mdh import settings
from mdh.core import db

NAME = "grayscale"
URL = "https://reporting-prod-20231113144948145500000003.s3.amazonaws.com/product-performance/{pid}.xlsx"
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}


def read_sheet(blob: bytes, sheet: str) -> pd.DataFrame:
    """Minimal .xlsx reader (no openpyxl dependency): first row = header."""
    z = zipfile.ZipFile(io.BytesIO(blob))
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS):
            shared.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    target = {r.get("Id"): r.get("Target") for r in rels}
    rid = next(s.get(f"{{{NS['r']}}}id") for s in wb.find("m:sheets", NS) if s.get("name") == sheet)
    path = "xl/" + target[rid].lstrip("/").replace("xl/", "")
    rows = []
    for row in ET.fromstring(z.read(path)).find("m:sheetData", NS):
        vals = {}
        for c in row:
            col = re.match(r"[A-Z]+", c.get("r")).group()
            v = c.find("m:v", NS)
            if c.get("t") == "s" and v is not None:
                val = shared[int(v.text)]
            elif c.get("t") == "inlineStr":
                val = "".join(t.text or "" for t in c.iter(f"{{{NS['m']}}}t"))
            else:
                val = v.text if v is not None else None
            vals[col] = val
        rows.append(vals)
    cols = sorted({k for r in rows for k in r}, key=lambda s: (len(s), s))
    df = pd.DataFrame([[r.get(c) for c in cols] for r in rows])
    df.columns = df.iloc[0]
    return df.iloc[1:].reset_index(drop=True)


def daily_frame(blob: bytes) -> pd.DataFrame:
    d = read_sheet(blob, "Daily Performance")
    out = pd.DataFrame({
        "date": pd.to_datetime(d["Date"]).dt.date,
        "shares_outstanding": pd.to_numeric(d["Shares Outstanding"], errors="coerce"),
        "nav_per_share": pd.to_numeric(d["NAV Per Share"], errors="coerce"),
        "market_price": pd.to_numeric(d["Market Price Per Share"], errors="coerce"),
        "aum_usd": pd.to_numeric(d["AUM"], errors="coerce"),
    })
    return out.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)


def flows(daily: pd.DataFrame, listing: str) -> pd.DataFrame:
    d = daily.dropna(subset=["shares_outstanding", "nav_per_share"]).reset_index(drop=True).copy()
    # fold out splits: shares jump by an integer factor k while NAV drops by ~k the same day
    adj = np.ones(len(d))
    for i in range(1, len(d)):
        r = d.at[i, "shares_outstanding"] / d.at[i - 1, "shares_outstanding"]
        k = round(r)
        nav_r = d.at[i - 1, "nav_per_share"] / d.at[i, "nav_per_share"]
        if k >= 2 and abs(r / k - 1) < 0.05 and abs(nav_r / k - 1) < 0.35:
            adj[:i] *= k   # express earlier days in post-split shares
    d["so_adj"] = d["shares_outstanding"] * adj
    d["nav_adj"] = d["nav_per_share"] / adj
    d["next_so"] = d["so_adj"].shift(-1)
    d["net_inflow_usd"] = (d["next_so"] - d["so_adj"]) * d["nav_adj"]
    d = d[(pd.to_datetime(d["date"]) >= pd.Timestamp(listing)) & d["next_so"].notna()].copy()
    d["cum_net_inflow_usd"] = d["net_inflow_usd"].cumsum()
    return d


def run(ctx, full=False):
    n_fund = n_flow = 0
    for asset, f in settings.GRAYSCALE_ETFS.items():
        blob = ctx.http.get(URL.format(pid=f["product_id"])).content
        daily = daily_frame(blob)
        fund = daily.assign(ticker=f["ticker"], asset=asset, source="grayscale")
        n_fund += db.upsert(ctx.con, "etf_fund_daily", fund, ["ticker", "date"])
        fl = flows(daily, f["etf_listing"])
        out = pd.DataFrame({"date": fl["date"], "asset": asset, "source": "grayscale",
                            "net_inflow_usd": fl["net_inflow_usd"].round(2), "value_traded_usd": np.nan,
                            "net_assets_usd": fl["aum_usd"], "cum_net_inflow_usd": fl["cum_net_inflow_usd"].round(2)})
        n_flow += db.upsert(ctx.con, "etf_flows_daily", out, ["date", "asset", "source"])
    return {"etf_fund_daily": n_fund, "etf_flows_daily": n_flow}
