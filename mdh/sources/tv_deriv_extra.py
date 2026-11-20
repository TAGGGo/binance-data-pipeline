"""
Extra perp OI contracts from TradingView, so our total matches TradingView's "加密货币未平仓合约 · Agg". Added 2026-11-06.

Why (Avalon 11/06: "Tradingview 和你的口径对不上…你自己去调查吧" -> "去拿，去调查"): TradingView's aggregated OI is USD summed over
9 exchanges and over EVERY perp of the coin (USDT, USDC, coin-margined/inverse). tv_deriv.py stores only the USDT perp of
Binance/Bybit/Bitget/OKX + Hyperliquid. For XRP on 11/06 00:00 UTC that was $1.24B vs TV Agg $1.53B.
This module adds, into the SAME table tv_oi (venue names below never clash with tv_deriv's 5):
  venue          TradingView symbol        unit stored   USD          checked
  BINANCE_USDC   BINANCE:<t>USDC.P_OI      coin          x px         verified 11/06 vs Binance fapi (within 0.4%)
  BINANCE_CM     BINANCE:<t>USD.P_OI       contracts     x face ($100 BTC, $10 others)   verified 11/06 vs Binance dapi (contracts and contractSize match within 0.03%)
  BYBIT_USDC     BYBIT:<t>USDC.P_OI        coin          x px         verified 11/06 vs Bybit API (XRP 4.679M vs 4.680M)
  BYBIT_INV      BYBIT:<t>USD.P_OI         usd           as is        verified 11/06 vs Bybit API (XRP 40.79M vs 40.81M USD)
  OKX_CM         OKX:<t>USD.P_OI           usd           as is        XRP 11/06: TV 11.61M vs OKX API oiUsd 11.62M
  BITGET_USDC    BITGET:<t>USDC.P_OI       coin          x px         not checked (Bitget API not tried)
  HTX            HTX:<t>USDT.P_OI          coin          x px         XRP 11/06: TV 21.65M vs HTX API 21.73M coins
  HTX_CM         HTX:<t>USD.P_OI           coin          x px         TV = HTX API 'amount' (BTC 258.2, XRP 166k)
  KRAKEN         KRAKEN:<t>USD.PM_OI       coin          x px         XRP 11/06: TV 16.43M vs Kraken API 16.44M
Verification script: scripts/ops/verify_binance_oi.py (runs on the Mac via the job queue, VPN if needed).
Not available on TradingView as _OI (checked 11/06): Coinbase (XRPUSDC.P, XPP, XRL), Deribit XRP (stale since 2026-10-01).
=> TV Agg "core 5" = tv_deriv venues; "agg" = all rows. ALWAYS sum oi_usd (unit differs by venue: coin / usd / contracts).
Time convention as tv_deriv: ts = bar open, oi_raw = value at bar close, oi_open = value at bar open.
Backfill 5000 bars (1h/4h/1d) BACKFILL_PER_RUN coins per run, BTC ETH XRP SOL ZEC first; state data/state/tv_deriv_extra_backfill.json
= {coin: tries}. A coin is re-tried (all pairs refetched) while any venue/interval pair is missing, up to 3 tries
(TradingView drops some requests under load; after 3 tries a missing pair = contract not listed).
"""
import json
import os
import time
from pathlib import Path

import pandas as pd

from mdh.core import db
from mdh.sources import tv_deriv as base

NAME = "tv_deriv_extra"
OI_TABLE = "tv_oi"
PAUSE_S = 0.15
BACKFILL_PER_RUN = 3
WORKERS = 4
STATE = Path(__file__).resolve().parents[2] / "data/state/tv_deriv_extra_backfill.json"
EXTRA = [("BINANCE_USDC", "BINANCE", "{t}USDC.P_OI", "coin"), ("BINANCE_CM", "BINANCE", "{t}USD.P_OI", "contracts"),
         ("BYBIT_USDC", "BYBIT", "{t}USDC.P_OI", "coin"), ("BYBIT_INV", "BYBIT", "{t}USD.P_OI", "usd"),
         ("OKX_CM", "OKX", "{t}USD.P_OI", "usd"), ("BITGET_USDC", "BITGET", "{t}USDC.P_OI", "coin"),
         ("HTX", "HTX", "{t}USDT.P_OI", "coin"), ("HTX_CM", "HTX", "{t}USD.P_OI", "coin"),
         ("KRAKEN", "KRAKEN", "{t}USD.PM_OI", "coin")]
FIRST = ["BTC", "ETH", "XRP", "SOL", "ZEC"]


def face(c):
    return 100.0 if c == "BTC" else 10.0


