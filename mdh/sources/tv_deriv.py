"""
Perp open interest by venue + funding, from TradingView symbols (no VPN). Added 2026-10-23.

Why: Binance futures refuses US IPs; the binance_1h table only updates in the hourly VPN step. Avalon watches
TradingView's "加密货币未平仓合约 · Agg" (multi-venue OI) and wants that live AND its history for backtests
("Tradingview 上的实时和历史数据backfill我都要"). TradingView carries each venue's OI as a symbol:
  BINANCE / BYBIT / BITGET  <SYM>USDT.P_OI  -> unit coins     (SHIB/PEPE: 1000<SYM>USDT.P_OI, unit = 1000 coins)
  OKX <SYM>USDT.P_OI, HYPERLIQUID <SYM>USDC.P_OI -> unit USD  (HL matched our deriv_oi: $3.73M vs $3.69M, DASH 10/23)
  funding: BINANCE / BYBIT <SYM>USDT.P_FR (rate per funding interval)
oi_usd = coins x Binance perp close of the same bar (BINANCE:<SYM>USDT.P); USD venues are stored as-is.
History depth (free tvdatafeed, 5000 bars max, checked 10/23): 1h ~7 months, 4h ~2.3 years, 1d since 2021-08.
TradingView's own Agg indicator covers more venues than these 5, so compare changes, not levels.
TIME CONVENTION (verified 10/23 vs our binance_1h from Binance's own API, 380 hours): a TradingView bar stamped H
covers H..H+1; oi_open = OI at H (= binance_1h at H: BTC median diff 0.03%, hourly-change corr 0.90; ZEC 0.25% offset,
corr 0.72), oi_raw = close = OI at the END of the bar (H+1). For the forming bar, oi_raw = latest value.
Tables: tv_oi (venue, symbol, interval, ts, unit, oi_raw, px, oi_usd), tv_funding (venue, symbol, interval, ts, rate).
Backfill (5000 bars of 1h/4h/1d, ~55 s per coin) is spread over runs: BACKFILL_PER_RUN coins per run, progress in
data/state/tv_deriv_backfill.json (~10 runs for the whole universe). --full redoes every coin in one go (slow).
Coins already backfilled get an incremental update every run: 1h last 12 bars; 4h/1d last 20 bars when their latest
stored bar is older than a day.
"""
import json
import os
import time
from pathlib import Path

import pandas as pd

from mdh import settings
from mdh.core import db

NAME = "tv_deriv"
OI_TABLE, FR_TABLE = "tv_oi", "tv_funding"
PAUSE_S = 0.15
BACKFILL_PER_RUN = 4
WORKERS = 4
STATE = Path(__file__).resolve().parents[2] / "data/state/tv_deriv_backfill.json"
OI_VENUES = [("BINANCE", "{t}USDT.P_OI", "coin"), ("BYBIT", "{t}USDT.P_OI", "coin"), ("BITGET", "{t}USDT.P_OI", "coin"),
             ("OKX", "{t}USDT.P_OI", "usd"), ("HYPERLIQUID", "{t}USDC.P_OI", "usd")]
FR_VENUES = ["BINANCE", "BYBIT"]
THOUSAND = {"SHIB", "PEPE"}          # Binance-style 1000x contracts


def coins():
    no_perp = set(getattr(settings, "NO_PERP", []))
    return [c for c in dict.fromkeys(["BTC"] + list(settings.BINANCE_UNIVERSE)) if c not in no_perp]


