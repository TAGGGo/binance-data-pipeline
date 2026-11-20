"""
5-minute open interest by venue, in COINS (plus USD where the venue gives it).

Why: hourly OI misses wicks. To tell "new shorts opened" from "longs closed / liquidated" you need 5-15m OI in
coins (USD OI falls just because price falls). Added 2026-10-08 (see claude/orderflow-options.md).

No extra schedule is needed: every venue serves recent 5m history, so the hourly run back-fills the last hour's
twelve 5m bars (and anything missed while the Mac was off, within each venue's window).

  venue     history window (5m)   network            source name
  okx       ~5 days               any                oi5m_okx   (normal step)
  binance   30 days               needs VPN (451)    oi5m       (VPN step, with bybit)
  bybit     ~30 days?             needs VPN (403)    oi5m

Table
  oi_5m (venue, symbol, contract, ts, oi_coin, oi_usd)
    contract = 'linear' (USDT-margined) | 'inverse' (coin-margined, OKX only). ts = bar open, UTC.
    oi_usd is NULL for Bybit (use v_oi_5m, which prices it with the Binance bar).
Size: ~4 coins x 4 series x 288/day = ~4.6k rows/day (~1.7M rows/yr, a few tens of MB).
"""
from __future__ import annotations

import time

import pandas as pd

from mdh import settings
from mdh.core import db
from mdh.sources.binance.parser import FAPI

STEP = 300_000  # 5 min in ms
OKX = "https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-history"
BYBIT = "https://api.bybit.com/v5/market/open-interest"


def _last_ms(con, venue, sym, contract):
    v = db.max_value(con, "oi_5m", "ts", "venue=? AND symbol=? AND contract=?", [venue, sym, contract]) \
        if db.table_exists(con, "oi_5m") else None
    return int(pd.Timestamp(v).timestamp() * 1000) if v is not None else None


def _frame(venue, sym, contract, ts_ms, coin, usd=None):
    return pd.DataFrame({"venue": venue, "symbol": sym, "contract": contract,
                         "ts": pd.to_datetime(pd.Series(ts_ms, dtype="int64"), unit="ms"),
                         "oi_coin": pd.Series(coin, dtype="float64"),
                         "oi_usd": pd.Series(usd if usd is not None else [None] * len(ts_ms), dtype="float64")})


class oi5m_okx:
    NAME = "oi5m_okx"

    @staticmethod
    def run(ctx, full=False):
        n = 0
        for sym in settings.OI5M_ASSETS:
            for contract, inst in (("linear", f"{sym}-USDT-SWAP"), ("inverse", f"{sym}-USD-SWAP")):
                last = None if full else _last_ms(ctx.con, "okx", sym, contract)
                rows, end = [], None
                for _ in range(16):                      # 100 bars per page, ~5 days max
                    params = {"instId": inst, "period": "5m", "limit": 100}
                    if end:
                        params["end"] = end
                    try:
                        data = ctx.http.get_json(OKX, params=params).get("data") or []
                    except Exception as e:  # noqa: BLE001 - e.g. no inverse swap for this coin
                        ctx.log.warning("oi5m okx %s: %s", inst, str(e)[:150])
                        break
                    if not data:
                        break
                    rows += data
                    end = data[-1][0]
                    if last is not None and int(end) <= last:
                        break
                if rows:   # [ts, oi (contracts), oiCcy (coins), oiUsd]
                    n += db.upsert(ctx.con, "oi_5m", _frame("okx", sym, contract, [int(r[0]) for r in rows],
                                                            [float(r[2]) for r in rows], [float(r[3]) for r in rows]),
                                   ["venue", "symbol", "contract", "ts"])
        return {"oi_5m": n}


