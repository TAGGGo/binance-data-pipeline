# Data sources — access, limits, caveats

Tested 2026-09-28. All free. Keys live in `.env` (gitignored).

| Source | Key? | Published limit | Our budget | History | Notes |
|---|---|---|---|---|---|
| Binance Vision archive (`data.binance.vision`) | no | none (static files) | 1200/min | 2017+ (metrics ~2021+) | Daily zips appear ~T+1. |
| Binance live spot (`data-api.binance.vision`) | no | 6000 weight/min | 600/min | recent | Public mirror, not geo-blocked. |
| Binance live futures (`fapi.binance.com`) | no | 2400 weight/min | — | 30 days for OI/ratios | **HTTP 451 from US IPs.** Needs VPN; otherwise T-0 is filled from the archive next day. |
| FRED (`api.stlouisfed.org`) | FRED_API_KEY | 120/min | 80/min | decades | Keyless `fredgraph.csv` times out from some networks — use the API. |
| SoSoValue v1 (`openapi.sosovalue.com`) | SOSOVALUE_API_KEY | 10/min, 10k/month | 7/min, 9k/month | **last 30 days** | Covers BTC/ETH/SOL/XRP. Older ranges return 403 on free plan. |
| SoSoValue v2 (`api.sosovalue.xyz`) | same key | 20/min | 14/min | last 300 days | BTC/ETH/SOL only; XRP empty. |
| Farside | — | — | — | BTC 2024-01-11+, ETH 2024-07-23+ | Cloudflare blocks scripts. Full history captured once via browser → `seeds/etf/`. |
| TradingView (`tvdatafeed`, unofficial) | no | unpublished | 1 symbol / 1.5 s | CRYPTOCAP daily 2014+, TVC daily 2005+; hourly ~2 months (5000 bars for TVC) | Occasional "connection lost" → auto-retried. Daily TVC bars are re-dated (+12h) to the trading date. Could break if TradingView changes its websocket. |
| DefiLlama stablecoins | no | generous | 60/min | 2017-11+ | |
| Coin Metrics Community | no | 10 req / 6 s | 60/min | 2010+ | Free metrics subset only. |
| Nasdaq API (`api.nasdaq.com`) | no | unpublished | 20/min | ~10 years | Yahoo Finance returned 429 immediately from this network, so Nasdaq is used instead. |
| Deribit | no | credit-based (~20/s) | 60/min | DVOL 2021-03+ | |
| alternative.me Fear & Greed | no | — | 20/min | 2018-02+ | |
| CoinGecko `/global` | optional Demo key | ~30/min keyless; 10k/month Demo | 20/min, 9k/month | snapshot only | Historical global chart is paid-only (401) — TradingView covers it. |

## Definitions

* **TOTAL3** (TradingView) = total crypto market cap excluding BTC and ETH. It *includes* stablecoins.
* **btc_dom_ex_stables** = BTC mcap / (TOTAL − all stablecoins [DefiLlama]).
* **btc_dom_ex_usdt_usdc** = BTC.D / (100 − USDT.D − USDC.D), TradingView-only, consistent basis.
* **net_liquidity_tn** = WALCL − WTREGEN − RRPONTSYD (all converted to $ trillions), as-of joined daily.
* **ETF flows** in USD. `v_etf_flows` prefers SoSoValue; Farside fills older BTC/ETH dates.
  Cumulative BTC flows agree across providers within ~0.1% ($57.6B Farside vs $57.5B SoSoValue).