def run(ctx, full=False):
    try:
        from tvDatafeed import Interval, TvDatafeed
    except ImportError as e:
        raise RuntimeError("tvdatafeed not installed: pip install git+https://github.com/rongardF/tvdatafeed.git") from e
    import logging
    logging.getLogger("tvDatafeed.main").setLevel(logging.CRITICAL)
    os.environ["TZ"] = "UTC"          # tvdatafeed converts epoch -> local time; force UTC
    time.tzset()
    IV = {"1h": Interval.in_1_hour, "4h": Interval.in_4_hour, "1d": Interval.in_daily}
    done = set(json.loads(STATE.read_text())) if STATE.exists() and not full else set()
    todo = [c for c in coins() if c not in done]
    backfill = todo if full else todo[:BACKFILL_PER_RUN]
    now = pd.Timestamp.utcnow().tz_localize(None)
    have = db.table_exists(ctx.con, OI_TABLE)
    jobs = [(c, iv, 5000) for c in backfill for iv in IV]           # (coin, interval, n_bars)
    for iv in IV:
        if iv == "1h":
            n = 12
        else:
            last = db.max_value(ctx.con, OI_TABLE, "ts", "interval = ?", [iv]) if have else None
            n = 20 if last is None or now - pd.Timestamp(last) > pd.Timedelta(hours=24) else 0
        if n:
            jobs += [(c, iv, n) for c in coins() if c in done]
    import threading
    from concurrent.futures import ThreadPoolExecutor
    local = threading.local()

    def get(sym, ex, iv, n):
        if not hasattr(local, "tv"):
            local.tv = TvDatafeed()          # one websocket client per worker thread
        for attempt in range(3):
            try:
                df = local.tv.get_hist(symbol=sym, exchange=ex, interval=IV[iv], n_bars=n)
                time.sleep(PAUSE_S)
                if df is not None and not df.empty:
                    return df
            except Exception:  # noqa: BLE001 - library raises bare exceptions
                pass
            if n <= 50 and attempt == 1:
                return None                   # incremental: venue simply doesn't list this coin
            time.sleep(1 + attempt)
        return None

    def fetch(job):
        c, iv, n = job
        t = ("1000" + c) if c in THOUSAND else c
        px = get(f"{t}USDT.P", "BINANCE", iv, n)
        pxs = px["close"] if px is not None else None
        rows, frs = [], []
        for ex, pat, unit in OI_VENUES:
            df = get(pat.format(t=t if ex != "HYPERLIQUID" else ("k" + c if c in THOUSAND else c)), ex, iv, n)
            if df is None:
                continue
            d = pd.DataFrame({"ts": pd.to_datetime(df.index), "oi_raw": df["close"].values, "oi_open": df["open"].values})
            d["px"] = pxs.reindex(df.index).values if pxs is not None else None
            d["oi_usd"] = d.oi_raw * d.px if unit == "coin" else d.oi_raw
            d["venue"], d["symbol"], d["interval"] = ex, c, iv
            d["unit"] = "1000coins" if unit == "coin" and c in THOUSAND else unit
            rows.append(d)
        for ex in FR_VENUES:
            df = get(f"{t}USDT.P_FR", ex, iv, n)
            if df is not None:
                frs.append(pd.DataFrame({"venue": ex, "symbol": c, "interval": iv, "ts": pd.to_datetime(df.index),
                                         "rate": df["close"].values}))
        return job, rows, frs

    n_oi = n_fr = 0
    missing = []
    got = {}                                   # (coin, interval) -> Binance rows fetched in this backfill
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for (c, iv, n), rows, frs in pool.map(fetch, jobs):
            if not rows:
                missing.append(f"{c}/{iv}")
            else:
                out = pd.concat(rows, ignore_index=True)
                n_oi += db.upsert(ctx.con, OI_TABLE, out[["venue", "symbol", "interval", "ts", "unit", "oi_open", "oi_raw", "px", "oi_usd"]],
                                  ["venue", "symbol", "interval", "ts"])
                if n == 5000:
                    got[(c, iv)] = int((out.venue == "BINANCE").sum())
            if frs:
                n_fr += db.upsert(ctx.con, FR_TABLE, pd.concat(frs, ignore_index=True), ["venue", "symbol", "interval", "ts"])
    if backfill:
        # backfilled only if some daily OI came back, and when Binance lists it, its 1h history too (>= 1000 bars)
        ok = {c for c in backfill if (c, "1d") in got and (got[(c, "1d")] == 0 or got.get((c, "1h"), 0) >= 1000)}
        bad = [c for c in backfill if c not in ok]
        STATE.write_text(json.dumps(sorted(done | ok)))
        ctx.log.info("tv_deriv: backfilled %s (%d of %d coins done)%s", ",".join(sorted(ok)), len(done | ok), len(coins()),
                     f"; RETRY next run: {','.join(bad)}" if bad else "")
    if missing:
        ctx.log.warning("tv_deriv: no OI on any venue for %s", ", ".join(missing[:20]))
    return {OI_TABLE: n_oi, FR_TABLE: n_fr}
