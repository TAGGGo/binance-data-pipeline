# Binance Market Data Pipeline (`market_data_parser.py`)

A robust, re-entrant, and multi-interval market data ingestion tool designed to pull comprehensive data from [Binance Vision](https://data.binance.vision).

## Overview & Architecture

The script pulls and merges **6 daily market archives** per symbol into a unified, clean CSV file tailored for quantitative research and trading strategies:
1. **Spot Klines (`spot_klines`)**: Open, High, Low, Close, Volume, Quote Volume, Trade Count, Taker Buy Base/Quote Volumes.
2. **Futures UM Klines (`futures_klines`)**: USDT-Margined perpetual contract Klines.
3. **Open Interest & Positioning Ratios (`metrics`)**: Open Interest, Top Trader Long/Short Ratios, Global Account Long/Short Ratios, Taker Volume Ratios.
4. **Funding Rates (`premium_index`)**: Premium index OHLC and exact funding rates.
5. **Mark Price Klines (`mark_price`)**: Mark price OHLC.
6. **Index Price Klines (`index_price`)**: Underlying index price OHLC.

### Key Engineering Features
* **Non-destructive Idempotence**: Uses exact index matching (`pandas.DataFrame.combine_first`). Re-running the pipeline will **only fill missing days or null cells**, preserving existing non-null data.
* **Cross-Day Boundary Snapshot Matching**: Automatically fetches the tail of the previous day's metrics archive (`T-1`) to accurately populate bar open snapshots at `00:00:00`.
* **Multi-Interval Support**: Supports any standard kline interval (`1m`, `5m`, `15m`, `30m`, `1h`, `4h`, `6h`, `1d`). Outputs files using intuitive naming conventions: `symbol_interval.csv` (e.g. `zecusdt_30m.csv`, `zecusdt_1h.csv`).
* **Meme Coin Prefix Resolution**: Dynamically maps UM Futures symbols requiring a `1000` prefix (e.g. `PEPEUSDT` -> `1000PEPEUSDT`) while querying standard tickers on spot endpoints.

---

## Installation & Requirements

Ensure you have Python 3.8+ and standard data science libraries installed:
```bash
pip install requests urllib3 pandas numpy
```

---

## Usage & Command-Line Examples

### 1. Basic Single Symbol Sync (Default 5m Interval)
Sync historical data for ZECUSDT starting from September 1, 2025 up to today. This generates `zecusdt_5m.csv`:
```bash
python3 market_data_parser.py --symbols ZECUSDT --start-date 2025-09-01
```

### 2. Multi-Symbol & Multi-Interval Backfill
Pull 30-minute and 1-hour historical data for ZECUSDT, HYPEUSDT, and PEPEUSDT over a specific date range. This will create 6 files (`zecusdt_30m.csv`, `zecusdt_1h.csv`, `hypeusdt_30m.csv`, etc.):
```bash
python3 market_data_parser.py \
  --symbols ZECUSDT HYPEUSDT PEPEUSDT \
  --intervals 30m 1h \
  --start-date 2026-01-01 \
  --end-date 2026-06-25
```

### 3. Lightweight Daily & 4-Hour Macro Data for Portfolio Analysis
Fetch higher timeframe bars (`4h` and `1d`) into a dedicated `./data/macro` folder:
```bash
python3 market_data_parser.py \
  --symbols ZECUSDT BTCUSDT ETHUSDT \
  --intervals 4h 1d \
  --start-date 2024-01-01 \
  --output-dir ./data/macro
```

### 4. Scheduled Daily Incremental Update (Cron / CI Job)
Run without a start date or end date specified. If an existing CSV is present, the script checks existing dates, skips complete days instantaneously, and fetches only missing recent days:
```bash
python3 market_data_parser.py --symbols ZECUSDT --intervals 5m 1h --output-dir ./data
```

### 5. Force Overwrite / Re-parse Legacy CSVs
If you upgraded from an older version of the parser (e.g., expanding from 12 columns to the full 38-column schema) or suspect corrupted local records, use `--force` to re-download and rebuild existing dates:
```bash
python3 market_data_parser.py \
  --symbols ZECUSDT \
  --intervals 5m \
  --start-date 2026-06-01 \
  --end-date 2026-06-24 \
  --force
```

---

#### CLI Arguments Summary:
* `--symbols`: List of symbols to process (default: `ZECUSDT`).
* `--intervals`: Kline intervals to fetch (`1m`, `5m`, `15m`, `30m`, `1h`, `4h`, `6h`, `1d`). Default is `5m`.
* `--start-date`: Start date in `YYYY-MM-DD` format (default: `2025-09-01`).
* `--end-date`: End date in `YYYY-MM-DD` format (defaults to current date).
* `--output-dir`: Folder path where CSV files will be written (default: current directory `.`).
* `--force`: Force re-download and re-parse even if the date is already complete in the existing CSV.

---

### Python API Usage

You can also import and execute the parser within your trading systems:
```python
from market_data_parser import BinanceMarketDataParser

# Initialize parser for 1-hour ZECUSDT data
parser = BinanceMarketDataParser(
    symbol="ZECUSDT",
    interval="1h",
    start_date="2026-05-01",
    end_date="2026-06-24",
    output_dir="./data"
)

# Execute sync (returns combined DataFrame and execution summary stats)
df, stats = parser.run()
print(df.head())
print("Execution summary:", stats)
```

---

## Running Verification & Unit Tests

To verify pipeline logic, interval calculations, symbol resolution, and live compatibility with Binance Vision servers, run the test suite:
```bash
python3 test_market_data_parser.py
```
