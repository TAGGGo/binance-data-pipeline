"""Daily OHLCV for US stocks / ETFs from api.nasdaq.com (free, no key)."""
from datetime import date, timedelta

import pandas as pd

from mdh import settings
from mdh.core import db

NAME = "nasdaq"
TABLE = "equity_daily"
URL = "https://api.nasdaq.com/api/quote/{t}/historical"


def _num(s):
    return pd.to_numeric(s.astype(str).str.replace(r"[$,]", "", regex=True).replace({"N/A": None, "": None}),
                         errors="coerce")


def run(ctx, full=False):
    total = 0
    today = date.today()
    for ticker, cls in settings.EQUITIES.items():
        start = settings.EQUITY_START
        if not full:
            last = db.max_value(ctx.con, TABLE, "date", "ticker = ?", [ticker])
            if last is not None:
                start = (pd.Timestamp(last) - timedelta(days=7)).strftime("%Y-%m-%d")
        j = ctx.http.get_json(URL.format(t=ticker), params={
            "assetclass": cls, "fromdate": start, "todate": today.isoformat(), "limit": 9999})
        rows = (((j or {}).get("data") or {}).get("tradesTable") or {}).get("rows") or []
        if not rows:
            ctx.log.warning("nasdaq: no rows for %s (%s)", ticker, (j or {}).get("status"))
            continue
        df = pd.DataFrame(rows)
        out = pd.DataFrame({
            "ticker": ticker,
            "date": pd.to_datetime(df["date"], format="%m/%d/%Y").dt.date,
            "open": _num(df["open"]), "high": _num(df["high"]), "low": _num(df["low"]),
            "close": _num(df["close"]), "volume": _num(df["volume"]),
        }).dropna(subset=["close"])
        total += db.upsert(ctx.con, TABLE, out, ["ticker", "date"])
    return {TABLE: total}