def run(ctx, full=False):
    from tvDatafeed import Interval, TvDatafeed
    import logging, threading
    from concurrent.futures import ThreadPoolExecutor
    logging.getLogger("tvDatafeed.main").setLevel(logging.CRITICAL)
    os.environ["TZ"] = "UTC"
    time.tzset()
    IV = {"1h": Interval.in_1_hour, "4h": Interval.in_4_hour, "1d": Interval.in_daily}
    allc = base.coins()
    allc = [c for c in FIRST if c in allc] + [c for c in allc if c not in FIRST]
    st = json.loads(STATE.read_text()) if STATE.exists() and not full else {}
    if isinstance(st, list):                     # old format: list of coins whose first backfill ran once
        st = {c: 1 for c in st}
    have_tbl = db.table_exists(ctx.con, OI_TABLE)
    venues = [v for v, *_ in EXTRA]
    present = set()
    if have_tbl:
        present = {(r[0], r[1], r[2]) for r in ctx.con.execute(
            f"SELECT symbol, venue, interval FROM {OI_TABLE} WHERE venue IN ({','.join('?' * len(venues))}) GROUP BY 1, 2, 3", venues).fetchall()}
    # a coin is retried (all its pairs refetched) while any pair is missing, until 3 tries; after that a missing pair = not listed there
    todo = [c for c in allc if st.get(c, 0) < 3 and (st.get(c, 0) == 0 or any((c, v, iv) not in present for v in venues for iv in IV))]
    backfill = todo if full else todo[:BACKFILL_PER_RUN]
    jobs = [(c, iv, 5000) for c in backfill for iv in IV]
    done = {c for c in allc if c in st}
    now = pd.Timestamp.utcnow().tz_localize(None)
    for iv in IV:
        if iv == "1h":
            n = 12
        else:
            last = db.max_value(ctx.con, OI_TABLE, "ts", "interval = ? AND venue = 'KRAKEN'", [iv]) if db.table_exists(ctx.con, OI_TABLE) else None
            n = 20 if last is None or now - pd.Timestamp(last) > pd.Timedelta(hours=24) else 0
        if n:
            jobs += [(c, iv, n) for c in allc if c in done]
    local = threading.local()

    def get(sym, ex, iv, n):
        if not hasattr(local, "tv"):
            local.tv = TvDatafeed()
        for attempt in range(3):
            try:
                df = local.tv.get_hist(symbol=sym, exchange=ex, interval=IV[iv], n_bars=n)
                time.sleep(PAUSE_S)
                if df is not None and not df.empty:
                    return df
            except Exception:  # noqa: BLE001
                pass
            if attempt == 1 and n <= 50:
                return None            # incremental: most coins are simply not listed on most of these contracts
            time.sleep(1)
        return None

    def fetch(job):
        c, iv, n = job
        t = ("1000" + c) if c in base.THOUSAND else c
        px = get(f"{t}USDT.P", "BINANCE", iv, n)
        pxs = px["close"] if px is not None else None
        rows = []
        for venue, ex, pat, unit in EXTRA:
            df = get(pat.format(t=c), ex, iv, n)       # these contracts use the plain ticker (no 1000x)
            if df is None:
                continue
            d = pd.DataFrame({"ts": pd.to_datetime(df.index), "oi_raw": df["close"].values, "oi_open": df["open"].values})
            d["px"] = pxs.reindex(df.index).values if pxs is not None else None
            if unit == "coin":
                d["oi_usd"] = d.oi_raw * d.px
            elif unit == "usd":
                d["oi_usd"] = d.oi_raw
            else:
                d["oi_usd"] = d.oi_raw * face(c)
            d["venue"], d["symbol"], d["interval"] = venue, c, iv
            d["unit"] = f"contracts{int(face(c))}" if unit == "contracts" else unit
            rows.append(d)
        return job, rows

    n_oi, got = 0, {}
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for (c, iv, n), rows in pool.map(fetch, jobs):
            if rows:
                out = pd.concat(rows, ignore_index=True)
                n_oi += db.upsert(ctx.con, OI_TABLE, out[["venue", "symbol", "interval", "ts", "unit", "oi_open", "oi_raw", "px", "oi_usd"]],
                                  ["venue", "symbol", "interval", "ts"])
            if n == 5000:
                got[(c, iv)] = len(rows)
    if backfill:
        for c in backfill:
            st[c] = st.get(c, 0) + 1
        STATE.write_text(json.dumps(st))
        ctx.log.info("tv_deriv_extra: backfill pass for %s (%d of %d coins tried at least once)", ",".join(backfill), len(st), len(allc))
    return {OI_TABLE: n_oi}
