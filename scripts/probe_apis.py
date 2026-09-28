#!/usr/bin/env python3
"""
probe_apis.py — one-shot health check for every free data source we plan to use.

For each source it makes 1-3 gentle requests, then reports:
  ok / HTTP status / latency / rows returned / earliest & latest date / rate-limit headers.

Run from the repo root on your Mac:
    python3 scripts/probe_apis.py
Optional API keys (read from environment or a .env file in the repo root):
    FRED_API_KEY, COINGECKO_DEMO_KEY, SOSOVALUE_API_KEY, COINGLASS_API_KEY
Optional libraries (probes are skipped if missing):
    pip install tvdatafeed   # TradingView TOTAL3 / BTC.D history (unofficial)

Writes data/probe/probe_report.json and prints a summary table.
Total footprint: ~30 requests spread across ~15 hosts — nowhere near any limit.
"""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
RATE_HDR_KEYS = ("ratelimit", "rate-limit", "x-mbx-used-weight", "retry-after", "x-cg", "quota")


def load_env():
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def ts(v):
    """epoch s/ms or ISO string -> YYYY-MM-DD"""
    if v is None:
        return None
    if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
        v = float(v)
        if v > 1e11:
            v /= 1000
        return datetime.fromtimestamp(v, tz=timezone.utc).strftime("%Y-%m-%d")
    return str(v)[:10]


def get(url, **kw):
    headers = {**UA, **kw.pop("headers", {})}
    t0 = time.time()
    r = requests.request(kw.pop("method", "GET"), url, headers=headers, timeout=30, **kw)
    r.latency_ms = int((time.time() - t0) * 1000)
    r.rate_headers = {k: v for k, v in r.headers.items()
                      if any(s in k.lower() for s in RATE_HDR_KEYS)}
    return r


RESULTS = []


def probe(name, needs=None):
    def deco(fn):
        def run():
            rec = {"source": name, "ok": False}
            if needs and not os.environ.get(needs):
                rec.update(skipped=f"set {needs} to test")
                RESULTS.append(rec)
                return
            try:
                out = fn() or {}
                rec["ok"] = True
                rec.update(out)
            except Exception as e:  # noqa: BLE001
                rec["error"] = f"{type(e).__name__}: {str(e)[:200]}"
            RESULTS.append(rec)
            time.sleep(0.5)  # be polite between sources
        run.__name__ = fn.__name__
        PROBES.append(run)
        return run
    return deco


PROBES = []


def meta(r, rows=None, first=None, last=None, **extra):
    d = {"status": r.status_code, "latency_ms": r.latency_ms, "rows": rows,
         "first": first, "last": last, "rate_headers": r.rate_headers}
    d.update(extra)
    if r.status_code != 200:
        d["ok"] = False
        d["body"] = r.text[:200]
    return d


# ---------------------------------------------------------------- Binance
@probe("binance_spot_klines_1d")
def _():
    r = get("https://api.binance.com/api/v3/klines",
            params={"symbol": "BTCUSDT", "interval": "1d", "startTime": 0, "limit": 1000})
    j = r.json()
    return meta(r, len(j), ts(j[0][0]), ts(j[-1][0]))


@probe("binance_futures_funding")
def _():
    r = get("https://fapi.binance.com/fapi/v1/fundingRate",
            params={"symbol": "BTCUSDT", "startTime": 0, "limit": 1000})
    j = r.json()
    return meta(r, len(j), ts(j[0]["fundingTime"]), ts(j[-1]["fundingTime"]))


@probe("binance_futures_oi_hist(REST, 30d max)")
def _():
    r = get("https://fapi.binance.com/futures/data/openInterestHist",
            params={"symbol": "BTCUSDT", "period": "1d", "limit": 500})
    j = r.json()
    return meta(r, len(j), ts(j[0]["timestamp"]), ts(j[-1]["timestamp"]))


@probe("binance_vision_archive")
def _():
    r = get("https://data.binance.vision/data/futures/um/daily/metrics/BTCUSDT/"
            "BTCUSDT-metrics-2024-08-01.zip", method="HEAD")
    return meta(r, note="no rate limit on static archive")


# ---------------------------------------------------------------- Macro
@probe("fred_csv_DGS10(no key)")
def _():
    r = get("https://fred.stlouisfed.org/graph/fredgraph.csv", params={"id": "DGS10"})
    lines = [l for l in r.text.strip().splitlines()[1:] if l]
    return meta(r, len(lines), lines[0].split(",")[0], lines[-1])


