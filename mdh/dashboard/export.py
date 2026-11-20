"""
Export a compact JSON snapshot of the database for the dashboard page.

    python3 -m mdh export            -> data/dashboard/data.json

The page (mdh/dashboard/page.html) only reads this file, so refreshing the dashboard is:
`python3 -m mdh update && python3 -m mdh export`, then republish data.json and coins/*.json.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone

from mdh import settings
from mdh.core import db

ASSETS = ["BTC", "ETH", "SOL", "XRP"]


def _clean(v, nd=6):
    if v is None:
        return None
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
        return round(v, nd)
    return v


def _cols(rows, names, nd=None):
    """rows -> {name: [..]} with dates as ISO strings and floats rounded."""
    out = {n: [] for n in names}
    for r in rows:
        for n, v in zip(names, r):
            if hasattr(v, "isoformat"):
                v = v.isoformat()[:16].replace("T", " ") if hasattr(v, "hour") else v.isoformat()
            out[n].append(_clean(v, (nd or {}).get(n, 6)))
    return out


def q(con, sql, names, nd=None):
    return _cols(con.execute(sql).fetchall(), names, nd)


def build(con) -> dict:
    d: dict = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}

    # ------------------------------------------------------------ ETF flows (daily, per asset)
    etf = {}
    for a in ASSETS + [x for x in list(settings.GRAYSCALE_ETFS) + list(settings.ISSUER_ETFS) if x not in ASSETS]:
        etf[a] = q(con, f"""SELECT date, net_inflow_usd/1e6, net_assets_usd/1e9, cum_net_inflow_usd/1e9, value_traded_usd/1e6
                            FROM v_etf_flows WHERE asset='{a}' ORDER BY date""",
                   ["date", "flow_m", "aum_b", "cum_b", "traded_m"], {"flow_m": 2, "aum_b": 3, "cum_b": 3, "traded_m": 1})
    d["etf"] = {a: v for a, v in etf.items() if v["date"] or a in ASSETS}
    # per-fund (BTC/ETH, Farside): last 90 trading days
    funds = {}
    for a in ("BTC", "ETH"):
        rows = con.execute(f"""SELECT date, ticker, net_flow_usd/1e6 FROM etf_flows_by_fund
                               WHERE asset='{a}' AND date >= (SELECT max(date) FROM etf_flows_by_fund WHERE asset='{a}') - 130
                               ORDER BY date""").fetchall()
        tickers = sorted({r[1] for r in rows})
        dates = sorted({r[0].isoformat() for r in rows})
        grid = {t: {} for t in tickers}
        for dt, t, v in rows:
            grid[t][dt.isoformat()] = _clean(v, 2)
        funds[a] = {"dates": dates, "tickers": {t: [grid[t].get(x) for x in dates] for t in tickers}}
    d["etf_funds"] = funds

    # ------------------------------------------------------------ market structure + macro (daily)
    d["macro"] = q(con, """SELECT date, btc_usd, eth_usd, total_mcap/1e9, total3_mcap/1e9, btc_d, btc_dom_ex_stables,
                                  stablecoin_mcap/1e9, us10y, us2y, dxy, spx, vix, net_liquidity_tn, fear_greed, btc_mvrv
                           FROM v_macro_daily WHERE date >= DATE '2017-01-01' ORDER BY date""",
                   ["date", "btc", "eth", "total_b", "total3_b", "btc_d", "btc_d_ex", "stables_b", "us10y", "us2y",
                    "dxy", "spx", "vix", "netliq_t", "fg", "mvrv"],
                   {"btc": 1, "eth": 2, "total_b": 1, "total3_b": 1, "btc_d": 2, "btc_d_ex": 2, "stables_b": 1,
                    "us10y": 3, "us2y": 3, "dxy": 3, "spx": 1, "vix": 2, "netliq_t": 3, "mvrv": 3})
    # ------------------------------------------------------------ US stocks (added 2026-10-17)
    # CNN Fear & Greed = US STOCK sentiment (the `fg` column above is alternative.me CRYPTO F&G).
    us = {}
    if db.table_exists(con, "cnn_fear_greed"):
        us["cnn_fg"] = q(con, """SELECT date, score, rating, spx, spx_ma125, net_new_highs_pct, mcclellan_vol_sum,
                                        put_call_5d, vix, vix_ma50, junk_spread, stock_bond_20d
                                 FROM cnn_fear_greed ORDER BY date""",
                         ["date", "score", "rating", "spx", "spx_ma125", "nnh_pct", "mcclellan", "pc5d", "vix",
                          "vix50", "junk", "stock_bond"],
                         {"score": 1, "spx": 2, "spx_ma125": 2, "nnh_pct": 2, "mcclellan": 1, "pc5d": 3,
                          "vix": 2, "vix50": 2, "junk": 3, "stock_bond": 2})
    if db.table_exists(con, "cnn_fg_components"):
        us["cnn_components"] = q(con, """SELECT date, component, score, rating, prev_close, prev_1w, prev_1m, prev_1y
                                         FROM cnn_fg_components
                                         WHERE date = (SELECT max(date) FROM cnn_fg_components) ORDER BY component""",
                                 ["date", "component", "score", "rating", "prev_close", "prev_1w", "prev_1m", "prev_1y"],
                                 {"score": 1, "prev_close": 1, "prev_1w": 1, "prev_1m": 1, "prev_1y": 1})
    if db.table_exists(con, "equity_daily"):
        us["equity"] = q(con, """SELECT date, ticker, close FROM equity_daily
                                 WHERE date >= DATE '2024-01-01' ORDER BY ticker, date""",
                         ["date", "ticker", "close"], {"close": 2})
    d["us"] = us
    # BTC daily candles (Bitstamp via TradingView, 2013+) for the main price chart and its moving averages
    d["btc_ohlc"] = q(con, """SELECT CAST(ts AS DATE), open, high, low, close FROM tv_bars
                              WHERE symbol='BITSTAMP:BTCUSD' AND interval='1d' ORDER BY ts""",
                      ["date", "o", "h", "l", "c"], {"o": 2, "h": 2, "l": 2, "c": 2})
    # hourly market structure (rolling ~2 months)
    d["market_1h"] = q(con, """SELECT ts,
            max(close) FILTER (WHERE symbol='CRYPTOCAP:TOTAL3')/1e9, max(close) FILTER (WHERE symbol='CRYPTOCAP:BTC.D'),
            max(close) FILTER (WHERE symbol='TVC:US10Y'), max(close) FILTER (WHERE symbol='TVC:DXY')
        FROM tv_bars WHERE interval='1h' AND ts >= (SELECT max(ts) FROM tv_bars WHERE interval='1h') - INTERVAL 30 DAY
        GROUP BY ts ORDER BY ts""", ["ts", "total3_b", "btc_d", "us10y", "dxy"],
        {"total3_b": 1, "btc_d": 3, "us10y": 3, "dxy": 3})

    # ------------------------------------------------------------ Binance derivatives
    deriv = {}
    syms = [r[0] for r in con.execute("SELECT DISTINCT symbol FROM binance_1h ORDER BY 1").fetchall()]
    for s in syms:
        daily = q(con, f"""SELECT CAST("timestamp" AS DATE) d,
                last(spot_close ORDER BY "timestamp"),
                last(sum_open_interest_value ORDER BY "timestamp")/1e9,
                avg(funding_rate)*1e4,
                avg(futures_close/spot_close - 1)*1e4,
                last(count_long_short_ratio ORDER BY "timestamp"),
                last(sum_toptrader_long_short_ratio ORDER BY "timestamp"),
                avg(sum_taker_long_short_vol_ratio),
                sum(futures_quote_volume)/1e9
            FROM binance_1h WHERE symbol='{s}' GROUP BY 1 HAVING count(*) >= 20 ORDER BY 1  -- skip partial days
            """,
                  ["date", "price", "oi_b", "premium_bp", "basis_bp", "ls_accounts", "ls_top", "taker", "vol"],
                  {"price": 4, "oi_b": 4, "premium_bp": 2, "basis_bp": 2, "ls_accounts": 3, "ls_top": 3, "taker": 3, "vol": 3})
        hourly = q(con, f"""SELECT "timestamp", spot_close, sum_open_interest_value/1e9, funding_rate*1e4,
                                   count_long_short_ratio, sum_taker_long_short_vol_ratio, futures_quote_volume/1e9
                            FROM binance_1h WHERE symbol='{s}'
                              AND "timestamp" >= (SELECT max("timestamp") FROM binance_1h WHERE symbol='{s}') - INTERVAL 14 DAY
                            ORDER BY 1""", ["ts", "price", "oi_b", "premium_bp", "ls_accounts", "taker", "vol"],
                   {"price": 4, "oi_b": 4, "premium_bp": 2, "ls_accounts": 3, "taker": 3, "vol": 4})
        deriv[s] = {"daily": daily, "hourly": hourly}
    d["deriv"] = deriv

    # ------------------------------------------------------------ cross-exchange: Coinbase premium, OI / funding by venue, CME
    def exists(v):
        return con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name=?", [v]).fetchone()[0] > 0
    VENUES = ["binance", "okx", "bybit", "hyperliquid"]
    def pivot(sql, key, venues):
        rows = con.execute(sql).fetchall()
        dates = sorted({r[0].isoformat() for r in rows})
        idx = {x: i for i, x in enumerate(dates)}
        out = {"date": dates}
        for v in venues:
            out[v] = [None] * len(dates)
        for dt, v, val in rows:
            if v in out:
                out[v][idx[dt.isoformat()]] = _clean(val, 4)
        return out
    xc = {}
    for a in ASSETS:
        x = {}
        if exists("v_coinbase_premium_1d"):
            x["cbp_1d"] = q(con, f"SELECT date, premium_bp FROM v_coinbase_premium_1d WHERE symbol='{a}' ORDER BY date",
                            ["date", "bp"], {"bp": 2})
        if exists("v_coinbase_premium_1h"):
            x["cbp_1h"] = q(con, f"""SELECT ts, premium_bp FROM v_coinbase_premium_1h WHERE symbol='{a}'
                                     AND ts >= (SELECT max(ts) FROM v_coinbase_premium_1h) - INTERVAL 180 DAY ORDER BY ts""",
                            ["ts", "bp"], {"bp": 2})
        if exists("v_oi_daily_by_venue"):
            x["oi"] = pivot(f"""SELECT date, venue, oi_usd/1e9 FROM v_oi_daily_by_venue WHERE symbol='{a}'
                                AND date >= current_date - 400 ORDER BY date""", "oi", VENUES)
        if exists("v_funding_daily"):
            x["funding"] = pivot(f"""SELECT date, venue, funding_8h_bp FROM v_funding_daily WHERE symbol='{a}'
                                     AND date >= current_date - 400 ORDER BY date""", "f", VENUES)
        if exists("v_cme_positioning"):
            x["cme"] = q(con, f"""SELECT report_date, oi_coins, lev_funds_net_coins, asset_mgr_net_coins
                                  FROM v_cme_positioning WHERE asset='{a}' ORDER BY report_date""",
                         ["date", "oi", "lev_net", "am_net"], {"oi": 1, "lev_net": 1, "am_net": 1})
        if exists("v_kimchi_premium_1d"):
            x["kp_1d"] = q(con, f"SELECT date, premium_pct FROM v_kimchi_premium_1d WHERE symbol='{a}' ORDER BY date", ["date", "pct"], {"pct": 3})
            x["kp_1h"] = q(con, f"""SELECT ts, premium_pct FROM v_kimchi_premium_1h WHERE symbol='{a}'
                                    AND ts >= (SELECT max(ts) FROM v_kimchi_premium_1h) - INTERVAL 180 DAY ORDER BY ts""", ["ts", "pct"], {"pct": 3})
        if exists("v_spot_volume_daily"):
            x["spot_vol"] = pivot(f"""SELECT date, venue, volume_usd/1e9 FROM v_spot_volume_daily WHERE symbol='{a}'
                                      AND date >= current_date - 400 ORDER BY date""", "v", ["binance", "coinbase", "upbit"])
        xc[a] = x
    d["xc"] = xc
    if exists("v_kimchi_premium_1d"):
        d["tether_kp"] = {"d": q(con, "SELECT date, premium_pct FROM v_kimchi_premium_1d WHERE symbol='USDT' ORDER BY date", ["date", "pct"], {"pct": 3}),
                          "h": q(con, """SELECT ts, premium_pct FROM v_kimchi_premium_1h WHERE symbol='USDT'
                                         AND ts >= (SELECT max(ts) FROM v_kimchi_premium_1h) - INTERVAL 180 DAY ORDER BY ts""", ["ts", "pct"], {"pct": 3})}
    if exists("v_spot_volume_daily"):   # BTC spot volume across Binance + Coinbase + Upbit, for the candle chart
        d["btc_vol"] = q(con, "SELECT date, sum(volume_usd)/1e9 FROM v_spot_volume_daily WHERE symbol='BTC' GROUP BY 1 ORDER BY 1", ["date", "b"], {"b": 3})

    # ------------------------------------------------------------ freshness per source (for the footer)
    d["freshness"] = [
        {"source": r[0], "last_run": str(r[1])[:16], "status": r[2]}
        for r in con.execute("""SELECT source, max(run_at), arg_max(status, run_at) FROM _ingest_log
                                GROUP BY 1 ORDER BY 1""").fetchall()]
    return d


def since(con) -> dict | None:
    """Hourly Binance spot closes for every coin-page coin from settings.SINCE_BASE_UTC. Base = the open of that
    hour's candle (the price at that moment); latest = the newest hourly close (the current hour may be forming)."""
    import pandas as pd
    if not db.table_exists(con, "bn_kline_1h"):
        return None
    base = pd.Timestamp(settings.SINCE_BASE_UTC)
    k = con.execute("""SELECT symbol, ts, open, close FROM bn_kline_1h WHERE market = 'spot' AND ts >= ?
                       ORDER BY symbol, ts""", [base.to_pydatetime()]).df()
    out = {"base_utc": base.isoformat(), "label": settings.SINCE_LABEL, "coins": {}}
    alt = con.execute("""SELECT symbol, venue, ts, open, close FROM cex_spot WHERE interval = '1h' AND ts >= ?
                         ORDER BY ts""", [base.to_pydatetime()]).df() if db.table_exists(con, "cex_spot") else pd.DataFrame()
    for sym in settings.COIN_PAGES:
        g = k[k.symbol == sym]
        if (g.empty or pd.Timestamp(g.ts.iloc[0]) != base) and sym in settings.SPOT_FALLBACK and len(alt):
            g = alt[(alt.symbol == sym) & (alt.venue == settings.SPOT_FALLBACK[sym][0])]   # e.g. HYPE: Hyperliquid spot
        if g.empty or pd.Timestamp(g.ts.iloc[0]) != base:
            continue
        b = float(g.open.iloc[0])
        out["coins"][sym] = {"base": _clean(b, 8), "t": [int(pd.Timestamp(t).timestamp()) for t in g.ts],
                             "pct": [_clean((c / b - 1) * 100, 3) for c in g.close], "last": _clean(float(g.close.iloc[-1]), 8)}
    out["last_utc"] = pd.Timestamp(k.ts.max()).isoformat() if len(k) else None
    return out


