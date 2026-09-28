"""On-chain / valuation metrics from the Coin Metrics Community API (free, no key)."""
from datetime import timedelta

import pandas as pd

from mdh import settings
from mdh.core import db
from mdh.core.http import HttpError

NAME = "coinmetrics"
TABLE = "onchain_daily"
URL = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"


def _fetch(ctx, asset, metrics, start):
    rows, url, params = [], URL, {"assets": asset, "metrics": ",".join(metrics), "frequency": "1d",
                                  "start_time": start, "page_size": 10000}
    while url:
        j = ctx.http.get_json(url, params=params)
        rows += j.get("data", [])
        url, params = j.get("next_page_url"), None
    return rows


def run(ctx, full=False):
    total = 0
    for asset in settings.COINMETRICS_ASSETS:
        start = "2009-01-01"
        if not full:
            last = db.max_value(ctx.con, TABLE, "date", "asset = ?", [asset.upper()])
            if last is not None:
                start = (pd.Timestamp(last) - timedelta(days=7)).strftime("%Y-%m-%d")
        try:
            rows = _fetch(ctx, asset, settings.COINMETRICS_METRICS, start)
        except HttpError:
            # some metrics aren't in the free tier for every asset: fall back to one metric at a time
            rows = []
            for m in settings.COINMETRICS_METRICS:
                try:
                    rows += _fetch(ctx, asset, [m], start)
                except HttpError as e:
                    ctx.log.info("coinmetrics %s/%s unavailable: %s", asset, m, e.status)
        if not rows:
            continue
        wide = pd.DataFrame(rows)
        long = wide.melt(id_vars=["asset", "time"], var_name="metric", value_name="value")
        long["value"] = pd.to_numeric(long["value"], errors="coerce")
        long = long.dropna(subset=["value"])
        out = pd.DataFrame({"asset": long["asset"].str.upper(),
                            "date": pd.to_datetime(long["time"]).dt.date,
                            "metric": long["metric"], "value": long["value"]})
        total += db.upsert(ctx.con, TABLE, out, ["asset", "date", "metric"])
    return {TABLE: total}