@probe("fred_api_WALCL(keyed)", needs="FRED_API_KEY")
def _():
    r = get("https://api.stlouisfed.org/fred/series/observations",
            params={"series_id": "WALCL", "file_type": "json",
                    "api_key": os.environ["FRED_API_KEY"]})
    obs = r.json().get("observations", [])
    return meta(r, len(obs), obs[0]["date"] if obs else None, obs[-1]["date"] if obs else None)


@probe("treasury_gov_yield_csv(backup)")
def _():
    y = datetime.now(timezone.utc).year
    r = get(f"https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
            f"daily-treasury-rates.csv/{y}/all",
            params={"type": "daily_treasury_yield_curve", "field_tdr_date_value": y, "page": "", "_format": "csv"})
    lines = r.text.strip().splitlines()
    return meta(r, len(lines) - 1, lines[-1][:10] if len(lines) > 1 else None,
                lines[1][:60] if len(lines) > 1 else None)


@probe("yahoo_chart(^TNX,SPY,IBIT)")
def _():
    out = {}
    last_r = None
    for sym in ("^TNX", "SPY", "IBIT"):
        last_r = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
                     params={"range": "max", "interval": "1d"})
        res = last_r.json()["chart"]["result"][0]
        t = res["timestamp"]
        out[sym] = f"{len(t)} rows {ts(t[0])}..{ts(t[-1])}"
        time.sleep(1)
    return meta(last_r, detail=out)


# ---------------------------------------------------------------- Crypto-wide
@probe("defillama_stablecoins_total")
def _():
    r = get("https://stablecoins.llama.fi/stablecoincharts/all")
    j = r.json()
    last = j[-1]["totalCirculatingUSD"].get("peggedUSD")
    return meta(r, len(j), ts(j[0]["date"]), ts(j[-1]["date"]), latest_usd=round(last / 1e9, 1))


@probe("coingecko_global(snapshot)")
def _():
    h = {}
    base = "https://api.coingecko.com/api/v3"
    if os.environ.get("COINGECKO_DEMO_KEY"):
        h["x-cg-demo-api-key"] = os.environ["COINGECKO_DEMO_KEY"]
    r = get(f"{base}/global", headers=h)
    d = r.json()["data"]
    pct = d["market_cap_percentage"]
    return meta(r, 1, None, ts(d["updated_at"]),
                total_mcap_t=round(d["total_market_cap"]["usd"] / 1e12, 3),
                btc_pct=round(pct["btc"], 2), eth_pct=round(pct["eth"], 2),
                usdt_pct=round(pct.get("usdt", 0), 2), usdc_pct=round(pct.get("usdc", 0), 2),
                keyed=bool(h))


@probe("coingecko_btc_history(days=365)")
def _():
    h = {}
    if os.environ.get("COINGECKO_DEMO_KEY"):
        h["x-cg-demo-api-key"] = os.environ["COINGECKO_DEMO_KEY"]
    r = get("https://api.coingecko.com/api/v3/coins/bitcoin/market_chart",
            params={"vs_currency": "usd", "days": 365, "interval": "daily"}, headers=h)
    mc = r.json().get("market_caps", [])
    return meta(r, len(mc), ts(mc[0][0]) if mc else None, ts(mc[-1][0]) if mc else None)


@probe("coingecko_global_chart(paid-only check)")
def _():
    h = {}
    if os.environ.get("COINGECKO_DEMO_KEY"):
        h["x-cg-demo-api-key"] = os.environ["COINGECKO_DEMO_KEY"]
    r = get("https://api.coingecko.com/api/v3/global/market_cap_chart",
            params={"vs_currency": "usd", "days": 30}, headers=h)
    d = meta(r)
    d["note"] = "expected to fail on free tier"
    d["ok"] = r.status_code == 200
    return d


@probe("coinmetrics_community(BTC,ETH mcap)")
def _():
    r = get("https://community-api.coinmetrics.io/v4/timeseries/asset-metrics",
            params={"assets": "btc,eth", "metrics": "CapMrktCurUSD,CapMVRVCur",
                    "frequency": "1d", "start_time": "2010-01-01", "page_size": 10000})
    data = r.json().get("data", [])
    return meta(r, len(data), ts(data[0]["time"]) if data else None,
                ts(data[-1]["time"]) if data else None)


@probe("coinpaprika_global(backup)")
def _():
    r = get("https://api.coinpaprika.com/v1/global")
    j = r.json()
    return meta(r, 1, None, ts(j.get("last_updated")),
                btc_dominance=j.get("bitcoin_dominance_percentage"))


@probe("alternative_me_fear_greed")
def _():
    r = get("https://api.alternative.me/fng/", params={"limit": 0})
    j = r.json()["data"]
    return meta(r, len(j), ts(j[-1]["timestamp"]), ts(j[0]["timestamp"]))


