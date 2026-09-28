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
    "api.nasdaq.com": 20,                  # unpublished; stay gentle
    "www.deribit.com": 60,                 # 20 req/s credit system
    "api.alternative.me": 20,
    "data-api.binance.vision": 600,        # weight 6000/min
    "data.binance.vision": 1200,           # static archive
    "cdn.cboe.com": 20,
}
# hard monthly ceilings (published quota minus a safety margin)
MONTHLY_QUOTA = {
    "openapi.sosovalue.com": 9000,         # 10,000 / month
    "api.sosovalue.xyz": 9000,
    "api.coingecko.com": 9000,             # Demo plan: 10,000 / month
}

# ----------------------------------------------------------------------------- Binance
BINANCE_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ZECUSDT", "DASHUSDT", "STRKUSDT"]
BINANCE_INTERVALS = ["1h"]
BINANCE_START = "2024-08-01"

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
]
TV_INTERVALS = ["1d", "1h"]   # daily = full history; hourly = rolling ~2 months, accumulated going forward

# ----------------------------------------------------------------------------- Crypto-wide
ETF_ASSETS = ["BTC", "ETH", "SOL", "XRP"]
COINMETRICS_ASSETS = ["btc", "eth"]
COINMETRICS_METRICS = ["PriceUSD", "CapMrktCurUSD", "CapRealUSD", "CapMVRVCur", "AdrActCnt", "TxCnt", "SplyCur"]
DEFILLAMA_STABLES = {"ALL": None, "USDT": 1, "USDC": 2}   # name: DefiLlama stablecoin id
DERIBIT_DVOL = ["BTC", "ETH"]
