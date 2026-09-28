"""
TradingView bars via the unofficial `tvdatafeed` library (no login).
Gives full daily history for CRYPTOCAP:TOTAL / TOTAL3 / BTC.D / USDT.D ... plus DXY and US10Y.
Hourly bars only reach back ~2 months, so we keep accumulating them on every run.
"""
import time

import pandas as pd

from mdh import settings
from mdh.core import db

NAME = "tradingview"
TABLE = "tv_bars"
PAUSE_S = 1.5   # websocket-based; one symbol at a time with a pause


def run(ctx, full=False):
    try:
        from tvDatafeed import Interval, TvDatafeed
    except ImportError as e:
        raise RuntimeError("tvdatafeed not installed: pip install git+https://github.com/rongardF/tvdatafeed.git") from e
    import logging
    import os
    logging.getLogger("tvDatafeed.main").setLevel(logging.ERROR)
    # tvdatafeed converts epoch -> *local* time; force UTC so daily bars land on the right date
    os.environ["TZ"] = "UTC"
    time.tzset()
    iv = {"1d": Interval.in_daily, "1h": Interval.in_1_hour, "4h": Interval.in_4_hour}
    tv = TvDatafeed()
    total, failed = 0, []
    for interval in settings.TV_INTERVALS:
        n_bars = 5000 if full else (60 if interval == "1d" else 400)
        for exch, sym in settings.TV_SYMBOLS:
            df = None
            for attempt in range(3):
                try:
                    df = tv.get_hist(symbol=sym, exchange=exch, interval=iv[interval], n_bars=n_bars)
                    if df is not None and len(df):
                        break
                except Exception as e:  # noqa: BLE001 - library raises bare exceptions
                    ctx.log.warning("tradingview %s:%s %s attempt %d: %s", exch, sym, interval, attempt + 1, e)
                time.sleep(PAUSE_S * (attempt + 2))
            time.sleep(PAUSE_S)
            if df is None or not len(df):
                failed.append(f"{exch}:{sym}/{interval}")
                continue
            out = df.reset_index().rename(columns={"datetime": "ts"})
            out = pd.DataFrame({
                "symbol": f"{exch}:{sym}", "interval": interval, "ts": pd.to_datetime(out["ts"]),
                "open": out["open"], "high": out["high"], "low": out["low"], "close": out["close"],
                "volume": out.get("volume"),
            })
            if interval == "1d":
                # TVC daily sessions open the previous evening UTC (e.g. DXY bar for Sep 25 is stamped
                # Sep 24 23:00); CRYPTOCAP bars are stamped 00:00. +12h then floor gives the trading date.
                out["ts"] = (out["ts"] + pd.Timedelta(hours=12)).dt.normalize()
            total += db.upsert(ctx.con, TABLE, out, ["symbol", "interval", "ts"])
    if failed:
        ctx.log.warning("tradingview: failed %s", failed)
    return {TABLE: total}
