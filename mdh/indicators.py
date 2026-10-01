"""
Candles and indicators for the coin pages. Pure pandas/numpy; every formula is written out here.

Candles (UTC, Binance's own boundaries)
  4h  00:00 04:00 ... 20:00 UTC        built from hourly klines (bn_kline_1h, since settings.COIN_HOURLY_START)
  1d  00:00 UTC                         Binance 1d klines (bn_kline_1d); days not in it yet (today's perp) from hourly
  1w  Monday 00:00 UTC                  aggregated from 1d (identical to Binance's native 1w)
  1M  1st of the month 00:00 UTC        aggregated from 1d (identical to Binance's native 1M)
  open = first open, high = max, low = min, close = last close, volumes summed.
  The newest candle is usually still forming; it is flagged (`partial`) and signals use the last *closed* candle.

Indicators (on the spot close)
  SMA(n)   mean of the last n closes; empty until n candles exist
  EMA(n)   alpha = 2/(n+1), seeded with the first close (same as TradingView's ta.ema and
           pandas ewm(adjust=False)); shown only after n candles so the seed has washed out
  MACD     EMA12 - EMA26; signal = EMA9 of MACD; histogram = MACD - signal; shown after 26+9 candles
  RSI(14)  Wilder: RMA of gains / RMA of losses (RMA alpha = 1/14, seeded with the SMA of the first 14 changes)

Derivatives (Binance USDT-margined perpetual)
  funding_bp   sum of funding settlements that fall inside the candle (a settlement at 08:00 pays for the
               hours before it, so it belongs to the candle that ends at/after 08:00), in basis points.
               4h candles are shorter than most funding intervals: each takes the settlement that covers it,
               pro-rated to 4 hours.
  funding_apr  average hourly funding rate over those settlements x 24 x 365, in %
  oi / oi_usd  open interest (coins / USD) at the candle's close: last 5-min snapshot at or before close time
  CVD          per-candle delta = taker-buy volume - taker-sell volume = 2 x taker_buy_quote - quote_volume (USD),
               for spot and perp separately; the page cumulates it from the first visible candle.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TF_RULE = {"4h": "4h", "1d": "1D", "1w": "W-MON", "1M": "MS"}
TF_LEN = {"4h": pd.Timedelta(hours=4), "1d": pd.Timedelta(days=1)}
MA_LENGTHS = (20, 30, 50, 100)


# ----------------------------------------------------------------------------- indicator math
def sma(x: pd.Series, n: int) -> pd.Series:
    return x.rolling(n, min_periods=n).mean()


def ema(x: pd.Series, n: int, show_after: int | None = None) -> pd.Series:
    out = x.ewm(alpha=2 / (n + 1), adjust=False).mean()
    first = x.first_valid_index()
    if first is None:
        return out
    k = (show_after if show_after is not None else n) - 1
    start = x.index.get_loc(first) + k
    out.iloc[:start] = np.nan
    return out


def macd(close: pd.Series, fast=12, slow=26, sig=9) -> pd.DataFrame:
    line = close.ewm(alpha=2 / (fast + 1), adjust=False).mean() - close.ewm(alpha=2 / (slow + 1), adjust=False).mean()
    signal = line.ewm(alpha=2 / (sig + 1), adjust=False).mean()
    df = pd.DataFrame({"macd": line, "signal": signal, "hist": line - signal})
    df.iloc[: slow + sig - 1] = np.nan
    return df


def rsi(close: pd.Series, n=14) -> pd.Series:
    d = close.diff()
    gain, loss = d.clip(lower=0).to_numpy(), (-d.clip(upper=0)).to_numpy()
    out = np.full(len(close), np.nan)
    if len(close) <= n:
        return pd.Series(out, index=close.index)
    ag, al = gain[1:n + 1].mean(), loss[1:n + 1].mean()
    for i in range(n, len(close)):
        if i > n:
            ag = (ag * (n - 1) + gain[i]) / n
            al = (al * (n - 1) + loss[i]) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return pd.Series(out, index=close.index)


def add_indicators(c: pd.DataFrame) -> pd.DataFrame:
    c = c.copy()
    for n in MA_LENGTHS:
        c[f"sma{n}"] = sma(c["close"], n)
        c[f"ema{n}"] = ema(c["close"], n)
    c = c.join(macd(c["close"]))
    c["rsi"] = rsi(c["close"])
    return c


# ----------------------------------------------------------------------------- candle building
def _ns(x) -> np.ndarray:
    """Timestamps as datetime64[ns]: pandas 2 and 3 pick different resolutions, and merge_asof needs them equal."""
    return pd.to_datetime(pd.Series(x)).astype("datetime64[ns]").to_numpy()



def _agg(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Resample OHLCV-style columns (index = candle open time)."""
    spec = {}
    for col in df.columns:
        if col.endswith("open"):
            spec[col] = "first"
        elif col.endswith("high"):
            spec[col] = "max"
        elif col.endswith("low"):
            spec[col] = "min"
        elif col.endswith("close"):
            spec[col] = "last"
        else:
            spec[col] = lambda s: s.sum(min_count=1)
    return df.resample(rule, label="left", closed="left").agg(spec)


