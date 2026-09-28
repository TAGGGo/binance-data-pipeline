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

Hourly automatic updates on macOS: `scripts/install_scheduler.sh` (launchd; see the script header).

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
scripts/               probe_apis.py (health check), install_scheduler.sh
tests/                 python3 -m unittest discover tests
data/                  gitignored: market.duckdb, raw/binance/*.csv, state/
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