class oi5m:
    """Binance + Bybit 5m OI. Both refuse US IPs: runs in the VPN step of run_update.sh."""
    NAME = "oi5m"

    @staticmethod
    def _binance_page(ctx, sym, start, end):
        """openInterestHist between start and end (ms), in explicit 500-bar windows. With startTime alone Binance
        returns the LATEST 500 bars, so the first run (2026-10-08 09:21) only got ~42h instead of 29 days."""
        rows, win = [], 500 * STEP
        while start < end:
            stop = min(start + win - 1, end)
            data = ctx.http.get_json(f"{FAPI}/futures/data/openInterestHist", params={
                "symbol": f"{sym}USDT", "period": "5m", "limit": 500, "startTime": start, "endTime": stop})
            rows += data or []
            start = stop + 1
        return rows

    @staticmethod
    def _binance(ctx, sym, full):
        last = None if full else _last_ms(ctx.con, "binance", sym, "linear")
        now = int(time.time() * 1000)
        floor = now - 29 * 86_400_000                                      # API keeps the last 30 days
        rows = oi5m._binance_page(ctx, sym, max(last - 2 * STEP if last else 0, floor), now)
        # one-time history fill below the earliest stored bar (marker file so it isn't retried every hour)
        mark = settings.DATA_DIR / "state" / f"oi5m_binance_backfill_{sym}.done"
        first = ctx.con.execute("SELECT epoch_ms(min(ts)) FROM oi_5m WHERE venue='binance' AND symbol=? AND contract='linear'",
                                [sym]).fetchone()[0] if db.table_exists(ctx.con, "oi_5m") else None
        if first is not None and not mark.exists():
            first = int(first)
            if first > floor + 86_400_000:
                rows += oi5m._binance_page(ctx, sym, floor, first)
            mark.parent.mkdir(parents=True, exist_ok=True)
            mark.write_text(time.strftime("%Y-%m-%d %H:%M:%S"))
        if not rows:
            return 0
        return db.upsert(ctx.con, "oi_5m", _frame("binance", sym, "linear", [int(r["timestamp"]) for r in rows],
                                                  [float(r["sumOpenInterest"]) for r in rows],
                                                  [float(r["sumOpenInterestValue"]) for r in rows]),
                         ["venue", "symbol", "contract", "ts"])

    @staticmethod
    def _bybit_tv_fallback(ctx, sym, full):
        """Fallback via TradingView BYBIT:<SYM>USDT.P_OI 5m bars when api.bybit.com returns 403 (US IP, no VPN)."""
        import logging
        import os
        from tvDatafeed import Interval, TvDatafeed
        logging.getLogger("tvDatafeed.main").setLevel(logging.CRITICAL)
        os.environ["TZ"] = "UTC"
        time.tzset()
        tv = TvDatafeed()
        df = tv.get_hist(symbol=f"{sym}USDT.P_OI", exchange="BYBIT", interval=Interval.in_5_minute,
                         n_bars=1000 if full else 60)
        if df is None or len(df) < 2:
            return 0
        done = df.iloc[:-1]
        ts_ms = [int((pd.Timestamp(t) + pd.Timedelta(minutes=5)).timestamp() * 1000) for t in done.index]
        return db.upsert(ctx.con, "oi_5m", _frame("bybit", sym, "linear", ts_ms,
                                                  [float(v) for v in done["close"].values]),
                         ["venue", "symbol", "contract", "ts"])

    @staticmethod
    def _bybit(ctx, sym, full):
        last = None if full else _last_ms(ctx.con, "bybit", sym, "linear")
        rows, end = [], int(time.time() * 1000)
        try:
            for _ in range(45 if last is None else 4):       # 200 bars (~16.7h) per window
                start = end - 199 * STEP
                res = ctx.http.get_json(BYBIT, params={"category": "linear", "symbol": f"{sym}USDT", "intervalTime": "5min",
                                                       "limit": 200, "startTime": start, "endTime": end}).get("result") or {}
                lst = res.get("list") or []
                if not lst:
                    break
                rows += lst
                oldest = min(int(r["timestamp"]) for r in lst)
                if last is not None and oldest <= last:
                    break
                end = oldest - 1
        except Exception as e:
            if "403" in str(e):
                return oi5m._bybit_tv_fallback(ctx, sym, full)
            raise
        if not rows:
            return 0
        return db.upsert(ctx.con, "oi_5m", _frame("bybit", sym, "linear", [int(r["timestamp"]) for r in rows],
                                                  [float(r["openInterest"]) for r in rows]),
                         ["venue", "symbol", "contract", "ts"])

    @staticmethod
    def run(ctx, full=False):
        n = 0
        for sym in settings.OI5M_ASSETS:
            for fn in (oi5m._binance, oi5m._bybit):
                try:
                    n += fn(ctx, sym, full)
                except Exception as e:  # noqa: BLE001 - one venue failing (no VPN) must not block the other
                    ctx.log.warning("oi5m %s %s: %s", fn.__name__, sym, str(e)[:200])
        return {"oi_5m": n}