def _q(con, table, sql, params):
    from mdh.core import db
    return con.execute(sql, params).df() if db.table_exists(con, table) else pd.DataFrame()


def load_inputs(con, asset: str) -> dict:
    """Hourly spot/perp klines (bn_kline_1h, archive + REST) with binance_1h (VPN, live) filling the newest
    hours; daily klines (bn_kline_1d); OI snapshots (metrics archive + binance_1h); Binance funding."""
    sym = f"{asset}USDT"
    k = _q(con, "bn_kline_1h", """SELECT market, ts, open, high, low, close, quote_volume AS qv, taker_buy_quote AS tbq
                                    FROM bn_kline_1h WHERE symbol = ? ORDER BY ts""", [asset])
    h = pd.DataFrame()
    if len(k):
        sp = k[k.market == "spot"].set_index("ts")
        pp = k[k.market == "perp"].set_index("ts")
        h = pd.DataFrame({"spot_open": sp["open"], "spot_high": sp["high"], "spot_low": sp["low"], "spot_close": sp["close"],
                          "spot_qv": sp["qv"], "spot_tbq": sp["tbq"]}).join(
            pd.DataFrame({"perp_close": pp["close"], "perp_qv": pp["qv"], "perp_tbq": pp["tbq"]}), how="outer")
    live = _q(con, "binance_1h", """
        SELECT timestamp AS ts,
               spot_open, spot_high, spot_low, spot_close,
               spot_quote_volume AS spot_qv, spot_taker_buy_quote_volume AS spot_tbq,
               futures_close AS perp_close, futures_quote_volume AS perp_qv,
               futures_taker_buy_quote_volume AS perp_tbq,
               sum_open_interest AS oi, sum_open_interest_value AS oi_usd
        FROM binance_1h WHERE symbol = ? ORDER BY ts""", [sym])
    if len(live):
        live = live.set_index("ts")
        h = live.drop(columns=["oi", "oi_usd"]) if h.empty else h.combine_first(live.drop(columns=["oi", "oi_usd"]))
    d = _q(con, "bn_kline_1d", """
        SELECT market, ts, open, high, low, close, volume, quote_volume AS qv, taker_buy_quote AS tbq
        FROM bn_kline_1d WHERE symbol = ? ORDER BY ts""", [asset])
    m = _q(con, "bn_metrics_1h_hist", """
        SELECT ts, sum_open_interest AS oi, sum_open_interest_value AS oi_usd FROM bn_metrics_1h_hist
        WHERE symbol = ? ORDER BY ts""", [asset])
    parts = []
    if len(m):
        parts.append(m.set_index("ts"))
    if len(live):
        parts.append(live[["oi", "oi_usd"]].dropna())
    snaps = pd.concat(parts) if parts else pd.DataFrame(columns=["oi", "oi_usd"], index=pd.DatetimeIndex([]))
    snaps = snaps[~snaps.index.duplicated(keep="last")].sort_index()
    f = _q(con, "deriv_funding", """
        SELECT ts, funding_rate, interval_hours FROM deriv_funding
        WHERE venue = 'binance' AND symbol = ? ORDER BY ts""", [asset])
    # coins whose Binance spot listing is recent: earlier spot candles from another venue (settings.SPOT_FALLBACK).
    # No taker split there, so spot CVD stays blank for those candles.
    from mdh import settings
    fb = settings.SPOT_FALLBACK.get(asset)
    if fb:
        venue = fb[0]
        x = _q(con, "cex_spot", """SELECT interval, ts, open, high, low, close, volume FROM cex_spot
                                   WHERE venue = ? AND symbol = ? ORDER BY ts""", [venue, asset])
        if len(x):
            x1 = x[x["interval"] == "1h"].set_index("ts")
            if len(x1):
                alt = pd.DataFrame({"spot_open": x1["open"], "spot_high": x1["high"], "spot_low": x1["low"],
                                    "spot_close": x1["close"], "spot_qv": x1["volume"] * x1["close"], "spot_tbq": np.nan})
                if h.empty:
                    h = alt.assign(perp_close=np.nan, perp_qv=np.nan, perp_tbq=np.nan)
                else:
                    first_bn = h["spot_close"].first_valid_index()
                    alt = alt[alt.index < first_bn] if first_bn is not None else alt
                    h = h.combine_first(alt)
            xd = x[x["interval"] == "1d"]
            if len(xd):
                have = set(pd.to_datetime(d.loc[d["market"] == "spot", "ts"])) if len(d) else set()
                first_spot = min(have) if have else None
                xd = xd[[first_spot is None or pd.Timestamp(t) < first_spot for t in xd["ts"]]]
                add = pd.DataFrame({"market": "spot", "ts": xd["ts"], "open": xd["open"], "high": xd["high"],
                                    "low": xd["low"], "close": xd["close"], "volume": xd["volume"],
                                    "qv": xd["volume"] * xd["close"], "tbq": np.nan})
                d = pd.concat([add, d], ignore_index=True) if len(d) else add
    # one timestamp resolution everywhere (pandas 2 and 3 differ in what DuckDB hands back)
    h.index = pd.DatetimeIndex(_ns(h.index), name="ts")
    snaps.index = pd.DatetimeIndex(_ns(snaps.index), name="ts")
    for df in (d, f):
        if len(df):
            df["ts"] = _ns(df["ts"])
    return {"h": h.sort_index(), "d": d, "oi": snaps, "f": f}


