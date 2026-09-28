"""FRED (St. Louis Fed): yields, dollar, liquidity, equities indices, VIX. Free key required."""
import os
from datetime import timedelta

import pandas as pd

from mdh import settings
from mdh.core import db

NAME = "fred"
TABLE = "fred_series"
URL = "https://api.stlouisfed.org/fred/series/observations"
REVISION_WINDOW_DAYS = 45   # re-pull recent window so revised values get corrected


def run(ctx, full=False):
    key = os.environ.get("FRED_API_KEY")
    if not key:
        raise RuntimeError("FRED_API_KEY missing from .env")
    total = 0
    for sid in settings.FRED_SERIES:
        start = "1900-01-01"
        if not full:
            last = db.max_value(ctx.con, TABLE, "date", "series_id = ?", [sid])
            if last is not None:
                start = (pd.Timestamp(last) - timedelta(days=REVISION_WINDOW_DAYS)).strftime("%Y-%m-%d")
        j = ctx.http.get_json(URL, params={"series_id": sid, "api_key": key, "file_type": "json",
                                           "observation_start": start})
        obs = j.get("observations", [])
        if not obs:
            continue
        df = pd.DataFrame(obs)[["date", "value"]]
        df["value"] = pd.to_numeric(df["value"], errors="coerce")   # "." = missing
        df = df.dropna()
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df.insert(0, "series_id", sid)
        total += db.upsert(ctx.con, TABLE, df, ["series_id", "date"])
    return {TABLE: total}
