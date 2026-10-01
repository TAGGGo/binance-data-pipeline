"""
Binance history for the coin pages (no VPN needed: data-api.binance.vision + the data.binance.vision archive).

  bn_kline_1d        (market, symbol, ts, open, high, low, close, volume, quote_volume,
                      taker_buy_volume, taker_buy_quote, trades)       market = 'spot' | 'perp', since listing
  bn_kline_1h        same columns, hourly, since settings.COIN_HOURLY_START (feeds the 4h candles)
  bn_metrics_1h_hist (symbol, ts, sum_open_interest, sum_open_interest_value,
                      count_toptrader_long_short_ratio, sum_toptrader_long_short_ratio,
                      count_long_short_ratio, sum_taker_long_short_vol_ratio)
                     hourly snapshots (minute 00) from the daily metrics archive (first file .. yesterday)
  deriv_funding      one-time seed from the monthly funding archive for coins binance_funding hasn't filled

Spot comes from the REST API and is current to the hour. Perp klines and metrics come from the archive, which
publishes each day on the next day; today's perp/OI for the chart comes from binance_1h (VPN step).
symbol = BTC, ETH, ... (settings.COIN_PAGES). Taker-buy volume is what CVD is built from.
Archive files are fetched with a small thread pool; all database writes stay on the main thread.
"""
from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from mdh import settings
from mdh.core import db
from mdh.core.http import HttpError

NAME = "binance_hist"
SPOT_URL = "https://data-api.binance.vision/api/v3/klines"
ARCHIVE = "https://data.binance.vision/data"
LISTING = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
WORKERS = 16
KCOLS = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume",
         "trades", "taker_buy_volume", "taker_buy_quote", "ignore"]
MCOLS = ["create_time", "symbol", "sum_open_interest", "sum_open_interest_value",
         "count_toptrader_long_short_ratio", "sum_toptrader_long_short_ratio",
         "count_long_short_ratio", "sum_taker_long_short_vol_ratio"]
KTABLE = {"1d": "bn_kline_1d", "1h": "bn_kline_1h"}


def _today():
    return pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()


def _last(con, table, where, params):
    v = db.max_value(con, table, "ts", where, params)
    return None if v is None else pd.Timestamp(v)


def _list_keys(ctx, prefix: str) -> list[str]:
    keys, marker = [], ""
    ns = "{http://s3.amazonaws.com/doc/2006-03-01/}"
    while True:
        r = ctx.http.get(LISTING, params={"prefix": prefix, "marker": marker, "max-keys": 1000})
        root = ET.fromstring(r.content)
        page = [e.text for e in root.iter(f"{ns}Key")]
        keys += [k for k in page if k.endswith(".zip")]
        if (root.findtext(f"{ns}IsTruncated") or "false") != "true" or not page:
            return keys
        marker = page[-1]


def _zip_csv(ctx, url: str, cols: list[str]) -> pd.DataFrame | None:
    try:
        r = ctx.http.get(url)
    except HttpError as e:
        if e.status == 404:
            return None
        raise
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        raw = z.read(z.namelist()[0]).decode()
    df = pd.read_csv(io.StringIO(raw), header=None, names=cols, dtype=str)
    return df[df[cols[0]] != cols[0]]            # newer files carry a header row, older ones don't


def _fetch_many(ctx, urls: list[str], cols: list[str]) -> list[pd.DataFrame]:
    if not urls:
        return []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        return [f for f in pool.map(lambda u: _zip_csv(ctx, u, cols), urls) if f is not None and len(f)]


