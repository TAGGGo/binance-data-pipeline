"""
Central configuration: paths, what to collect, and per-host rate budgets.
Edit the lists below to add symbols / series — no other code changes needed.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("MDH_DATA_DIR", ROOT / "data"))
DB_PATH = DATA_DIR / "market.duckdb"
RAW_DIR = DATA_DIR / "raw"
STATE_DIR = DATA_DIR / "state"
SEEDS_DIR = ROOT / "seeds"          # small, hand-collected history that is tracked in git


def load_env() -> None:
    """Read KEY=VALUE lines from ROOT/.env into os.environ (never overrides real env vars)."""
    f = ROOT / ".env"
    if not f.exists():
        return
    for line in f.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# ----------------------------------------------------------------------------- rate budgets
# requests / minute we allow ourselves per host (~70% of each provider's published limit)
HOST_RPM = {
    "api.stlouisfed.org": 80,              # FRED: 120/min
    "openapi.sosovalue.com": 7,            # SoSoValue v1: 10/min
    "api.sosovalue.xyz": 14,               # SoSoValue v2: 20/min
    "api.coingecko.com": 20,               # keyless ~30/min (Demo key: 30-100/min)
    "community-api.coinmetrics.io": 60,    # 10 req / 6 s
    "stablecoins.llama.fi": 60,
    "api.llama.fi": 30,                    # protocol / fees / volume endpoints (unpublished limit)
    "rpc.mainnet.near.org": 60,            # NEAR public RPC (block header: total_supply)
    "archival-rpc.mainnet.fastnear.com": 60,   # NEAR archival RPC (supply history backfill)
    "api.nasdaq.com": 20,                  # unpublished; stay gentle
    "www.deribit.com": 60,                 # 20 req/s credit system
    "api.alternative.me": 20,
    "data-api.binance.vision": 600,        # weight 6000/min
    "data.binance.vision": 1200,           # static archive
    "cdn.cboe.com": 20,
    "api.exchange.coinbase.com": 300,      # public: 10 req/s
    "www.okx.com": 60,                     # rubik stats: 5 req / 2 s
    "api.bybit.com": 120,                  # 600 req / 5 s per IP
    "api.hyperliquid.xyz": 40,             # 1200 weight/min; history calls weigh ~20
    "publicreporting.cftc.gov": 30,
    "api.upbit.com": 300,
    "api.xrpscan.com": 10,                 # rich list + labels; unpublished limit, stay gentle
    "s2.ripple.com:51234": 240,            # Ripple public full-history server (account_info)
    "s3-ap-northeast-1.amazonaws.com": 120,   # data.binance.vision bucket listing                  # 10 req/s per IP for candles
}
# hard monthly ceilings (published quota minus a safety margin)
MONTHLY_QUOTA = {
    "openapi.sosovalue.com": 9000,         # 10,000 / month
    "api.sosovalue.xyz": 9000,
    "api.coingecko.com": 9000,             # Demo plan: 10,000 / month
}

# ----------------------------------------------------------------------------- Binance
BINANCE_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ZECUSDT", "DASHUSDT", "STRKUSDT",
                   "ZENUSDT", "LDOUSDT", "ENAUSDT", "AAVEUSDT", "SUIUSDT", "UNIUSDT", "NEARUSDT", "DOGEUSDT",
                   "HYPEUSDT"]
BINANCE_INTERVALS = ["1h"]
BINANCE_START = "2024-08-01"
# symbols whose history intentionally starts later (keeps the hourly run from backfilling them)
# coin-page coins added later: their history comes from binance_hist (archive), so the hourly VPN step only
# needs the last few days (it supplies today's live perp price, OI and ratios)
BINANCE_START_OVERRIDES = {"DASHUSDT": "2025-08-01", "STRKUSDT": "2025-08-01",
                           **{f"{c}USDT": "2026-09-22" for c in ["ZEN", "LDO", "ENA", "AAVE", "SUI", "UNI", "NEAR", "DOGE", "HYPE"]}}

# ----------------------------------------------------------------------------- FRED (id: description)
FRED_SERIES = {
    "DGS10": "US 10Y Treasury yield (%)",
    "DGS2": "US 2Y Treasury yield (%)",
    "DGS30": "US 30Y Treasury yield (%)",
    "T10Y2Y": "10Y minus 2Y spread (%)",
    "DFII10": "10Y TIPS real yield (%)",
    "T10YIE": "10Y breakeven inflation (%)",
    "DFF": "Effective Fed funds rate (%)",
    "DTWEXBGS": "Broad trade-weighted US dollar index",
    "WALCL": "Fed total assets ($M, weekly)",
    "WTREGEN": "Treasury General Account ($M, weekly)",
    "RRPONTSYD": "Overnight reverse repo ($B, daily)",
    "M2SL": "M2 money supply ($B, monthly)",
    "SP500": "S&P 500 index level",
    "NASDAQCOM": "Nasdaq Composite index level",
    "VIXCLS": "CBOE VIX close",
    "BAMLH0A0HYM2": "US high-yield OAS spread (%)",
    "DCOILWTICO": "WTI crude oil ($/bbl)",
}

# ----------------------------------------------------------------------------- Stocks / ETFs (Nasdaq API)
EQUITIES = {  # ticker: asset class for api.nasdaq.com
    "SPY": "etf", "QQQ": "etf", "IWM": "etf", "TLT": "etf", "GLD": "etf",
    "IBIT": "etf", "FBTC": "etf", "ETHA": "etf",
    "MSTR": "stocks", "COIN": "stocks", "NVDA": "stocks", "HOOD": "stocks",
}
EQUITY_START = "2015-01-01"

# ----------------------------------------------------------------------------- TradingView (exchange, symbol)
TV_SYMBOLS = [
    ("CRYPTOCAP", "TOTAL"), ("CRYPTOCAP", "TOTAL2"), ("CRYPTOCAP", "TOTAL3"),
    ("CRYPTOCAP", "BTC.D"), ("CRYPTOCAP", "ETH.D"), ("CRYPTOCAP", "OTHERS.D"),
    ("CRYPTOCAP", "USDT.D"), ("CRYPTOCAP", "USDC.D"),
    ("TVC", "DXY"), ("TVC", "US10Y"), ("TVC", "US02Y"),
    ("BITSTAMP", "BTCUSD"),   # long BTC OHLC history (2011+) for candles and weekly moving averages
    ("FX_IDC", "USDKRW"),     # USD/KRW for the Kimchi premium
]
TV_INTERVALS = ["1d", "1h"]   # daily = full history; hourly = rolling ~2 months, accumulated going forward

# ----------------------------------------------------------------------------- Crypto-wide
ETF_ASSETS = ["BTC", "ETH", "SOL", "XRP"]
# Grayscale ETPs SoSoValue doesn't cover: flows from the fund's own daily workbook (mdh/sources/grayscale.py)
GRAYSCALE_ETFS = {"ZEC": {"ticker": "ZCSH", "product_id": "c131f37d-6f8f-4af9-8645-346503081d6a", "etf_listing": "2026-08-25"}}
# ETPs read straight from the issuers (SoSoValue's free API doesn't carry them): snapshots / history in
# mdh/sources/bitwise.py + twentyone.py, flows in etf_issuers.py. listing = first NY trading day.
ISSUER_ETFS = {
    "NEAR": [{"ticker": "NRR", "issuer": "bitwise", "url": "https://nrretf.com/", "listing": "2026-09-29"}],
    "HYPE": [{"ticker": "THYP", "issuer": "21shares", "listing": "2026-05-12"},
             {"ticker": "BHYP", "issuer": "bitwise", "url": "https://www.bhypetf.com/", "listing": "2026-05-15"}],
}
COINMETRICS_ASSETS = ["btc", "eth"]
COINMETRICS_METRICS = ["PriceUSD", "CapMrktCurUSD", "CapMVRVCur", "AdrActCnt", "TxCnt", "SplyCur"]
DEFILLAMA_STABLES = {"ALL": None, "USDT": 1, "USDC": 2}   # name: DefiLlama stablecoin id
DERIBIT_DVOL = ["BTC", "ETH"]
DERIBIT_OPTIONS = ["BTC", "ETH"]          # hourly options snapshot (mdh/sources/options.py), builds forward from 2026-10-02
# Coinbase taker flow (mdh/sources/flows.py): 15m buy/sell buckets from public trades. ~25 calls/hour/product.
CB_TRADES_PRODUCTS = ["BTC", "ETH"]
CB_TRADES_BACKFILL_HOURS = 24             # first run only (~1,500 calls, ~10 min)
CB_TRADES_MAX_PAGES = 2000                # per product per run (safety cap)
# Exchange balances (mdh/sources/cex_reserves.py): DefiLlama CEX transparency slugs, largest first (2026-09-30).
# Coinbase, Kraken, Upbit, Bithumb publish no wallet list, so they are not here.
CEX_RESERVE_EXCHANGES = ["binance-cex", "okx", "bitfinex", "bybit", "robinhood", "gate", "bitget", "gemini", "mexc",
                         "deribit", "htx", "bitstamp", "kucoin", "crypto-com", "poloniex", "hashkey-exchange", "bitkub",
                         "swissborg", "osl-exchange", "bitmex", "bitvavo", "bingx", "backpack", "indodax", "korbit",
                         "nexo", "weex", "phemex", "bitunix", "coindcx"]
CEX_RESERVE_ALIASES = {"ETH": ["ETH", "WETH"]}     # DefiLlama labels native ETH as WETH on several chains
# NEAR Intents (mdh/sources/near.py): DefiLlama adapters for volume / fees / revenue + NEAR total supply from RPC
NEAR_INTENTS_SLUG = "near-intents"
NEAR_SUPPLY_START = "2024-01-01"                   # weekly supply points back to here on the first run

# ----------------------------------------------------------------------------- Coin pages (mdh/sources/binance_hist.py, mdh/indicators.py)
COIN_TABS = ["BTC", "ETH", "XRP", "SOL", "ZEC"]     # own tab each on the dashboard
COIN_MORE = ["DASH", "ZEN", "LDO", "ENA", "AAVE", "SUI", "UNI", "NEAR", "DOGE", "HYPE"]   # under "More coins"
COIN_PAGES = COIN_TABS + COIN_MORE                   # candles, MAs, MACD, RSI, funding, OI, CVD for each
# "Since" tab: every coin-page coin's move from one fixed moment (Avalon's XRP entry, 2:00 AM PDT Sep 18)
# PushPlus daily report (scripts/ops/push_etf_report.py): written for Avalon's dad and his holdings
PUSH_HOLDINGS = ["ETH", "SOL", "ZEC", "NEAR", "UNI"]
PUSH_SECTORS = {"隐私币": ["ZEC", "DASH", "ZEN"], "公链": ["ETH", "SOL", "NEAR", "SUI"],
                "DeFi": ["AAVE", "UNI", "LDO", "ENA"], "其他": ["XRP", "DOGE", "HYPE"]}
# FOMC meeting days (second day = decision, 2:00 PM New York), from federalreserve.gov/monetarypolicy/fomccalendars.htm
FOMC_DECISIONS = ["2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16", "2026-10-28",
                  "2026-12-09", "2027-01-27", "2027-03-17", "2027-04-28", "2027-06-09", "2027-07-28", "2027-09-15",
                  "2027-10-27", "2027-12-08"]
SINCE_BASE_UTC = "2026-09-18 09:00"
SINCE_LABEL = "Sep 18, 2:00 AM PDT"
COIN_HOURLY_START = "2023-01-01"                     # hourly klines kept from here (4h candles)
BINANCE_FUNDING_ASSETS = COIN_PAGES

# ----------------------------------------------------------------------------- Cross-exchange (mdh/sources/cex.py)
CEX_ASSETS = ["BTC", "ETH", "SOL", "XRP"]
CEX_SPOT_START = {"1h": "2024-08-01", "1d": "2015-01-01"}   # Coinbase candles
# coin-page coins that also trade on Coinbase (USD): candles for their Coinbase premium (rebound signals).
# Shorter history than the four majors to keep the first backfill light.
COINBASE_EXTRA = [c for c in COIN_PAGES if c not in CEX_ASSETS]
COINBASE_EXTRA_START = "2025-06-01"
HL_FUNDING_START = "2023-05-01"
# Hyperliquid perps: every coin-page coin (not just CEX_ASSETS). Funding history for the non-majors from here
HL_FUNDING_START_EXTRA = "2025-09-01"
# Hyperliquid spot pairs stored in cex_spot (venue='hyperliquid'); 1h keeps the last 5000 candles, 1d the full history
HL_SPOT = {"HYPE": "@107"}          # HYPE/USDC, Hyperliquid's main HYPE spot market
# coin pages whose Binance spot history is short: earlier candles come from this cex_spot venue (no taker split,
# so spot CVD is blank for those candles)
SPOT_FALLBACK = {"HYPE": ("hyperliquid", "Hyperliquid spot HYPE/USDC before Binance spot listed it (2026-09-24)")}
# sources that need a non-US connection; scripts/ops/run_update.sh runs them inside the VPN step
VPN_SOURCES = ["binance", "bybit", "binance_funding"]