def build_candles(inp: dict, tf: str, now: pd.Timestamp | None = None) -> pd.DataFrame:
    h, d = inp["h"], inp["d"]
    now = now or pd.Timestamp.now(tz="UTC").tz_localize(None)
    hourly = pd.DataFrame({
        "open": h["spot_open"], "high": h["spot_high"], "low": h["spot_low"], "close": h["spot_close"],
        "vol": h["spot_qv"], "spot_delta": 2 * h["spot_tbq"] - h["spot_qv"],
        "perp_vol": h["perp_qv"], "perp_delta": 2 * h["perp_tbq"] - h["perp_qv"],
        "perp_close": h["perp_close"],
    }) if len(h) else pd.DataFrame(columns=["open", "high", "low", "close", "vol", "spot_delta", "perp_vol", "perp_delta", "perp_close"],
                                   index=pd.DatetimeIndex([], name="ts"))
    if tf == "4h":
        c = _agg(hourly, TF_RULE["4h"])
    else:
        daily_h = _agg(hourly, "1D")
        spot = d[d["market"] == "spot"].set_index("ts")
        perp = d[d["market"] == "perp"].set_index("ts")
        daily = pd.DataFrame({
            "open": spot["open"], "high": spot["high"], "low": spot["low"], "close": spot["close"],
            "vol": spot["qv"], "spot_delta": 2 * spot["tbq"] - spot["qv"]})
        daily = daily.join(pd.DataFrame({"perp_vol": perp["qv"], "perp_delta": 2 * perp["tbq"] - perp["qv"],
                                         "perp_close": perp["close"]}), how="outer")
        # fill what the daily klines don't have yet (today's forming candle, perp days not archived yet)
        daily = daily.combine_first(daily_h)
        daily = daily.dropna(subset=["close"]).sort_index()
        c = daily if tf == "1d" else _agg(daily, TF_RULE[tf])
    c = c.dropna(subset=["close"])
    c.index.name = "ts"

    # candle close time and whether it is still forming
    if tf in TF_LEN:
        end = c.index + TF_LEN[tf]
    elif tf == "1w":
        end = c.index + pd.Timedelta(days=7)
    else:
        end = c.index + pd.offsets.MonthBegin(1)
    c["end"] = end
    c["partial"] = c["end"] > now

    # open interest at candle close
    snaps = inp["oi"]
    if len(snaps):
        probe = pd.DataFrame({"end": _ns(c["end"])}).sort_values("end")
        # a snapshot stamped exactly at close time belongs to this candle
        right = snaps.reset_index()
        right.columns = ["sts", "oi", "oi_usd"]
        right["sts"] = _ns(right["sts"])
        joined = pd.merge_asof(probe, right,
                               left_on="end", right_on="sts", direction="backward")
        c["oi"] = joined["oi"].values
        c["oi_usd"] = joined["oi_usd"].values
        c.loc[c["end"] <= snaps.index.min(), ["oi", "oi_usd"]] = np.nan
    else:
        c["oi"] = c["oi_usd"] = np.nan

    # funding settled inside each candle (settlement at t pays for the interval ending at t)
    f = inp["f"]
    if len(f):
        fs = f.set_index("ts").sort_index()
        pay_t = fs.index - pd.Timedelta(seconds=1)
        bins = pd.IntervalIndex.from_arrays(c.index, c["end"], closed="left")
        idx = bins.get_indexer(pay_t)
        ok = idx >= 0
        g = pd.DataFrame({"k": idx[ok], "rate": fs["funding_rate"].values[ok],
                          "hourly": (fs["funding_rate"] / fs["interval_hours"]).values[ok]})
        agg = g.groupby("k").agg(rate=("rate", "sum"), hourly=("hourly", "mean"))
        c["funding_bp"] = np.nan
        c["funding_apr"] = np.nan
        c.iloc[agg.index, c.columns.get_loc("funding_bp")] = agg["rate"].values * 1e4
        c.iloc[agg.index, c.columns.get_loc("funding_apr")] = agg["hourly"].values * 24 * 365 * 100
        if tf == "4h":
            # settlements come every 8h (sometimes 4h/1h): each 4h candle takes the settlement whose interval
            # covers it, i.e. the first one at or after the candle's close, pro-rated to 4 hours
            right = fs.reset_index()[["ts", "funding_rate", "interval_hours"]].rename(columns={"ts": "sts"})
            right["sts"] = _ns(right["sts"])
            nxt = pd.merge_asof(pd.DataFrame({"end": _ns(c["end"])}), right,
                                left_on="end", right_on="sts", direction="forward")
            covers = (nxt["sts"] - nxt["end"]) < pd.to_timedelta(nxt["interval_hours"], unit="h")
            hourly_rate = (nxt["funding_rate"] / nxt["interval_hours"]).where(covers)
            c["funding_apr"] = hourly_rate.values * 24 * 365 * 100
            c["funding_bp"] = hourly_rate.values * 4 * 1e4
    else:
        c["funding_bp"] = c["funding_apr"] = np.nan

    return add_indicators(c)


