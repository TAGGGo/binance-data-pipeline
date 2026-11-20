"""US Treasury Daily Treasury Statement: TGA (Treasury General Account) closing balance, daily.

Added 2026-10-29: FRED WTREGEN is weekly (Wednesdays) and lags about a week; the DTS is published
each business day ~4 PM ET for the prior business day. No key needed.
Units: $ millions (same as WTREGEN). Two row formats in the API:
  - later:         account_type 'Treasury General Account (TGA) Closing Balance', value in open_today_bal
  - 2021-10 .. split: account_type 'Treasury General Account (TGA)', value in close_today_bal
  - before 2021-10:  account_type 'Federal Reserve Account', value in close_today_bal
"""
from datetime import timedelta

import pandas as pd

from mdh.core import db

NAME = "treasury_dts"
TABLE = "treasury_tga_daily"
URL = ("https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/"
       "accounting/dts/operating_cash_balance")
FIRST = "2005-10-01"
REVISION_WINDOW_DAYS = 14


def _value(r):
    at = r.get("account_type") or ""
    if "TGA" in at and "Closing Balance" in at:
        return r.get("open_today_bal")
    if at in ("Federal Reserve Account", "Treasury General Account (TGA)"):
        return r.get("close_today_bal")
    return None


def run(ctx, full=False):
    start = FIRST
    if not full:
        last = db.max_value(ctx.con, TABLE, "date") if db.table_exists(ctx.con, TABLE) else None
        if last is not None:
            start = (pd.Timestamp(last) - timedelta(days=REVISION_WINDOW_DAYS)).strftime("%Y-%m-%d")
    rows, page = [], 1
    while True:
        j = ctx.http.get_json(URL, params={
            "filter": f"record_date:gte:{start}",
            "fields": "record_date,account_type,open_today_bal,close_today_bal",
            "sort": "record_date", "page[size]": 10000, "page[number]": page})
        data = j.get("data", [])
        for r in data:
            v = _value(r)
            if v not in (None, "null", ""):
                rows.append((r["record_date"], float(v)))
        pages = (j.get("meta") or {}).get("total-pages", 1)
        if page >= pages or not data:
            break
        page += 1
    if not rows:
        return {TABLE: 0}
    df = pd.DataFrame(rows, columns=["date", "tga_musd"])
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.groupby("date", as_index=False)["tga_musd"].last()
    return {TABLE: db.upsert(ctx.con, TABLE, df, ["date"])}
