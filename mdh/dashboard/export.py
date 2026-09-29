"""
Export a compact JSON snapshot of the database for the dashboard page.

    python3 -m mdh export            -> data/dashboard/data.json

The page (mdh/dashboard/page.html) only reads this file, so refreshing the dashboard is:
`python3 -m mdh update && python3 -m mdh export`, then republish data.json.
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
    for a in ASSETS:
        etf[a] = q(con, f"""SELECT date, net_inflow_usd/1e6, net_assets_usd/1e9, cum_net_inflow_usd/1e9, value_traded_usd/1e6
                            FROM v_etf_flows WHERE asset='{a}' ORDER BY date""",
                   ["date", "flow_m", "aum_b", "cum_b", "traded_m"], {"flow_m": 2, "aum_b": 3, "cum_b": 3, "traded_m": 1})
    d["etf"] = etf
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
                avg(sum_taker_long_short_vol_ratio)
            FROM binance_1h WHERE symbol='{s}' GROUP BY 1 HAVING count(*) >= 20 ORDER BY 1  -- skip partial days
            """,
                  ["date", "price", "oi_b", "premium_bp", "basis_bp", "ls_accounts", "ls_top", "taker"],
                  {"price": 4, "oi_b": 4, "premium_bp": 2, "basis_bp": 2, "ls_accounts": 3, "ls_top": 3, "taker": 3})
        hourly = q(con, f"""SELECT "timestamp", spot_close, sum_open_interest_value/1e9, funding_rate*1e4,
                                   count_long_short_ratio, sum_taker_long_short_vol_ratio
                            FROM binance_1h WHERE symbol='{s}'
                              AND "timestamp" >= (SELECT max("timestamp") FROM binance_1h WHERE symbol='{s}') - INTERVAL 14 DAY
                            ORDER BY 1""", ["ts", "price", "oi_b", "premium_bp", "ls_accounts", "taker"],
                   {"price": 4, "oi_b": 4, "premium_bp": 2, "ls_accounts": 3, "taker": 3})
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
        xc[a] = x
    d["xc"] = xc

    # ------------------------------------------------------------ freshness per source (for the footer)
    d["freshness"] = [
        {"source": r[0], "last_run": str(r[1])[:16], "status": r[2]}
        for r in con.execute("""SELECT source, max(run_at), arg_max(status, run_at) FROM _ingest_log
                                GROUP BY 1 ORDER BY 1""").fetchall()]
    return d


def run() -> str:
    con = db.connect(read_only=True)
    data = build(con)
    out = settings.DATA_DIR / "dashboard" / "data.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, separators=(",", ":")))
    return f"wrote {out} ({out.stat().st_size / 1e6:.2f} MB)"