def top50(con) -> dict | None:
    """"Top 50" tab: every coin with Binance data (BTC + settings.BINANCE_UNIVERSE = the top-50 list + coin pages).
    Rolling moves from the latest hourly spot close (same as an exchange app), vs BTC in percentage points, distance
    from the 30-day high and from the 50-day average of closed daily closes, perp OI change (coins, so price moves
    don't count) and the 3-day average funding in bp per 8h."""
    import pandas as pd
    if not db.table_exists(con, "bn_kline_1h"):
        return None
    coins = list(dict.fromkeys(["BTC"] + settings.BINANCE_UNIVERSE))
    ph = ",".join("?" * len(coins))
    k = con.execute(f"""SELECT symbol, ts, high, close, quote_volume FROM bn_kline_1h WHERE market = 'spot' AND symbol IN ({ph})
                        AND ts >= (SELECT max(ts) FROM bn_kline_1h WHERE market = 'spot') - INTERVAL 31 DAY ORDER BY ts""", coins).df()
    if k.empty:
        return None
    dly = con.execute(f"""SELECT symbol, ts, close FROM bn_kline_1d WHERE market = 'spot' AND symbol IN ({ph})
                          AND ts >= current_date - 75 AND ts < current_date ORDER BY ts""", coins).df()
    last_ts = pd.Timestamp(k.ts.max())
    # perp OI: live hourly (VPN step, binance_1h) first, else the archive (bn_metrics_1h_hist, lags a day)
    oi_live = oi_hist = pd.DataFrame(columns=["symbol", "ts", "oi"])
    if db.table_exists(con, "binance_1h"):
        oi_live = con.execute("""SELECT replace(replace(symbol, '1000', ''), 'USDT', '') AS symbol, "timestamp" AS ts,
                                 sum_open_interest AS oi FROM binance_1h WHERE sum_open_interest IS NOT NULL
                                 AND "timestamp" >= current_date - 9""").df()
    if db.table_exists(con, "bn_metrics_1h_hist"):
        oi_hist = con.execute(f"""SELECT symbol, ts, sum_open_interest AS oi FROM bn_metrics_1h_hist WHERE symbol IN ({ph})
                                  AND ts >= current_date - 9""", coins).df()
    fund = {}
    if db.table_exists(con, "deriv_funding"):
        fund = dict(con.execute(f"""SELECT symbol, avg(funding_rate * 1e4 * 8 / coalesce(nullif(interval_hours, 0), 8))
                                    FROM deriv_funding WHERE venue = 'binance' AND symbol IN ({ph})
                                    AND ts >= now() - INTERVAL 3 DAY GROUP BY 1""", coins).fetchall())

    def back(g, hrs):
        x = g[g.ts <= last_ts - pd.Timedelta(hours=hrs)]
        return float(x.close.iloc[-1]) if len(x) else None

    def pct(a, b):
        return None if a is None or b is None or b == 0 else (a / b - 1) * 100

    try:   # live ranks from the universe source (data/state/universe.json), else the fixed 2026-10-07 list
        uni = json.loads(settings.UNIVERSE_FILE.read_text())
    except (FileNotFoundError, ValueError):
        uni = {}
    ranks = uni.get("rank") or {c: i + 2 for i, c in enumerate(settings.TOP50)}
    rows = []
    for c in coins:
        g = k[k.symbol == c]
        if g.empty:
            continue
        px = float(g.close.iloc[-1])
        r = {"c": c, "rank": 1 if c == "BTC" else ranks.get(c),
             "page": c in settings.COIN_PAGES, "px": px, "t": pd.Timestamp(g.ts.iloc[-1]).isoformat(),
             "d1": pct(px, back(g, 24)), "d7": pct(px, back(g, 24 * 7)), "d30": pct(px, back(g, 24 * 30)),
             "from_hi": pct(px, float(g[g.ts > last_ts - pd.Timedelta(days=30)].high.max())),
             "vol24": float(g[g.ts > last_ts - pd.Timedelta(hours=24)].quote_volume.sum()) / 1e6}
        dc = dly[dly.symbol == c].close
        r["sma50"] = pct(px, float(dc.tail(50).mean())) if len(dc) >= 50 else None
        for key, h in (("oi1", 24), ("oi7", 24 * 7)):
            for src in (oi_live, oi_hist):       # each change is measured within one source (units can differ)
                o = src[src.symbol == c].sort_values("ts")
                if len(o):
                    x = o[o.ts <= pd.Timestamp(o.ts.iloc[-1]) - pd.Timedelta(hours=h)]
                    if len(x):
                        r[key] = pct(float(o.oi.iloc[-1]), float(x.oi.iloc[-1]))
                        break
        r["fund"] = fund.get(c)
        rows.append(r)
    btc = next((r for r in rows if r["c"] == "BTC"), {})
    for r in rows:
        for w in ("d1", "d7", "d30"):
            r["v" + w[1:]] = None if r[w] is None or btc.get(w) is None else r[w] - btc[w]
    return {"as_of_utc": last_ts.isoformat(), "list_as_of": uni.get("as_of", settings.TOP50_AS_OF),
            "rows": [{key: (_clean(v, 8) if key == "px" else _clean(v, 2) if isinstance(v, float) else v) for key, v in r.items()} for r in rows]}


def run() -> str:
    con = db.connect(read_only=True)
    data = build(con)
    out = settings.DATA_DIR / "dashboard" / "data.json"
    data["since"] = since(con)
    data["top50"] = top50(con)
    from mdh.dashboard import etf_report
    data["etf_report"] = etf_report.build(con)
    # coin pages: candles + indicators per coin in dashboard/coins/<SYM>.json, summary + readout in data.json
    if db.table_exists(con, "bn_kline_1d"):
        from mdh.dashboard import coins
        data["coins"] = coins.export(con, out.parent / "coins")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, separators=(",", ":")))
    return f"wrote {out} ({out.stat().st_size / 1e6:.2f} MB)"
