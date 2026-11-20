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
symbol = BTC, ETH, ... (settings.BINANCE_UNIVERSE = coin pages + the top-50 list). Taker-buy volume is what CVD
is built from. Perps listed with a 1000x contract (1000SHIBUSDT, settings.PERP_SYMBOL) are stored per coin: prices
divided by the multiplier, base volumes and OI multiplied by it. Coins in settings.NO_PERP get spot only.
Coins are fetched in parallel (ASSET_WORKERS), archive files with a small thread pool inside each; every database
read and write stays on the main thread. Parquet files dropped into data/backfill/binance_hist/
(<table>__<anything>.parquet) are upserted at the start of a run and renamed *.loaded (bulk backfills built in the
cloud, see claude/how-this-works.md).
"""
from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from mdh import settings
from mdh.core import db
from mdh.core.http import HttpError

NAME = "binance_hist"
SPOT_URL = "https://data-api.binance.vision/api/v3/klines"
ARCHIVE = "https://data.binance.vision/data"
LISTING = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
WORKERS = 16
ASSET_WORKERS = 4
KCOLS = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume",
         "trades", "taker_buy_volume", "taker_buy_quote", "ignore"]
MCOLS = ["create_time", "symbol", "sum_open_interest", "sum_open_interest_value",
         "count_toptrader_long_short_ratio", "sum_toptrader_long_short_ratio",
         "count_long_short_ratio", "sum_taker_long_short_vol_ratio"]
KTABLE = {"1d": "bn_kline_1d", "1h": "bn_kline_1h"}
KEYS = {"bn_kline_1d": ["market", "symbol", "ts"], "bn_kline_1h": ["market", "symbol", "ts"],
        "bn_metrics_1h_hist": ["symbol", "ts"], "deriv_funding": ["venue", "symbol", "ts"],
        "cex_spot": ["venue", "symbol", "interval", "ts"]}     # cex_spot: backfill drop only (coinbase / hyperliquid)
BACKFILL_DIR = settings.DATA_DIR / "backfill" / "binance_hist"


def _perp_sym(asset: str) -> tuple[str, int]:
    """(Binance USDT-M symbol, units per contract quote) for a coin, e.g. SHIB -> ('1000SHIBUSDT', 1000)."""
    return tuple(settings.PERP_SYMBOL.get(asset, (f"{asset}USDT", 1)))


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


def _klines_frame(df: pd.DataFrame, market: str, asset: str, mult: int = 1) -> pd.DataFrame:
    t = pd.to_numeric(df["open_time"]).astype("int64")
    t = t.where(t < 10**14, t // 1000)          # some 2025+ archive files are in microseconds
    out = pd.DataFrame({"market": market, "symbol": asset, "ts": pd.to_datetime(t.values, unit="ms")})
    for c in ["open", "high", "low", "close", "volume", "quote_volume", "taker_buy_volume", "taker_buy_quote"]:
        out[c] = pd.to_numeric(df[c].values, errors="coerce")
    if mult != 1:                               # 1000SHIBUSDT: price per 1000 coins -> per coin
        for c in ["open", "high", "low", "close"]:
            out[c] = out[c] / mult
        for c in ["volume", "taker_buy_volume"]:
            out[c] = out[c] * mult
    out["trades"] = pd.to_numeric(df["trades"].values, errors="coerce").astype("int64")
    return out


# ---------------------------------------------------------------- fetchers (worker threads; no database access)
def _spot(ctx, asset: str, interval: str, last) -> pd.DataFrame | None:
    step = 86_400_000 if interval == "1d" else 3_600_000
    first = pd.Timestamp("2017-01-01") if interval == "1d" else pd.Timestamp(settings.COIN_HOURLY_START)
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
        return None
    df = pd.DataFrame([r[:12] for r in rows], columns=KCOLS).astype(str)
    return _klines_frame(df, "spot", asset)


def _perp(ctx, asset: str, interval: str, last) -> pd.DataFrame | None:
    sym, mult = _perp_sym(asset)
    today = _today()
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
        return None
    return _klines_frame(pd.concat(frames, ignore_index=True), "perp", asset, mult)


def _metrics_frame(df: pd.DataFrame, asset: str, mult: int = 1) -> pd.DataFrame:
    ts = pd.to_datetime(df["create_time"])
    keep = (ts.dt.minute == 0).values
    out = pd.DataFrame({"symbol": asset, "ts": ts.values[keep]})
    for c in MCOLS[2:]:
        out[c] = pd.to_numeric(df[c].values[keep], errors="coerce")
    if mult != 1:
        out["sum_open_interest"] = out["sum_open_interest"] * mult
    return out


def _metrics(ctx, asset: str, last, have: set, full: bool) -> pd.DataFrame | None:
    sym, mult = _perp_sym(asset)
    yesterday = _today() - pd.Timedelta(days=1)
    if last is not None and not full and last.normalize() >= yesterday:
        return None
    if last is None or full or len(have) < 30:
        keys = _list_keys(ctx, f"data/futures/um/daily/metrics/{sym}/")
        days = [pd.Timestamp(k.rsplit("-metrics-", 1)[1][:10]) for k in keys]
    else:
        days = list(pd.date_range(last.normalize() + pd.Timedelta(days=1), yesterday, freq="D"))
    days = [d for d in days if d <= yesterday and d.date() not in have]
    urls = [f"{ARCHIVE}/futures/um/daily/metrics/{sym}/{sym}-metrics-{d:%Y-%m-%d}.zip" for d in days]
    frames = _fetch_many(ctx, urls, MCOLS)
    if not frames:
        return None
    return pd.concat([_metrics_frame(f, asset, mult) for f in frames], ignore_index=True)


def _funding_seed(ctx, asset: str) -> pd.DataFrame | None:
    """One-time funding history from the monthly archive for coins that binance_funding (VPN) hasn't filled
    yet. Same table and keys as binance_funding, which then keeps it current."""
    sym, _ = _perp_sym(asset)
    urls = [f"https://data.binance.vision/{k}" for k in _list_keys(ctx, f"data/futures/um/monthly/fundingRate/{sym}/")]
    frames = _fetch_many(ctx, urls, ["calc_time", "interval_hours", "rate"])
    if not frames:
        return None
    df = pd.concat(frames, ignore_index=True)
    t = pd.to_numeric(df["calc_time"]).astype("int64")
    return pd.DataFrame({"venue": "binance", "symbol": asset,
                         "ts": pd.to_datetime(t.values, unit="ms").floor("min"),
                         "funding_rate": pd.to_numeric(df["rate"]).values,
                         "interval_hours": pd.to_numeric(df["interval_hours"]).astype(float).values})


# ---------------------------------------------------------------- main thread: state, backfill drop, writes
def _state(con, asset: str, full: bool) -> dict:
    st = {"perp": asset not in settings.NO_PERP}
    for interval, table in KTABLE.items():
        for market in ("spot", "perp"):
            st[(market, interval)] = None if full else _last(con, table, "market=? AND symbol=?", [market, asset])
    st["metrics_last"] = None if full else _last(con, "bn_metrics_1h_hist", "symbol=?", [asset])
    st["metrics_have"] = set()
    if st["metrics_last"] is not None:
        st["metrics_have"] = {d for (d,) in con.execute(
            "SELECT DISTINCT CAST(ts AS DATE) FROM bn_metrics_1h_hist WHERE symbol=?", [asset]).fetchall()}
    st["funding_seeded"] = db.table_exists(con, "deriv_funding") and bool(con.execute(
        "SELECT count(*) FROM deriv_funding WHERE venue='binance' AND symbol=?", [asset]).fetchone()[0])
    return st


def _job(ctx, asset: str, st: dict, full: bool) -> list[tuple[str, pd.DataFrame | None]]:
    out = []
    for interval, table in KTABLE.items():
        out.append((table, _spot(ctx, asset, interval, st[("spot", interval)])))
        if st["perp"]:
            out.append((table, _perp(ctx, asset, interval, st[("perp", interval)])))
    if st["perp"]:
        out.append(("bn_metrics_1h_hist", _metrics(ctx, asset, st["metrics_last"], st["metrics_have"], full)))
        if not st["funding_seeded"]:
            out.append(("deriv_funding", _funding_seed(ctx, asset)))
    return out


def load_backfill(ctx, res: dict) -> None:
    """Also called at the start of the coinbase and hyperliquid sources, which run before binance_hist."""
    if not BACKFILL_DIR.exists():
        return
    for f in sorted(BACKFILL_DIR.glob("*.parquet")):
        table = f.name.split("__", 1)[0]
        if table not in KEYS:
            continue
        df = ctx.con.execute("SELECT * FROM read_parquet(?)", [f.as_posix()]).df()
        res[table] = res.get(table, 0) + db.upsert(ctx.con, table, df, KEYS[table])
        f.rename(f.with_name(f.name + ".loaded"))
        ctx.log.info("binance_hist: loaded backfill %s (%d rows)", f.name, len(df))


def run(ctx, full=False, assets=None):
    res = {t: 0 for t in KEYS if t != "cex_spot"}
    load_backfill(ctx, res)
    assets = list(assets or settings.BINANCE_UNIVERSE)
    states = {a: _state(ctx.con, a, full) for a in assets}
    failed = []
    with ThreadPoolExecutor(max_workers=ASSET_WORKERS) as pool:
        futs = {pool.submit(_job, ctx, a, states[a], full): a for a in assets}
        for fut in as_completed(futs):
            a = futs[fut]
            try:
                parts = fut.result()
            except Exception as e:  # noqa: BLE001 - one coin failing must not stop the others
                failed.append(a)
                ctx.log.warning("binance_hist %s failed: %s", a, e)
                continue
            for table, df in parts:
                if df is not None and len(df):
                    res[table] += db.upsert(ctx.con, table, df, KEYS[table])
    if failed:   # shows up in the run summary line; details are in the warnings above
        res["failed_coins"] = len(failed)
    return res
