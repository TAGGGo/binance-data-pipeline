"""
Canary Capital US ETPs that SoSoValue's free API doesn't carry (settings.ISSUER_ETFS entries with issuer='canary':
SUI via SUIS).

Source: the fund page (e.g. https://canaryetfs.com/suis/). It embeds a wpDataTables table (id "table_15") with the
daily history of EVERY Canary fund since launch: Fund Name, Fund Ticker, CUSIP, Net Assets, Shares Outstanding,
NAV, ..., Market Price, ..., Rate Date (MM/DD/YYYY). We parse that table and keep the configured tickers.
Checked 2026-10-07: SUIS 2026-02-17 seed 1,000,000 shares x $25; 2026-10-06 1,140,000 shares, NAV $30.68, net assets
$34,979,333.77 (matches the page header).

Flows are computed in etf_issuers.py (same-day convention as 21Shares; see its docstring).
Table: etf_fund_daily rows with source='canary'.
"""
from __future__ import annotations

import re

import pandas as pd

from mdh import settings
from mdh.core import db

NAME = "canary"
# a bare "Mozilla/5.0" gets HTTP 403 from the site's firewall (checked 2026-10-07); a full browser UA gets 200
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
HEAD = ["Fund Name", "Fund Ticker", "CUSIP", "Net Assets", "Shares Outstanding", "NAV"]


def _cells(row: str) -> list[str]:
    return [re.sub(r"<[^>]+>|\s+", " ", c).strip() for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)]


def parse(html: str) -> pd.DataFrame:
    """Daily history of all Canary funds from the page's history table (found by its header, not by id)."""
    for t in re.findall(r"<table[^>]*>(.*?)</table>", html, re.S):
        rows = [_cells(r) for r in re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S)]
        rows = [r for r in rows if r]
        if not rows or rows[0][:len(HEAD)] != HEAD or "Rate Date" not in rows[0]:
            continue
        h = rows[0]
        df = pd.DataFrame([r for r in rows[1:] if len(r) == len(h) and r[1] != "Fund Ticker"], columns=h)
        num = lambda s: pd.to_numeric(s.str.replace(r"[$,%]", "", regex=True).replace("", None), errors="coerce")
        return pd.DataFrame({"ticker": df["Fund Ticker"],
                             "date": pd.to_datetime(df["Rate Date"], format="%m/%d/%Y").dt.date,
                             "shares_outstanding": num(df["Shares Outstanding"]).astype("Int64"),
                             "nav_per_share": num(df["NAV"]), "market_price": num(df["Market Price"]),
                             "aum_usd": num(df["Net Assets"])}).drop_duplicates(["ticker", "date"], keep="last")
    raise ValueError("canary: history table not found")


def run(ctx, full=False):
    n = 0
    for asset, fs in settings.ISSUER_ETFS.items():
        for f in fs:
            if f["issuer"] != "canary":
                continue
            d = parse(ctx.http.get(f["url"], headers={"User-Agent": UA}).text)
            d = d[d["ticker"] == f["ticker"]].dropna(subset=["shares_outstanding", "nav_per_share"])
            if d.empty:
                raise ValueError(f"canary: no rows for {f['ticker']}")
            n += db.upsert(ctx.con, "etf_fund_daily", d.assign(asset=asset, source="canary"), ["ticker", "date"])
    return {"etf_fund_daily": n}