def build_all(con, asset: str, now=None) -> dict[str, pd.DataFrame]:
    inp = load_inputs(con, asset)
    return {tf: build_candles(inp, tf, now) for tf in TF_RULE}


def last_closed(c: pd.DataFrame) -> pd.Series:
    closed = c[~c["partial"]]
    return closed.iloc[-1] if len(closed) else c.iloc[-1]


# ----------------------------------------------------------------------------- relative strength vs BTC
# Daily, closed UTC days only, Binance spot closes.
#   ratio        coin close / BTC close
#   high_1y      the ratio's highest close over the trailing 365 days (including today)
#   last_high    date of the latest close that set a new 365-day high (ratio >= high_1y)
#   dd_pct       ratio vs the ratio at last_high, % (how far it has bled since its last breakout)
#   days_since   calendar days since last_high
#   beta60/r2_60 OLS of daily log returns (coin on BTC) over the last 60 days; r2 = share of the coin's daily
#                variance explained by BTC. alpha60 = sum of the residuals (coin - beta*BTC) over those 60 days, %.
REL_WIN, REL_HIGH_DAYS, REL_MIN_GAP = 60, 365, 90


def vs_btc(coin: pd.Series, btc: pd.Series) -> pd.DataFrame:
    df = pd.concat({"c": coin, "b": btc}, axis=1).dropna()
    df = df[(df["c"] > 0) & (df["b"] > 0)]
    out = pd.DataFrame(index=df.index)
    out["ratio"] = df["c"] / df["b"]
    out["high_1y"] = out["ratio"].rolling(f"{REL_HIGH_DAYS}D", min_periods=1).max()
    is_high = out["ratio"] >= out["high_1y"] - 1e-15
    hi_t = pd.Series(np.where(is_high, out.index, pd.NaT), index=out.index, dtype="datetime64[ns]").ffill()
    hi_v = out["ratio"].where(is_high).ffill()
    out["last_high"] = hi_t
    out["dd_pct"] = (out["ratio"] / hi_v - 1) * 100
    out["days_since"] = (out.index - hi_t).dt.days
    rc, rb = np.log(df["c"]).diff(), np.log(df["b"]).diff()
    cov = rc.rolling(REL_WIN, min_periods=REL_WIN).cov(rb)
    var = rb.rolling(REL_WIN, min_periods=REL_WIN).var()
    beta = cov / var
    out["beta60"] = beta
    out["r2_60"] = rc.rolling(REL_WIN, min_periods=REL_WIN).corr(rb) ** 2
    # alpha over the window with that window's beta: sum(rc) - beta*sum(rb)
    out["alpha60_pct"] = (np.exp(rc.rolling(REL_WIN, min_periods=REL_WIN).sum() - beta * rb.rolling(REL_WIN, min_periods=REL_WIN).sum()) - 1) * 100
    # 50 / 200-day averages of the ratio itself; streak = consecutive closes above (+n) or below (-n) the 200-day line
    out["ma50"] = out["ratio"].rolling(50, min_periods=50).mean()
    out["ma200"] = out["ratio"].rolling(200, min_periods=200).mean()
    above = (out["ratio"] > out["ma200"]).where(out["ma200"].notna())
    run = (above != above.shift()).cumsum()
    n = above.groupby(run).cumcount() + 1
    out["streak200"] = np.where(above.isna(), np.nan, np.where(above == True, n, -n))  # noqa: E712
    return out


