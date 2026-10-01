# market-data-hub

Collects crypto + macro market data from free sources into one local DuckDB file
(`data/market.duckdb`) so dashboards and analysis run on **real stored numbers**.

| Area | What | Source |
|---|---|---|
| Crypto derivatives | Spot/perp OHLCV, open interest, long/short ratios, funding, mark/index price (1h) | Binance Vision archive + live API |
| Spot ETF flows | BTC · ETH · SOL · XRP daily net flows, AUM, volume (per-fund for BTC/ETH) | SoSoValue API + seeded Farside history |
| Market structure | TOTAL, TOTAL2, TOTAL3, BTC.D, ETH.D, USDT.D, USDC.D, OTHERS.D (daily since 2014, hourly rolling) | TradingView (`tvdatafeed`) |
| Stablecoins | Total / USDT / USDC circulating supply | DefiLlama |
| Rates & macro | 2Y/10Y/30Y, real yield, breakevens, Fed funds, dollar, Fed balance sheet, TGA, RRP, M2, VIX, HY spread, oil | FRED |
| Intraday macro | DXY, US10Y, US02Y (daily since 2005, hourly rolling) | TradingView |
| Equities | SPY QQQ IWM TLT GLD IBIT FBTC ETHA MSTR COIN NVDA HOOD | Nasdaq API |
| On-chain | BTC/ETH price, market cap, realized cap, MVRV, active addresses, tx count, supply | Coin Metrics Community |
| Sentiment / vol | Fear & Greed index, Deribit DVOL (BTC, ETH) | alternative.me, Deribit |
| Snapshot | CoinGecko global market cap + dominance | CoinGecko |
| XRP holders | Daily XRPL rich list (top 10,000 accounts) by holder type: exchange, Ripple, likely custodial, unlabeled whales (`v_xrp_holders_daily`, `v_xrp_whale_flow_daily`) | XRPScan + Ripple public XRPL server |

Derived views: `v_etf_flows`, `v_crypto_market_daily` (incl. **BTC dominance ex-stablecoins**),
`v_net_liquidity` (Fed assets − TGA − RRP), `v_macro_daily` (one wide row per day).
Full column list: [docs/DATA_DICTIONARY.md](docs/DATA_DICTIONARY.md). Source notes & limits: [docs/SOURCES.md](docs/SOURCES.md).

## Setup

```bash
pip3 install -r requirements.txt
cp .env.example .env        # add FRED_API_KEY and SOSOVALUE_API_KEY (both free)
python3 -m mdh backfill     # first run: full history (Binance part takes a while)
```

## Everyday use

```bash
python3 -m mdh update                  # incremental update of everything
python3 -m mdh update fred sosovalue   # just some sources
python3 -m mdh status                  # row counts + first/last date per series, last run per source
python3 -m mdh sql "SELECT * FROM v_macro_daily ORDER BY date DESC LIMIT 5"
python3 -m mdh sources                 # list source names
```

Hourly automatic updates on macOS: `scripts/ops/install_scheduler.sh` (launchd; see the script header).

The original Binance CLI still works: `python3 market_data_parser.py --symbols BTCUSDT --intervals 1h`
(output now defaults to `data/raw/binance/`).

## Dashboard

`mdh/dashboard/page.html` is a static page that reads one data file, `data.json`, produced by

```bash
python3 -m mdh update && python3 -m mdh export     # -> data/dashboard/data.json
```

It is published as a private Claude artifact ("Market Data Hub"). To refresh it, ask Claude to
refresh the dashboard: it runs the two commands above and republishes `data.json` to the same link.
Pages: Overview, ETF flows, Market & macro, Derivatives (Binance).

## Coin pages (BTC ETH XRP SOL ZEC + DASH ZEN LDO ENA AAVE SUI UNI NEAR DOGE)

One page per coin with 4H / 1D / 1W / 1M candles (Binance spot, UTC boundaries), MA 20/30/50/100 (SMA or EMA),
MACD 12/26/9, RSI 14, funding, open interest and spot/perp CVD, plus plain-English "what the numbers say" lines.

* Data: `binance_hist` source (no VPN): daily klines since listing, hourly klines since 2023, hourly OI snapshots from
  the metrics archive, funding seed. Today's perp/OI comes from `binance_1h` (VPN step).
  Tables: `bn_kline_1d`, `bn_kline_1h`, `bn_metrics_1h_hist`.
* Math: `mdh/indicators.py` (formulas written out at the top), tests in `tests/test_indicators.py`.
* Check against Binance: `python3 scripts/tools/verify_candles.py` (rebuilt days/weeks/months vs Binance's own klines).
* Export: `mdh export` also writes `data/dashboard/coins/<SYM>.json`; publish those with data.json.
* Add a coin: append to `COIN_MORE` (or `COIN_TABS`) in `mdh/settings.py`, add `<SYM>USDT` to `BINANCE_SYMBOLS` with a
  recent `BINANCE_START_OVERRIDES` date, run `python3 -m mdh update binance_hist`.

## Layout

```
mdh/
  settings.py          what to collect (symbols, series) + per-host rate budgets
  cli.py               `python3 -m mdh ...`
  core/http.py         shared rate-limited client (pacing, retries, backoff, monthly quotas)
  core/db.py           DuckDB upserts + ingest log
  sources/             one module per provider (binance/, fred, nasdaq, tradingview, ...)
  derived/views.sql    derived views (dominance ex-stables, net liquidity, macro daily)
seeds/etf/             one-time ETF flow history that APIs don't serve for free (tracked in git)
scripts/ops/           hourly pipeline: run_update.sh, install_scheduler.sh, fetch_bgeometrics.py, push_etf_report.py
scripts/research/      backtests and one-off analyses (run from repo root)
scripts/tools/         probe_apis.py (health check), verify_candles.py, load_xrpl_backfill.py
tests/                 python3 -m unittest discover tests
data/                  gitignored: market.duckdb, raw/, state/, logs/, outputs/ (finished charts), scratch/ (dumps)
```

## Rate limits

Every request goes through `mdh/core/http.py`: each host is paced at ~70% of its published
limit (`HOST_RPM` in settings), 429/5xx/timeouts are retried with exponential backoff
(honouring `Retry-After`, halving that host's budget after a 429), and hard monthly quotas
(SoSoValue, CoinGecko) are counted in `data/state/http_quota.json` and never exceeded.
A normal hourly `update` makes ~60 HTTP calls in total.

## Adding things

* New Binance symbol → append to `BINANCE_SYMBOLS` in `mdh/settings.py`, run `python3 -m mdh backfill binance`.
* New FRED series / stock / TradingView symbol → add to the matching dict/list in settings, run `update`.
* New provider → add `mdh/sources/<name>.py` with `run(ctx, full=False) -> {table: rows}` and register it
  in `mdh/sources/__init__.py`.