@probe("deribit_dvol_btc")
def _():
    now = int(time.time() * 1000)
    r = get("https://www.deribit.com/api/v2/public/get_volatility_index_data",
            params={"currency": "BTC", "resolution": "1D",
                    "start_timestamp": now - 1000 * 86400 * 1000, "end_timestamp": now})
    d = r.json()["result"]["data"]
    return meta(r, len(d), ts(d[0][0]), ts(d[-1][0]))


# ---------------------------------------------------------------- ETF flows
def _farside(path):
    r = get(f"https://farside.co.uk/{path}/")
    html = r.text
    blocked = "cf-chl" in html or "Just a moment" in html
    rows = html.count("<tr")
    return meta(r, rows, None, None, cloudflare_block=blocked,
                ok=(r.status_code == 200 and not blocked and rows > 10))


@probe("farside_btc")
def _():
    return _farside("btc")


@probe("farside_eth")
def _():
    return _farside("eth")


@probe("farside_sol")
def _():
    return _farside("sol")


@probe("sosovalue_etf_history", needs="SOSOVALUE_API_KEY")
def _():
    key = os.environ["SOSOVALUE_API_KEY"]
    detail = {}
    last_r = None
    # v2 endpoint (historical inflow chart) — one call per ETF family
    for t in ("us-btc-spot", "us-eth-spot", "us-sol-spot", "us-xrp-spot"):
        last_r = get("https://api.sosovalue.xyz/openapi/v2/etf/historicalInflowChart",
                     method="POST", json={"type": t},
                     headers={"x-soso-api-key": key, "Content-Type": "application/json"})
        try:
            data = last_r.json().get("data") or []
            dates = sorted(x.get("date") for x in data if x.get("date"))
            detail[t] = f"{len(data)} rows {dates[0] if dates else ''}..{dates[-1] if dates else ''}"
        except Exception:  # noqa: BLE001
            detail[t] = f"HTTP {last_r.status_code}: {last_r.text[:120]}"
        time.sleep(3.5)  # free tier = 20/min
    return meta(last_r, detail=detail)


@probe("coinglass_btc_etf_flows", needs="COINGLASS_API_KEY")
def _():
    r = get("https://open-api-v4.coinglass.com/api/etf/bitcoin/flow-history",
            headers={"CG-API-KEY": os.environ["COINGLASS_API_KEY"]})
    data = r.json().get("data") or []
    return meta(r, len(data))


# ---------------------------------------------------------------- TradingView (unofficial)
@probe("tradingview_TOTAL3/BTC.D/USDT.D(tvdatafeed)")
def _():
    try:
        from tvDatafeed import TvDatafeed, Interval  # type: ignore
    except ImportError:
        return {"ok": False, "skipped": "pip install tvdatafeed  (optional)"}
    tv = TvDatafeed()
    detail = {}
    for sym in ("TOTAL", "TOTAL3", "BTC.D", "USDT.D", "USDC.D"):
        df = tv.get_hist(symbol=sym, exchange="CRYPTOCAP", interval=Interval.in_daily, n_bars=5000)
        detail[sym] = (f"{len(df)} rows {df.index[0]:%Y-%m-%d}..{df.index[-1]:%Y-%m-%d} "
                       f"last={df['close'].iloc[-1]:.4g}") if df is not None else "none"
        time.sleep(2)
    return {"ok": all(v != "none" for v in detail.values()), "detail": detail}


# ---------------------------------------------------------------- main
def main():
    load_env()
    for p in PROBES:
        p()
    out_dir = ROOT / "data" / "probe"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    (out_dir / "probe_report.json").write_text(json.dumps({"run_at": stamp, "results": RESULTS}, indent=2))

    print(f"\nProbe run {stamp}\n")
    print(f"{'source':44} {'ok':4} {'http':5} {'ms':>6} {'rows':>7}  range / note")
    print("-" * 110)
    for r in RESULTS:
        flag = "SKIP" if r.get("skipped") else ("OK" if r.get("ok") else "FAIL")
        rng = r.get("skipped") or r.get("error") or ""
        if not rng:
            rng = f"{r.get('first') or ''} .. {r.get('last') or ''}".strip(" .")
            if r.get("detail"):
                rng += " " + json.dumps(r["detail"])
        print(f"{r['source'][:44]:44} {flag:4} {str(r.get('status','')):5} "
              f"{str(r.get('latency_ms','')):>6} {str(r.get('rows','')):>7}  {rng[:200]}")
    print(f"\nFull report: {out_dir / 'probe_report.json'}")


if __name__ == "__main__":
    main()
