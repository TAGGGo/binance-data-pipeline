"""
Cross-exchange data: spot candles (Coinbase, Binance), open interest and funding
(OKX, Bybit, Hyperliquid) and CME positioning (CFTC Traders in Financial Futures).

Tables
  cex_spot      (venue, symbol, interval, ts, open, high, low, close, volume)      symbol = BTC, ETH, SOL, XRP, USDT
  deriv_oi      (venue, symbol, interval, ts, oi_usd, volume_usd)
  deriv_funding (venue, symbol, ts, funding_rate, interval_hours)                  raw rate per funding interval
  cftc_crypto   (report_date, market, asset, units_per_contract, open_interest, ...positions by trader group)

Notes (tested 2026-09-28)
  * Coinbase: full candle history (BTC-USD from 2015-07), 300 candles per call, no key.
  * OKX: open interest history is short (last 180 days daily / 30 days hourly); funding ~3 months.
  * Bybit: refuses US IPs (403). It runs in the VPN step of scripts/run_update.sh together with Binance.
  * Hyperliquid: no open-interest history API, so we snapshot it every run; funding history from 2023.
  * CFTC: weekly (as of Tuesday, published Friday).
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pandas as pd

from mdh import settings
from mdh.core import db

ASSETS = settings.CEX_ASSETS
NOW = lambda: datetime.now(timezone.utc).replace(tzinfo=None)  # noqa: E731


def _last_ts(con, table, where, params):
    v = db.max_value(con, table, "ts", where, params)
    return pd.Timestamp(v) if v is not None else None


# ============================================================================ Coinbase spot
class coinbase:
    NAME = "coinbase"
    URL = "https://api.exchange.coinbase.com/products/{p}/candles"
    GRAN = {"1h": 3600, "1d": 86400}

    @staticmethod
    def run(ctx, full=False):
        total = 0
        for asset in ASSETS + ["USDT"]:
            for interval, gran in coinbase.GRAN.items():
                start = pd.Timestamp(settings.CEX_SPOT_START[interval])
                last = None if full else _last_ts(ctx.con, "cex_spot", "venue='coinbase' AND symbol=? AND interval=?", [asset, interval])
                if last is not None:
                    start = max(start, last - pd.Timedelta(seconds=3 * gran))
                end_all = pd.Timestamp(NOW())
                rows, cur = [], start
                while cur < end_all:
                    nxt = min(cur + pd.Timedelta(seconds=gran * 299), end_all)
                    data = ctx.http.get_json(coinbase.URL.format(p=f"{asset}-USD"), params={
                        "granularity": gran, "start": cur.isoformat() + "Z", "end": nxt.isoformat() + "Z"})
                    if isinstance(data, list):
                        rows += data
                    cur = nxt
                if not rows:
                    continue
                df = pd.DataFrame(rows, columns=["t", "low", "high", "open", "close", "volume"])
                out = pd.DataFrame({"venue": "coinbase", "symbol": asset, "interval": interval,
                                    "ts": pd.to_datetime(df["t"], unit="s"), "open": df["open"], "high": df["high"],
                                    "low": df["low"], "close": df["close"], "volume": df["volume"]})
                total += db.upsert(ctx.con, "cex_spot", out, ["venue", "symbol", "interval", "ts"])
        return {"cex_spot": total}


# ============================================================================ Binance spot, daily (long history for the daily Coinbase premium)
class binance_spot:
    NAME = "binance_spot"
    URL = "https://data-api.binance.vision/api/v3/klines"

    @staticmethod
    def run(ctx, full=False):
        total = 0
        for asset in ASSETS:
            last = None if full else _last_ts(ctx.con, "cex_spot", "venue='binance' AND symbol=? AND interval='1d'", [asset])
            start_ms = int(((last - pd.Timedelta(days=3)) if last is not None else pd.Timestamp("2017-01-01")).timestamp() * 1000)
            rows = []
            while True:
                data = ctx.http.get_json(binance_spot.URL, params={"symbol": f"{asset}USDT", "interval": "1d",
                                                                   "startTime": start_ms, "limit": 1000})
                if not data:
                    break
                rows += data
                if len(data) < 1000:
                    break
                start_ms = data[-1][0] + 86_400_000
            if not rows:
                continue
            df = pd.DataFrame([r[:6] for r in rows], columns=["t", "open", "high", "low", "close", "volume"]).astype(float)
            out = pd.DataFrame({"venue": "binance", "symbol": asset, "interval": "1d", "ts": pd.to_datetime(df["t"], unit="ms"),
                                "open": df["open"], "high": df["high"], "low": df["low"], "close": df["close"], "volume": df["volume"]})
            total += db.upsert(ctx.con, "cex_spot", out, ["venue", "symbol", "interval", "ts"])
        return {"cex_spot": total}


# ============================================================================ OKX
class okx:
    NAME = "okx"
    OI = "https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-volume"
    FUND = "https://www.okx.com/api/v5/public/funding-rate-history"

    @staticmethod
    def run(ctx, full=False):
        oi_n = fund_n = 0
        for asset in ASSETS:
            for interval, period in (("1d", "1D"), ("1h", "1H")):
                j = ctx.http.get_json(okx.OI, params={"ccy": asset, "period": period})
                data = j.get("data") or []
                if not data:
                    continue
                df = pd.DataFrame(data, columns=["t", "oi", "vol"])
                out = pd.DataFrame({"venue": "okx", "symbol": asset, "interval": interval,
                                    "ts": pd.to_datetime(df["t"].astype("int64"), unit="ms"),
                                    "oi_usd": df["oi"].astype(float), "volume_usd": df["vol"].astype(float)})
                oi_n += db.upsert(ctx.con, "deriv_oi", out, ["venue", "symbol", "interval", "ts"])
            # funding: newest first, page backwards with `after`
            last = None if full else _last_ts(ctx.con, "deriv_funding", "venue='okx' AND symbol=?", [asset])
            rows, after = [], None
            for _ in range(20):
                params = {"instId": f"{asset}-USDT-SWAP", "limit": 100}
                if after:
                    params["after"] = after
                data = ctx.http.get_json(okx.FUND, params=params).get("data") or []
                if not data:
                    break
                rows += data
                after = data[-1]["fundingTime"]
                if last is not None and pd.to_datetime(int(after), unit="ms") <= last:
                    break
            if rows:
                df = pd.DataFrame(rows)
                out = pd.DataFrame({"venue": "okx", "symbol": asset, "ts": pd.to_datetime(df["fundingTime"].astype("int64"), unit="ms"),
                                    "funding_rate": pd.to_numeric(df["realizedRate"].replace("", None).fillna(df["fundingRate"]), errors="coerce"),
                                    "interval_hours": 8.0})
                fund_n += db.upsert(ctx.con, "deriv_funding", out, ["venue", "symbol", "ts"])
        return {"deriv_oi": oi_n, "deriv_funding": fund_n}


# ============================================================================ Bybit (needs a non-US connection)
class bybit:
    NAME = "bybit"
    BASE = "https://api.bybit.com/v5/market"

    @staticmethod
    def _kline_close(ctx, symbol, interval, start_ms):
        """{ts_ms: close} for converting OI (coins) to USD."""
        out, end = {}, None
        for _ in range(10):
            params = {"category": "linear", "symbol": symbol, "interval": interval, "limit": 1000, "start": start_ms}
            if end:
                params["end"] = end
            lst = (ctx.http.get_json(f"{bybit.BASE}/kline", params=params).get("result") or {}).get("list") or []
            if not lst:
                break
            for r in lst:
                out[int(r[0])] = float(r[4])
            oldest = min(int(r[0]) for r in lst)
            if oldest <= start_ms or len(lst) < 1000:
                break
            end = oldest - 1
        return out

    @staticmethod
    def run(ctx, full=False):
        oi_n = fund_n = 0
        for asset in ASSETS:
            sym = f"{asset}USDT"
            # open interest: walk explicit time windows backwards from now (200 points per window);
            # cursor paging returned the oldest pages first, so windows keep it deterministic
            for interval, bi, kl, step_ms, max_win in (("1d", "1d", "D", 86_400_000, 12), ("1h", "1h", "60", 3_600_000, 1 if not full else 10)):
                last = None if full else _last_ts(ctx.con, "deriv_oi", "venue='bybit' AND symbol=? AND interval=?", [asset, interval])
                rows, end = [], int(time.time() * 1000)
                for _ in range(max_win):
                    start = end - 199 * step_ms
                    res = ctx.http.get_json(f"{bybit.BASE}/open-interest", params={
                        "category": "linear", "symbol": sym, "intervalTime": bi, "limit": 200,
                        "startTime": start, "endTime": end}).get("result") or {}
                    lst = res.get("list") or []
                    if not lst:
                        break
                    rows += lst
                    if last is not None and pd.to_datetime(start, unit="ms") <= last:
                        break
                    end = start - 1
                if not rows:
                    continue
                df = pd.DataFrame(rows).drop_duplicates("timestamp")
                df["t"] = df["timestamp"].astype("int64")
                closes = bybit._kline_close(ctx, sym, kl, int(df["t"].min()))
                px = df["t"].map(closes)
                out = pd.DataFrame({"venue": "bybit", "symbol": asset, "interval": interval,
                                    "ts": pd.to_datetime(df["t"], unit="ms"),
                                    "oi_usd": df["openInterest"].astype(float) * px, "volume_usd": None}).dropna(subset=["oi_usd"])
                oi_n += db.upsert(ctx.con, "deriv_oi", out, ["venue", "symbol", "interval", "ts"])
            # funding history, newest first; page back with endTime
            last = None if full else _last_ts(ctx.con, "deriv_funding", "venue='bybit' AND symbol=?", [asset])
            rows, end = [], None
            for _ in range(30):
                params = {"category": "linear", "symbol": sym, "limit": 200}
                if end:
                    params["endTime"] = end
                lst = (ctx.http.get_json(f"{bybit.BASE}/funding/history", params=params).get("result") or {}).get("list") or []
                if not lst:
                    break
                rows += lst
                oldest = min(int(r["fundingRateTimestamp"]) for r in lst)
                end = oldest - 1
                if len(lst) < 200 or (last is not None and pd.to_datetime(oldest, unit="ms") <= last):
                    break
            if rows:
                df = pd.DataFrame(rows).sort_values("fundingRateTimestamp")
                t = df["fundingRateTimestamp"].astype("int64")
                gap_h = (t.diff().median() or 28_800_000) / 3_600_000
                out = pd.DataFrame({"venue": "bybit", "symbol": asset, "ts": pd.to_datetime(t, unit="ms"),
                                    "funding_rate": df["fundingRate"].astype(float), "interval_hours": float(round(gap_h, 2))})
                fund_n += db.upsert(ctx.con, "deriv_funding", out, ["venue", "symbol", "ts"])
        return {"deriv_oi": oi_n, "deriv_funding": fund_n}


# ============================================================================ Hyperliquid
class hyperliquid:
    NAME = "hyperliquid"
    URL = "https://api.hyperliquid.xyz/info"

    @staticmethod
    def run(ctx, full=False):
        # 1) open-interest snapshot (no history API: we build it hour by hour)
        meta, ctxs = ctx.http.post(hyperliquid.URL, json={"type": "metaAndAssetCtxs"}).json()
        names = [u["name"] for u in meta["universe"]]
        now_h = pd.Timestamp(NOW()).floor("h")
        snap = []
        for asset in ASSETS:
            if asset in names:
                c = ctxs[names.index(asset)]
                mark = float(c["markPx"])
                snap.append({"venue": "hyperliquid", "symbol": asset, "interval": "1h", "ts": now_h,
                             "oi_usd": float(c["openInterest"]) * mark, "volume_usd": float(c.get("dayNtlVlm") or 0)})
        oi_n = db.upsert(ctx.con, "deriv_oi", pd.DataFrame(snap), ["venue", "symbol", "interval", "ts"]) if snap else 0
        # 2) funding history (hourly), paging forward from the last stored point
        fund_n = 0
        end_ms = int(time.time() * 1000)
        for asset in ASSETS:
            last = None if full else _last_ts(ctx.con, "deriv_funding", "venue='hyperliquid' AND symbol=?", [asset])
            start = int(((last + pd.Timedelta(minutes=1)) if last is not None else pd.Timestamp(settings.HL_FUNDING_START)).timestamp() * 1000)
            rows = []
            for _ in range(200):
                data = ctx.http.post(hyperliquid.URL, json={"type": "fundingHistory", "coin": asset, "startTime": start}).json()
                if not data:
                    break
                rows += data
                newest = max(int(r["time"]) for r in data)
                if newest >= end_ms - 3_600_000 or len(data) < 500:
                    break
                start = newest + 1
            if rows:
                df = pd.DataFrame(rows)
                out = pd.DataFrame({"venue": "hyperliquid", "symbol": asset,
                                    "ts": pd.to_datetime(df["time"].astype("int64"), unit="ms").dt.floor("min"),
                                    "funding_rate": df["fundingRate"].astype(float), "interval_hours": 1.0})
                fund_n += db.upsert(ctx.con, "deriv_funding", out, ["venue", "symbol", "ts"])
        return {"deriv_oi": oi_n, "deriv_funding": fund_n}


# ============================================================================ CFTC: CME crypto futures positioning (weekly)
class cftc:
    NAME = "cftc"
    URL = "https://publicreporting.cftc.gov/resource/gpe5-46if.json"
    MARKETS = {  # market_and_exchange_names: (asset, units per contract)
        "BITCOIN - CHICAGO MERCANTILE EXCHANGE": ("BTC", 5),
        "MICRO BITCOIN - CHICAGO MERCANTILE EXCHANGE": ("BTC", 0.1),
        "ETHER CASH SETTLED - CHICAGO MERCANTILE EXCHANGE": ("ETH", 50),
        "MICRO ETHER  - CHICAGO MERCANTILE EXCHANGE": ("ETH", 0.1),
        "SOL - CHICAGO MERCANTILE EXCHANGE": ("SOL", 500),
        "MICRO SOL - CHICAGO MERCANTILE EXCHANGE": ("SOL", 25),
        "XRP - CHICAGO MERCANTILE EXCHANGE": ("XRP", 50000),
        "MICRO XRP - CHICAGO MERCANTILE EXCHANGE": ("XRP", 2500),
    }
    COLS = {"open_interest_all": "open_interest",
            "asset_mgr_positions_long": "am_long", "asset_mgr_positions_short": "am_short",
            "lev_money_positions_long": "lev_long", "lev_money_positions_short": "lev_short",
            "dealer_positions_long_all": "dealer_long", "dealer_positions_short_all": "dealer_short",
            "other_rept_positions_long": "other_long", "other_rept_positions_short": "other_short",
            "nonrept_positions_long_all": "nonrept_long", "nonrept_positions_short_all": "nonrept_short"}

    @staticmethod
    def run(ctx, full=False):
        last = None if full else db.max_value(ctx.con, "cftc_crypto", "report_date")
        since = (pd.Timestamp(last) - pd.Timedelta(days=21)).strftime("%Y-%m-%d") if last else "2017-01-01"
        names = ",".join("'" + m.replace("'", "''") + "'" for m in cftc.MARKETS)
        rows = ctx.http.get_json(cftc.URL, params={
            "$where": f"market_and_exchange_names in ({names}) AND report_date_as_yyyy_mm_dd >= '{since}'",
            "$limit": 50000, "$order": "report_date_as_yyyy_mm_dd"})
        if not rows:
            return {"cftc_crypto": 0}
        df = pd.DataFrame(rows)
        out = pd.DataFrame({"report_date": pd.to_datetime(df["report_date_as_yyyy_mm_dd"]).dt.date,
                            "market": df["market_and_exchange_names"]})
        out["asset"] = out["market"].map(lambda m: cftc.MARKETS[m][0])
        out["units_per_contract"] = out["market"].map(lambda m: cftc.MARKETS[m][1])
        for src, dst in cftc.COLS.items():
            out[dst] = pd.to_numeric(df.get(src), errors="coerce")
        return {"cftc_crypto": db.upsert(ctx.con, "cftc_crypto", out, ["report_date", "market"])}


# ============================================================================ Binance funding (actual 8h rates; needs a non-US connection)
class binance_funding:
    NAME = "binance_funding"
    URL = "https://fapi.binance.com/fapi/v1/fundingRate"

    @staticmethod
    def run(ctx, full=False):
        import os
        url = os.environ.get("BINANCE_FAPI", "https://fapi.binance.com") + "/fapi/v1/fundingRate"
        total = 0
        for asset in ASSETS:
            last = None if full else _last_ts(ctx.con, "deriv_funding", "venue='binance' AND symbol=?", [asset])
            start_ms = int(((last - pd.Timedelta(hours=8)) if last is not None else pd.Timestamp("2019-09-01")).timestamp() * 1000)
            rows = []
            for _ in range(20):
                data = ctx.http.get_json(url, params={"symbol": f"{asset}USDT", "startTime": start_ms, "limit": 1000})
                if not data:
                    break
                rows += data
                if len(data) < 1000:
                    break
                start_ms = int(data[-1]["fundingTime"]) + 1
            if rows:
                df = pd.DataFrame(rows)
                t = df["fundingTime"].astype("int64")
                out = pd.DataFrame({"venue": "binance", "symbol": asset, "ts": pd.to_datetime(t, unit="ms").dt.floor("min"),
                                    "funding_rate": df["fundingRate"].astype(float), "interval_hours": 8.0})
                total += db.upsert(ctx.con, "deriv_funding", out, ["venue", "symbol", "ts"])
        return {"deriv_funding": total}