def _klines_frame(df: pd.DataFrame, market: str, asset: str) -> pd.DataFrame:
    t = pd.to_numeric(df["open_time"]).astype("int64")
    t = t.where(t < 10**14, t // 1000)          # some 2025+ archive files are in microseconds
    out = pd.DataFrame({"market": market, "symbol": asset, "ts": pd.to_datetime(t.values, unit="ms")})
    for c in ["open", "high", "low", "close", "volume", "quote_volume", "taker_buy_volume", "taker_buy_quote"]:
        out[c] = pd.to_numeric(df[c].values, errors="coerce")
    out["trades"] = pd.to_numeric(df["trades"].values, errors="coerce").astype("int64")
    return out


def _spot(ctx, asset: str, interval: str, full: bool) -> int:
    table = KTABLE[interval]
    step = 86_400_000 if interval == "1d" else 3_600_000
    first = pd.Timestamp("2017-01-01") if interval == "1d" else pd.Timestamp(settings.COIN_HOURLY_START)
    last = None if full else _last(ctx.con, table, "market='spot' AND symbol=?", [asset])
    start_ms = int(((last - pd.Timedelta(milliseconds=3 * step)) if last is not None else first).timestamp() * 1000)
    rows = []
    while True:
        data = ctx.http.get_json(SPOT_URL, params={"symbol": f"{asset}USDT", "interval": interval,
                                                   "startTime": start_ms, "limit": 1000})
        if not data:
            break
        rows += data
        if len(data) < 1000:
            break
        start_ms = data[-1][0] + step
    if not rows:
        return 0
    df = pd.DataFrame([r[:12] for r in rows], columns=KCOLS).astype(str)
    return db.upsert(ctx.con, table, _klines_frame(df, "spot", asset), ["market", "symbol", "ts"])


def _perp(ctx, asset: str, interval: str, full: bool) -> int:
    sym, table = f"{asset}USDT", KTABLE[interval]
    today = _today()
    last = None if full else _last(ctx.con, table, "market='perp' AND symbol=?", [asset])
    urls = []
    if last is None:   # full history: monthly files, then daily files for the current month
        first = pd.Timestamp("2000-01-01") if interval == "1d" else pd.Timestamp(settings.COIN_HOURLY_START)
        for key in _list_keys(ctx, f"data/futures/um/monthly/klines/{sym}/{interval}/"):
            if pd.Timestamp(key.rsplit(f"-{interval}-", 1)[1][:7] + "-01") >= first.replace(day=1):
                urls.append(f"https://data.binance.vision/{key}")
        start = today.replace(day=1)
    else:
        start = last.normalize() + pd.Timedelta(days=1) if interval == "1d" else last.normalize()
    urls += [f"{ARCHIVE}/futures/um/daily/klines/{sym}/{interval}/{sym}-{interval}-{d:%Y-%m-%d}.zip"
             for d in pd.date_range(start, today - pd.Timedelta(days=1), freq="D")]
    frames = _fetch_many(ctx, urls, KCOLS)
    if not frames:
        return 0
    out = _klines_frame(pd.concat(frames, ignore_index=True), "perp", asset)
    return db.upsert(ctx.con, table, out, ["market", "symbol", "ts"])


def _metrics_frame(df: pd.DataFrame, asset: str) -> pd.DataFrame:
    ts = pd.to_datetime(df["create_time"])
    keep = (ts.dt.minute == 0).values
    out = pd.DataFrame({"symbol": asset, "ts": ts.values[keep]})
    for c in MCOLS[2:]:
        out[c] = pd.to_numeric(df[c].values[keep], errors="coerce")
    return out


def _metrics(ctx, asset: str, full: bool) -> int:
    sym = f"{asset}USDT"
    yesterday = _today() - pd.Timedelta(days=1)
    last = None if full else _last(ctx.con, "bn_metrics_1h_hist", "symbol=?", [asset])
    if last is not None and last.normalize() >= yesterday:
        return 0
    have = set()
    if last is not None:
        have = {d for (d,) in ctx.con.execute(
            "SELECT DISTINCT CAST(ts AS DATE) FROM bn_metrics_1h_hist WHERE symbol=?", [asset]).fetchall()}
    if last is None or full or len(have) < 30:
        keys = _list_keys(ctx, f"data/futures/um/daily/metrics/{sym}/")
        days = [pd.Timestamp(k.rsplit("-metrics-", 1)[1][:10]) for k in keys]
    else:
        days = list(pd.date_range(last.normalize() + pd.Timedelta(days=1), yesterday, freq="D"))
    days = [d for d in days if d <= yesterday and d.date() not in have]
    total = 0
    for i in range(0, len(days), 400):        # write in chunks so a long backfill can resume
        urls = [f"{ARCHIVE}/futures/um/daily/metrics/{sym}/{sym}-metrics-{d:%Y-%m-%d}.zip" for d in days[i:i + 400]]
        frames = _fetch_many(ctx, urls, MCOLS)
        if frames:
            out = pd.concat([_metrics_frame(f, asset) for f in frames], ignore_index=True)
            total += db.upsert(ctx.con, "bn_metrics_1h_hist", out, ["symbol", "ts"])
    return total


def _funding_seed(ctx, asset: str) -> int:
    """One-time funding history from the monthly archive for coins that binance_funding (VPN) hasn't filled
    yet. Same table and keys as binance_funding, which then keeps it current."""
    if db.table_exists(ctx.con, "deriv_funding") and ctx.con.execute(
            "SELECT count(*) FROM deriv_funding WHERE venue='binance' AND symbol=?", [asset]).fetchone()[0]:
        return 0
    sym = f"{asset}USDT"
    urls = [f"https://data.binance.vision/{k}" for k in _list_keys(ctx, f"data/futures/um/monthly/fundingRate/{sym}/")]
    frames = _fetch_many(ctx, urls, ["calc_time", "interval_hours", "rate"])
    if not frames:
        return 0
    df = pd.concat(frames, ignore_index=True)
    t = pd.to_numeric(df["calc_time"]).astype("int64")
    out = pd.DataFrame({"venue": "binance", "symbol": asset,
                        "ts": pd.to_datetime(t.values, unit="ms").floor("min"),
                        "funding_rate": pd.to_numeric(df["rate"]).values,
                        "interval_hours": pd.to_numeric(df["interval_hours"]).astype(float).values})
    return db.upsert(ctx.con, "deriv_funding", out, ["venue", "symbol", "ts"])


def run(ctx, full=False):
    res = {"bn_kline_1d": 0, "bn_kline_1h": 0, "bn_metrics_1h_hist": 0, "deriv_funding": 0}
    for asset in settings.COIN_PAGES:
        for interval in ("1d", "1h"):
            res[KTABLE[interval]] += _spot(ctx, asset, interval, full) + _perp(ctx, asset, interval, full)
        res["bn_metrics_1h_hist"] += _metrics(ctx, asset, full)
        res["deriv_funding"] += _funding_seed(ctx, asset)
    return res