def ma200_tries(rel: pd.DataFrame, days: int = 90) -> list[dict]:
    """Every close that crossed back above the ratio's 200-day line within the last `days` days: start date and how many
    closes it stayed above (the last one may still be running)."""
    s = rel["streak200"]
    recent = s[s.index >= s.index[-1] - pd.Timedelta(days=days)]
    out = []
    for t, v in recent.items():
        if v == 1:
            seg = s.loc[t:]
            below = seg[seg < 0]
            end = below.index[0] if len(below) else None
            out.append({"start": t, "days": int(seg.loc[:end].iloc[-2 if end is not None else -1]) if len(seg) else 1,
                        "running": end is None})
    return out


def rel_episodes(rel: pd.DataFrame, min_gap: int = REL_MIN_GAP) -> list[dict]:
    """Past 'bleed then catch up' stretches: from a 365-day ratio high, at least `min_gap` days without a new one,
    ended by a close back at a new 365-day high. Reports the bleed (high -> lowest ratio) and the catch-up
    (lowest -> new high). The stretch still running (if any) is returned last with end=None."""
    r = rel["ratio"]
    highs = rel.index[rel["days_since"] == 0]
    eps = []
    for a, b in zip(highs[:-1], highs[1:]):
        if (b - a).days < min_gap:
            continue
        seg = r.loc[a:b]
        lo = seg.idxmin()
        eps.append({"start": a, "trough": lo, "end": b, "bleed_days": (lo - a).days, "bleed_pct": (r[lo] / r[a] - 1) * 100,
                    "catch_days": (b - lo).days, "catch_pct": (r[b] / r[lo] - 1) * 100, "total_days": (b - a).days})
    if len(highs) and (rel.index[-1] - highs[-1]).days >= min_gap:
        a = highs[-1]; seg = r.loc[a:]; lo = seg.idxmin()
        eps.append({"start": a, "trough": lo, "end": None, "bleed_days": (lo - a).days, "bleed_pct": (r[lo] / r[a] - 1) * 100,
                    "catch_days": (rel.index[-1] - lo).days, "catch_pct": (r.iloc[-1] / r[lo] - 1) * 100,
                    "total_days": (rel.index[-1] - a).days})
    return eps


