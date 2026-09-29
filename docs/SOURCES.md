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

| Coinbase Exchange (`api.exchange.coinbase.com`) | no | 10 req/s | 300/min | BTC-USD 2015-07+, hourly + daily | Spot candles; used for the Coinbase premium (vs Binance, USDT-adjusted). |
| OKX (`www.okx.com`) | no | 5 req / 2 s (stats) | 60/min | OI: 180 days daily, 30 days hourly; funding ~3 months | Collected hourly from now on. |
| Bybit (`api.bybit.com`) | no | 600 / 5 s | 120/min | OI and funding back to 2020–21 | **403 from US IPs** — runs in the VPN step. |
| Hyperliquid (`api.hyperliquid.xyz`) | no | 1200 weight/min | 40/min | funding 2023-05+; OI none | OI snapshotted every run. Funding is hourly (scaled ×8 to compare). |
| CFTC TFF (`publicreporting.cftc.gov`) | no | — | 30/min | BTC 2018+, ETH 2021+, SOL/XRP 2025+ | Weekly CME positioning (as of Tuesday). |
| Upbit (`api.upbit.com`) | no | 10 req/s (candles) | 300/min | KRW daily 2017-09+, hourly 2024-08+ | Korean spot; Kimchi and Tether premium, Korean volume. |
| TradingView FX_IDC:USDKRW | no | — | shared TV pacing | daily 2007+, hourly ~10 months | FX for the Kimchi premium. |

## Definitions

* **TOTAL3** (TradingView) = total crypto market cap excluding BTC and ETH. It *includes* stablecoins.
* **btc_dom_ex_stables** = BTC mcap / (TOTAL − all stablecoins [DefiLlama]).
* **btc_dom_ex_usdt_usdc** = BTC.D / (100 − USDT.D − USDC.D), TradingView-only, consistent basis.
* **net_liquidity_tn** = WALCL − WTREGEN − RRPONTSYD (all converted to $ trillions), as-of joined daily.
* **ETF flows** in USD. `v_etf_flows` prefers SoSoValue; Farside fills older BTC/ETH dates.
  Cumulative BTC flows agree across providers within ~0.1% ($57.6B Farside vs $57.5B SoSoValue).
* **Coinbase premium** = Coinbase USD close / (Binance USDT close × Coinbase USDT-USD) − 1, in bp. Completed hours/days only.
* **Tracked OI** = Binance USDT-M + OKX (all contracts) + Bybit USDT perp + Hyperliquid. Not the whole market.
* **Funding (8h bp)** = daily average funding rate × 8 / funding interval hours, in basis points.
* **Kimchi premium** = Upbit KRW close / (Binance USDT close × USD/KRW) − 1, in %. **Tether premium** = Upbit USDT-KRW / USD/KRW − 1.
* **Spot volume** = base volume × close (Binance, Coinbase); Upbit KRW traded value ÷ USD/KRW.