# ----------------------------------------------------------------------------- rebound signals
# Six checks on closed UTC days, each 2 = green, 1 = amber, 0 = red, NaN = no data. They describe what brought
# ZEC back from its September pullbacks (leverage flushed, shorts crowded, US + Binance spot buying, ETF money,
# price reclaiming its week) and are computed the same way for every coin.
#   flush    OI (coins) at the close vs its 14-day high:           <= 85% green, <= 92% amber
#   shorts   funding, 3-day average per 8h (bp) < 0 green, < 0.5 amber;
#            or top-trader long/short vs its 14-day high <= 85% green, <= 92% amber (the better of the two)
#   us       Coinbase premium, daily average of hourly premiums (bp): >= +3 green, > 0 amber
#   spot     Binance spot CVD (taker buys - sells, USD): last 2 days > 0 green, last day > 0 amber
#   etf      US spot ETF net flow, last 3 sessions: sum > 0 and last > 0 green, sum > 0 amber
#   reclaim  close vs its 7-day average close: above green, within 2% below amber
SIGNALS = ["flush", "shorts", "us", "spot", "etf", "reclaim"]


def _grade(x: pd.Series, green, amber, higher=True) -> pd.Series:
    g = (x >= green) if higher else (x <= green)
    a = (x > amber) if higher else (x <= amber)
    out = np.where(g, 2.0, np.where(a, 1.0, 0.0))
    return pd.Series(np.where(x.isna(), np.nan, out), index=x.index)


def rebound_signals(d1: pd.DataFrame, top_ls: pd.Series | None = None, cb_prem: pd.Series | None = None,
                    etf: pd.Series | None = None) -> pd.DataFrame:
    """d1: closed daily candles (close, oi, funding_bp, spot_delta). Extra daily series are indexed by UTC day."""
    idx = d1.index
    reidx = lambda s: (s.reindex(idx) if s is not None and len(s) else pd.Series(np.nan, index=idx))
    raw = pd.DataFrame(index=idx)
    raw["oi_vs_high"] = d1["oi"] / d1["oi"].rolling(14, min_periods=5).max()
    raw["fund8_3d"] = (d1["funding_bp"] / 3).rolling(3, min_periods=2).mean()
    top = reidx(top_ls)
    raw["top_ls"] = top
    raw["top_vs_high"] = top / top.rolling(14, min_periods=5).max()
    raw["cb_prem_bp"] = reidx(cb_prem)
    raw["cvd"] = d1["spot_delta"]
    e = etf.dropna() if etf is not None else pd.Series(dtype=float)
    if len(e):
        s3 = e.rolling(3, min_periods=1).sum()
        raw["etf_3"] = s3.reindex(idx, method="ffill")
        raw["etf_last"] = e.reindex(idx, method="ffill")
        raw.loc[raw.index < e.index.min(), ["etf_3", "etf_last"]] = np.nan
    else:
        raw["etf_3"] = raw["etf_last"] = np.nan
    raw["close_vs_7d"] = d1["close"] / d1["close"].rolling(7).mean()
    raw["chg7_pct"] = (d1["close"] / d1["close"].shift(7) - 1) * 100

    sig = pd.DataFrame(index=idx)
    sig["flush"] = _grade(raw["oi_vs_high"], 0.85, 0.92, higher=False)
    sig["shorts"] = pd.concat([_grade(raw["fund8_3d"], -1e-9, 0.5, higher=False),
                               _grade(raw["top_vs_high"], 0.85, 0.92, higher=False)], axis=1).max(axis=1)
    sig["us"] = _grade(raw["cb_prem_bp"], 3, 0)
    c = raw["cvd"]
    sig["spot"] = pd.Series(np.where(c.isna(), np.nan, np.where((c > 0) & (c.shift(1) > 0), 2.0, np.where(c > 0, 1.0, 0.0))), index=idx)
    sig["etf"] = pd.Series(np.where(raw["etf_3"].isna(), np.nan,
                                    np.where((raw["etf_3"] > 0) & (raw["etf_last"] > 0), 2.0, np.where(raw["etf_3"] > 0, 1.0, 0.0))), index=idx)
    sig["reclaim"] = _grade(raw["close_vs_7d"], 1.0, 0.98)
    sig["greens"] = (sig[SIGNALS] == 2).sum(axis=1)
    sig["available"] = sig[SIGNALS].notna().sum(axis=1)
    return pd.concat([sig, raw], axis=1)


# ----------------------------------------------------------------------------- Fibonacci retracement + trend channel
FIB_RATIOS = (0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0, 1.618)
FIB_WINDOW = 90          # days searched for the latest swing extreme
FIB_ANCHOR = 60          # days before that extreme searched for the swing's starting point


def fib_levels(d1: pd.DataFrame, window: int = FIB_WINDOW, anchor: int = FIB_ANCHOR) -> dict | None:
    """Retracement levels of the latest daily swing (live frame, today's forming candle included).

    Swing: the most recent of {highest high, lowest low} over the last `window` days is the swing's end.
      end = high -> up-swing; start = lowest low in the `anchor` days before it.  level(r) = H - r * (H - L)
      end = low  -> down-swing; start = highest high in the `anchor` days before it. level(r) = L + r * (H - L)
    So 0 is the swing's end, 1 its start, 1.618 the extension beyond the start (same as TradingView's
    Fib retracement tool drawn from start to end). Picking swings is a judgement call; this rule is fixed so
    it is repeatable, and the page shows which two candles it used.

    Channel: least-squares line through log(close) from the swing start to today, +/- 2 standard deviations of the
    residuals (TradingView "Regression Trend" style, in log space so a 3x move isn't distorted)."""
    d = d1.dropna(subset=["high", "low", "close"])
    if len(d) < 30:
        return None
    w = d.iloc[-window:]
    ih, il = w["high"].idxmax(), w["low"].idxmin()
    up = ih >= il
    end_t = ih if up else il
    pre = d.loc[:end_t].iloc[-(anchor + 1):]
    start_t = pre["low"].idxmin() if up else pre["high"].idxmax()
    H = float(d.at[ih if up else start_t, "high"])
    L = float(d.at[start_t if up else il, "low"])
    if not H > L:
        return None
    rng = H - L
    lv = [{"r": r, "p": (H - r * rng) if up else (L + r * rng)} for r in FIB_RATIOS]
    px = float(d["close"].iloc[-1])
    below = [x for x in lv if x["p"] <= px]
    above = [x for x in lv if x["p"] > px]
    sup = max(below, key=lambda x: x["p"]) if below else None
    res = min(above, key=lambda x: x["p"]) if above else None
    retr = ((H - px) / rng) if up else ((px - L) / rng)     # how much of the swing has been given back
    # regression channel on log closes from the swing start to today
    seg = d.loc[start_t:, "close"]
    x = np.arange(len(seg), dtype=float)
    y = np.log(seg.to_numpy(dtype=float))
    ch = None
    if len(seg) >= 10:
        b, a = np.polyfit(x, y, 1)
        resid = y - (a + b * x)
        sd = float(resid.std(ddof=1))
        mid = a + b * x
        ch = {"t0": seg.index[0], "t1": seg.index[-1], "n": len(seg),
              "mid": np.exp(mid), "upper": np.exp(mid + 2 * sd), "lower": np.exp(mid - 2 * sd),
              "slope_pct_day": (np.exp(b) - 1) * 100, "sd_pct": (np.exp(2 * sd) - 1) * 100,
              "pos": float((y[-1] - (mid[-1] - 2 * sd)) / (4 * sd)) if sd > 0 else None}
    return {"dir": "up" if up else "down", "high": (ih if up else start_t, H), "low": (start_t if up else il, L),
            "start": start_t, "end": end_t, "levels": lv, "price": px, "retrace": retr,
            "support": sup, "resist": res, "channel": ch}
