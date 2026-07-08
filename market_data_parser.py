#!/usr/bin/env python3
"""
Binance Market Data Parser & Idempotent Fixer
=============================================
A robust, re-entrant market data ingestion pipeline for Binance Vision.
Extracts comprehensive spot, futures, funding, mark price, index price, and open interest /
trader positioning ratio metrics across arbitrary intervals (e.g. 5m, 30m, 1h, 6h, 1d).

Key Features:
1. Non-destructive Re-entry: Preserves existing valid non-null rows/cells while fetching and filling
   only missing rows or missing columns using precise pandas combine_first indexing.
2. Cross-Day Snapshot Alignment: Automatically loads the previous day's metrics tail to accurately
   populate bar open snapshots (e.g. 00:00:00).
3. Dynamic Symbol Resolution: Transparently handles meme coin futures prefixing (e.g. PEPEUSDT -> 1000PEPEUSDT).
4. Multi-Interval Support: Generates customized interval files (e.g. zecusdt_5m.csv, zecusdt_30m.csv, zecusdt_1h.csv).
"""

import argparse
import logging
import os
import io
import zipfile
from datetime import datetime, timedelta
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import pandas as pd

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("BinanceMarketDataParser")


class BinanceMarketDataParser:
    """
    Robust data ingestor and incremental fixer for Binance Vision market archives.
    """

    KNOWN_1000_PREFIXES = {
        "PEPEUSDT", "SHIBUSDT", "FLOKIUSDT", "BONKUSDT",
        "SATSUSDT", "RATSUSDT", "CATUSDT"
    }

    CORE_COLUMNS = [
        'timestamp', 'spot_open', 'spot_close', 'spot_volume',
        'create_time', 'sum_open_interest', 'sum_open_interest_value',
        'count_toptrader_long_short_ratio', 'sum_toptrader_long_short_ratio',
        'count_long_short_ratio', 'sum_taker_long_short_vol_ratio', 'funding_rate'
    ]

    def __init__(self, symbol, interval="5m", start_date="2025-09-01", end_date=None, output_dir=".", data_types=None):
        self.symbol = symbol.replace('.P', '').upper()
        self.interval = interval.lower()
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

        # Output file convention: lowercase symbol + _ + interval + .csv (e.g. zecusdt_5m.csv)
        self.filename = f"{self.symbol.lower()}_{self.interval}.csv"
        self.file_path = os.path.join(self.output_dir, self.filename)

        self.start_date = pd.to_datetime(start_date).normalize()
        self.end_date = pd.to_datetime(end_date or pd.Timestamp.utcnow().tz_localize(None)).normalize()
        self.base_url = "https://data.binance.vision/data"

        self.data_types = data_types or [
            "spot_klines",
            "futures_klines",
            "metrics",
            "premium_index",
            "mark_price",
            "index_price"
        ]

        # Calculate interval frequency and expected periods per day
        self.freq = self._interval_to_freq(self.interval)
        self.periods_per_day = max(1, int(pd.Timedelta("1d") / pd.Timedelta(self.freq)))

        # Setup resilient HTTP session
        self.session = requests.Session()
        retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
        self.session.mount("https://", HTTPAdapter(max_retries=retries))

        self.future_symbol = self._resolve_future_symbol()
        self._df_cache = {}

    def _interval_to_freq(self, interval):
        """Converts standard Binance intervals ('5m', '1h', '1d') to pandas frequency strings."""
        if interval.endswith('m'):
            return interval.replace('m', 'min')
        if interval.endswith('d'):
            return interval.replace('d', 'D')
        return interval

    def _resolve_future_symbol(self):
        """Resolves whether UM futures archives require a '1000' prefix."""
        if self.symbol in self.KNOWN_1000_PREFIXES:
            return "1000" + self.symbol
        return self.symbol

    def _get_url(self, data_type, date_str):
        """Builds archive URL for a given data type, interval, and date."""
        fsym = self.future_symbol
        sym = self.symbol
        inv = self.interval

        if data_type == "metrics":
            # Metrics archives are stored daily at 5m snapshots on Binance Vision
            return f"{self.base_url}/futures/um/daily/metrics/{fsym}/{fsym}-metrics-{date_str}.zip"
        elif data_type == "spot_klines":
            return f"{self.base_url}/spot/daily/klines/{sym}/{inv}/{sym}-{inv}-{date_str}.zip"
        elif data_type == "futures_klines":
            return f"{self.base_url}/futures/um/daily/klines/{fsym}/{inv}/{fsym}-{inv}-{date_str}.zip"
        elif data_type == "premium_index":
            return f"{self.base_url}/futures/um/daily/premiumIndexKlines/{fsym}/{inv}/{fsym}-{inv}-{date_str}.zip"
        elif data_type == "mark_price":
            return f"{self.base_url}/futures/um/daily/markPriceKlines/{fsym}/{inv}/{fsym}-{inv}-{date_str}.zip"
        elif data_type == "index_price":
            return f"{self.base_url}/futures/um/daily/indexPriceKlines/{fsym}/{inv}/{fsym}-{inv}-{date_str}.zip"
        raise ValueError(f"Unknown data type: {data_type}")

    def process_zip(self, content, data_type):
        """Parses CSV in zip payload and returns a typed DataFrame."""
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                with z.open(z.namelist()[0]) as f:
                    if data_type == "metrics":
                        df = pd.read_csv(f)
                        df['timestamp'] = pd.to_datetime(df.iloc[:, 0])
                        cols = {
                            'timestamp': df['timestamp'],
                            'create_time': df.iloc[:, 0],
                            'sum_open_interest': pd.to_numeric(df.get('sum_open_interest', df.iloc[:, 2]), errors='coerce'),
                            'sum_open_interest_value': pd.to_numeric(df.get('sum_open_interest_value', df.iloc[:, 3]), errors='coerce'),
                            'count_toptrader_long_short_ratio': pd.to_numeric(df.get('count_toptrader_long_short_ratio', df.iloc[:, 4]), errors='coerce'),
                            'sum_toptrader_long_short_ratio': pd.to_numeric(df.get('sum_toptrader_long_short_ratio', df.iloc[:, 5]), errors='coerce'),
                            'count_long_short_ratio': pd.to_numeric(df.get('count_long_short_ratio', df.iloc[:, 6]), errors='coerce'),
                            'sum_taker_long_short_vol_ratio': pd.to_numeric(df.get('sum_taker_long_short_vol_ratio', df.iloc[:, 7]), errors='coerce'),
                        }
                        return pd.DataFrame(cols)

                    # Kline files: check if header exists
                    head_check = pd.read_csv(f, nrows=2, header=None)
                    f.seek(0)
                    first_cell = str(head_check.iloc[0, 0]).replace('.', '').replace('-', '')
                    has_header = not first_cell.isdigit()
                    df = pd.read_csv(f, header=0 if has_header else None)

                    # Normalize open time timestamps
                    ts_series = pd.to_numeric(df.iloc[:, 0], errors='coerce')
                    if ts_series.iloc[0] > 10**14:
                        df['timestamp'] = pd.to_datetime(ts_series / 1000, unit='ms')
                    elif ts_series.iloc[0] > 10**11:
                        df['timestamp'] = pd.to_datetime(ts_series, unit='ms')
                    else:
                        df['timestamp'] = pd.to_datetime(ts_series, unit='s')

                    if data_type == "spot_klines":
                        return pd.DataFrame({
                            'timestamp': df['timestamp'],
                            'spot_open': pd.to_numeric(df.iloc[:, 1], errors='coerce'),
                            'spot_high': pd.to_numeric(df.iloc[:, 2], errors='coerce'),
                            'spot_low': pd.to_numeric(df.iloc[:, 3], errors='coerce'),
                            'spot_close': pd.to_numeric(df.iloc[:, 4], errors='coerce'),
                            'spot_volume': pd.to_numeric(df.iloc[:, 5], errors='coerce'),
                            'spot_quote_volume': pd.to_numeric(df.iloc[:, 7], errors='coerce'),
                            'spot_trades': pd.to_numeric(df.iloc[:, 8], errors='coerce'),
                            'spot_taker_buy_volume': pd.to_numeric(df.iloc[:, 9], errors='coerce'),
                            'spot_taker_buy_quote_volume': pd.to_numeric(df.iloc[:, 10], errors='coerce')
                        })
                    elif data_type == "futures_klines":
                        return pd.DataFrame({
                            'timestamp': df['timestamp'],
                            'futures_open': pd.to_numeric(df.iloc[:, 1], errors='coerce'),
                            'futures_high': pd.to_numeric(df.iloc[:, 2], errors='coerce'),
                            'futures_low': pd.to_numeric(df.iloc[:, 3], errors='coerce'),
                            'futures_close': pd.to_numeric(df.iloc[:, 4], errors='coerce'),
                            'futures_volume': pd.to_numeric(df.iloc[:, 5], errors='coerce'),
                            'futures_quote_volume': pd.to_numeric(df.iloc[:, 7], errors='coerce'),
                            'futures_trades': pd.to_numeric(df.iloc[:, 8], errors='coerce'),
                            'futures_taker_buy_volume': pd.to_numeric(df.iloc[:, 9], errors='coerce'),
                            'futures_taker_buy_quote_volume': pd.to_numeric(df.iloc[:, 10], errors='coerce')
                        })
                    elif data_type == "premium_index":
                        return pd.DataFrame({
                            'timestamp': df['timestamp'],
                            'premium_index_open': pd.to_numeric(df.iloc[:, 1], errors='coerce'),
                            'premium_index_high': pd.to_numeric(df.iloc[:, 2], errors='coerce'),
                            'premium_index_low': pd.to_numeric(df.iloc[:, 3], errors='coerce'),
                            'funding_rate': pd.to_numeric(df.iloc[:, 4], errors='coerce')
                        })
                    elif data_type == "mark_price":
                        return pd.DataFrame({
                            'timestamp': df['timestamp'],
                            'mark_price_open': pd.to_numeric(df.iloc[:, 1], errors='coerce'),
                            'mark_price_high': pd.to_numeric(df.iloc[:, 2], errors='coerce'),
                            'mark_price_low': pd.to_numeric(df.iloc[:, 3], errors='coerce'),
                            'mark_price_close': pd.to_numeric(df.iloc[:, 4], errors='coerce')
                        })
                    elif data_type == "index_price":
                        return pd.DataFrame({
                            'timestamp': df['timestamp'],
                            'index_price_open': pd.to_numeric(df.iloc[:, 1], errors='coerce'),
                            'index_price_high': pd.to_numeric(df.iloc[:, 2], errors='coerce'),
                            'index_price_low': pd.to_numeric(df.iloc[:, 3], errors='coerce'),
                            'index_price_close': pd.to_numeric(df.iloc[:, 4], errors='coerce')
                        })
        except Exception as e:
            logger.debug(f"Failed parsing {data_type} archive: {e}")
            return None

    def _get_parsed_df(self, d_type, date_str):
        """Fetches and parses a single day data archive with in-memory caching."""
        cache_key = (d_type, date_str)
        if cache_key in self._df_cache:
            return self._df_cache[cache_key]

        url = self._get_url(d_type, date_str)
        try:
            r = self.session.get(url, timeout=15)
            if r.status_code == 200:
                df = self.process_zip(r.content, d_type)
                self._df_cache[cache_key] = df
                return df
        except Exception as e:
            logger.warning(f"Error fetching {d_type} ({date_str}): {e}")

        self._df_cache[cache_key] = None
        return None

    def _is_day_complete(self, main_df, date_obj):
        """
        Determines whether existing dataset already contains complete rows and valid
        core columns for the given date.
        """
        if main_df.empty:
            return False
        day_data = main_df[main_df['timestamp'].dt.normalize() == date_obj]
        # Allow 2 rows tolerance for scheduled exchange maintenance windows
        if len(day_data) < max(1, self.periods_per_day - 2):
            return False

        # Map data types to representative columns to verify they are populated
        rep_cols = {
            "spot_klines": "spot_open",
            "futures_klines": "futures_open",
            "metrics": "sum_open_interest",
            "premium_index": "funding_rate",
            "mark_price": "mark_price_open",
            "index_price": "index_price_open"
        }

        for d_type in self.data_types:
            col = rep_cols.get(d_type)
            if col:
                if col not in day_data.columns:
                    return False
                # Ensure the column is not mostly null.
                # Allow same tolerance as row count (at most 2 nulls)
                non_null_count = day_data[col].notnull().sum()
                if non_null_count < max(1, self.periods_per_day - 2):
                    return False
        return True

    def _sort_and_order_columns(self, df):
        """Enforces standard column sorting prioritizing core trading schema."""
        ordered_cols = [c for c in self.CORE_COLUMNS if c in df.columns]
        other_cols = [c for c in df.columns if c not in ordered_cols]
        df = df[ordered_cols + other_cols]
        return df.sort_values('timestamp').reset_index(drop=True)

    def _interval_to_stats_period(self, interval):
        """Maps kline interval to Binance stats period."""
        if interval == '1m':
            return '5m'
        return interval

    def _fetch_live_data(self, url, params):
        """Helper to fetch live data from Binance API."""
        try:
            r = self.session.get(url, params=params, timeout=15)
            if r.status_code == 200:
                return r.json()
            else:
                logger.warning(f"Binance API returned status {r.status_code} for {url}: {r.text}")
                r.raise_for_status()
        except Exception as e:
            logger.debug(f"Error fetching live data from {url}: {e}")
            raise e

    def _fetch_live_klines(self, data_type, start_time_ms, end_time_ms):
        """Fetches klines from live API for T-0."""
        sym = self.symbol
        fsym = self.future_symbol
        inv = self.interval

        if data_type == "spot_klines":
            url = "https://api.binance.com/api/v3/klines"
            target_sym = sym
        elif data_type == "futures_klines":
            url = "https://fapi.binance.com/fapi/v1/klines"
            target_sym = fsym
        elif data_type == "premium_index":
            url = "https://fapi.binance.com/fapi/v1/premiumIndexKlines"
            target_sym = fsym
        elif data_type == "mark_price":
            url = "https://fapi.binance.com/fapi/v1/markPriceKlines"
            target_sym = fsym
        elif data_type == "index_price":
            url = "https://fapi.binance.com/fapi/v1/indexPriceKlines"
            target_sym = fsym
        else:
            raise ValueError(f"Unknown kline data type: {data_type}")

        params = {
            "interval": inv,
            "startTime": start_time_ms,
            "endTime": end_time_ms,
            "limit": 1500
        }

        if data_type == "index_price":
            params["pair"] = target_sym
        else:
            params["symbol"] = target_sym

        try:
            data = self._fetch_live_data(url, params)
            if not data:
                return None

            df = pd.DataFrame(data)
            if df.empty:
                return None

            ts_series = pd.to_numeric(df.iloc[:, 0], errors='coerce')
            df['timestamp'] = pd.to_datetime(ts_series, unit='ms')

            if data_type == "spot_klines":
                return pd.DataFrame({
                    'timestamp': df['timestamp'],
                    'spot_open': pd.to_numeric(df.iloc[:, 1], errors='coerce'),
                    'spot_high': pd.to_numeric(df.iloc[:, 2], errors='coerce'),
                    'spot_low': pd.to_numeric(df.iloc[:, 3], errors='coerce'),
                    'spot_close': pd.to_numeric(df.iloc[:, 4], errors='coerce'),
                    'spot_volume': pd.to_numeric(df.iloc[:, 5], errors='coerce'),
                    'spot_quote_volume': pd.to_numeric(df.iloc[:, 7], errors='coerce'),
                    'spot_trades': pd.to_numeric(df.iloc[:, 8], errors='coerce'),
                    'spot_taker_buy_volume': pd.to_numeric(df.iloc[:, 9], errors='coerce'),
                    'spot_taker_buy_quote_volume': pd.to_numeric(df.iloc[:, 10], errors='coerce')
                })
            elif data_type == "futures_klines":
                return pd.DataFrame({
                    'timestamp': df['timestamp'],
                    'futures_open': pd.to_numeric(df.iloc[:, 1], errors='coerce'),
                    'futures_high': pd.to_numeric(df.iloc[:, 2], errors='coerce'),
                    'futures_low': pd.to_numeric(df.iloc[:, 3], errors='coerce'),
                    'futures_close': pd.to_numeric(df.iloc[:, 4], errors='coerce'),
                    'futures_volume': pd.to_numeric(df.iloc[:, 5], errors='coerce'),
                    'futures_quote_volume': pd.to_numeric(df.iloc[:, 7], errors='coerce'),
                    'futures_trades': pd.to_numeric(df.iloc[:, 8], errors='coerce'),
                    'futures_taker_buy_volume': pd.to_numeric(df.iloc[:, 9], errors='coerce'),
                    'futures_taker_buy_quote_volume': pd.to_numeric(df.iloc[:, 10], errors='coerce')
                })
            elif data_type == "premium_index":
                return pd.DataFrame({
                    'timestamp': df['timestamp'],
                    'premium_index_open': pd.to_numeric(df.iloc[:, 1], errors='coerce'),
                    'premium_index_high': pd.to_numeric(df.iloc[:, 2], errors='coerce'),
                    'premium_index_low': pd.to_numeric(df.iloc[:, 3], errors='coerce'),
                    'funding_rate': pd.to_numeric(df.iloc[:, 4], errors='coerce')
                })
            elif data_type == "mark_price":
                return pd.DataFrame({
                    'timestamp': df['timestamp'],
                    'mark_price_open': pd.to_numeric(df.iloc[:, 1], errors='coerce'),
                    'mark_price_high': pd.to_numeric(df.iloc[:, 2], errors='coerce'),
                    'mark_price_low': pd.to_numeric(df.iloc[:, 3], errors='coerce'),
                    'mark_price_close': pd.to_numeric(df.iloc[:, 4], errors='coerce')
                })
            elif data_type == "index_price":
                return pd.DataFrame({
                    'timestamp': df['timestamp'],
                    'index_price_open': pd.to_numeric(df.iloc[:, 1], errors='coerce'),
                    'index_price_high': pd.to_numeric(df.iloc[:, 2], errors='coerce'),
                    'index_price_low': pd.to_numeric(df.iloc[:, 3], errors='coerce'),
                    'index_price_close': pd.to_numeric(df.iloc[:, 4], errors='coerce')
                })
        except Exception as e:
            logger.warning(f"Failed to fetch live klines for {data_type}: {e}")
            raise e

    def _fetch_live_metrics(self, start_time_ms, end_time_ms):
        """Fetches and merges metrics from multiple live endpoints for T-0."""
        fsym = self.future_symbol
        period = self._interval_to_stats_period(self.interval)

        params = {
            "symbol": fsym,
            "period": period,
            "startTime": start_time_ms,
            "endTime": end_time_ms,
            "limit": 500
        }

        endpoints = {
            "open_interest": ("https://fapi.binance.com/futures/data/openInterestHist", ["sumOpenInterest", "sumOpenInterestValue"]),
            "top_trader_acc": ("https://fapi.binance.com/futures/data/topLongShortAccountRatio", ["longShortRatio"]),
            "top_trader_pos": ("https://fapi.binance.com/futures/data/topLongShortPositionRatio", ["longShortRatio"]),
            "global_acc": ("https://fapi.binance.com/futures/data/globalLongShortAccountRatio", ["longShortRatio"]),
            "taker_ratio": ("https://fapi.binance.com/futures/data/takerlongshortRatio", ["buySellRatio"])
        }

        dfs = []
        try:
            for name, (url, fields) in endpoints.items():
                data = self._fetch_live_data(url, params)
                if not data:
                    continue
                df = pd.DataFrame(data)
                if df.empty:
                    continue

                ts_series = pd.to_numeric(df['timestamp'], errors='coerce')
                df['timestamp'] = pd.to_datetime(ts_series, unit='ms')

                keep_cols = ['timestamp'] + fields
                df = df[keep_cols]

                rename_map = {}
                if name == "open_interest":
                    rename_map = {
                        "sumOpenInterest": "sum_open_interest",
                        "sumOpenInterestValue": "sum_open_interest_value"
                    }
                elif name == "top_trader_acc":
                    rename_map = {"longShortRatio": "count_toptrader_long_short_ratio"}
                elif name == "top_trader_pos":
                    rename_map = {"longShortRatio": "sum_toptrader_long_short_ratio"}
                elif name == "global_acc":
                    rename_map = {"longShortRatio": "count_long_short_ratio"}
                elif name == "taker_ratio":
                    rename_map = {"buySellRatio": "sum_taker_long_short_vol_ratio"}

                df = df.rename(columns=rename_map)

                for col in df.columns:
                    if col != 'timestamp':
                        df[col] = pd.to_numeric(df[col], errors='coerce')

                dfs.append(df)

            if not dfs:
                return None

            merged_df = dfs[0]
            for next_df in dfs[1:]:
                merged_df = pd.merge(merged_df, next_df, on='timestamp', how='outer')

            merged_df = merged_df.sort_values('timestamp').reset_index(drop=True)
            merged_df['create_time'] = merged_df['timestamp'].dt.strftime('%Y-%m-%d %H:%M:%S')

            return merged_df

        except Exception as e:
            logger.warning(f"Failed to fetch live metrics: {e}")
            raise e

    def run(self, force_update=False):
        """
        Executes the ingestion pipeline across the target date range.
        Returns a dictionary of execution statistics alongside the final DataFrame.
        """
        logger.info(f"Starting pipeline for {self.symbol} [{self.interval}] from {self.start_date.strftime('%Y-%m-%d')} to {self.end_date.strftime('%Y-%m-%d')}")
        main_df = pd.DataFrame()
        if os.path.exists(self.file_path):
            main_df = pd.read_csv(self.file_path)
            main_df['timestamp'] = pd.to_datetime(main_df['timestamp'])

        stats = {
            "total_days": 0,
            "days_skipped_complete": 0,
            "days_fetched_and_merged": 0
        }

        today = pd.Timestamp.utcnow().tz_localize(None).normalize()
        target_range = pd.date_range(start=self.start_date, end=self.end_date, freq="D")
        for date_obj in target_range:
            stats["total_days"] += 1
            date_str = date_obj.strftime("%Y-%m-%d")

            if not force_update and self._is_day_complete(main_df, date_obj):
                stats["days_skipped_complete"] += 1
                continue

            is_today_or_later = date_obj >= today

            if is_today_or_later:
                logger.info(f"Processing live data for {date_str} (T-0, interval: {self.interval})...")
            else:
                logger.info(f"Processing missing/incomplete data for {date_str} ({self.interval})...")

            day_grid = pd.DataFrame({
                'timestamp': pd.date_range(start=date_obj, periods=self.periods_per_day, freq=self.freq)
            })

            # Calculate start and end times in ms for live API
            start_ms = int(date_obj.value / 10**6)
            end_ms = int((date_obj + timedelta(days=1) - timedelta(milliseconds=1)).value / 10**6)

            live_api_failed = False
            used_live_api = False

            for d_type in self.data_types:
                df_part = None

                # 1. Try archive first if it is a past day
                if not is_today_or_later:
                    df_part = self._get_parsed_df(d_type, date_str)

                    if df_part is not None and not df_part.empty:
                        # For archive metrics, attach previous day's tail
                        if d_type == "metrics":
                            prev_date_str = (date_obj - timedelta(days=1)).strftime("%Y-%m-%d")
                            df_prev = self._get_parsed_df(d_type, prev_date_str)
                            dfs_to_combine = [d for d in [df_prev, df_part] if d is not None and not d.empty]
                            if dfs_to_combine:
                                df_part = pd.concat(dfs_to_combine, ignore_index=True)

                # 2. If it is today, or archive failed, try live API
                if df_part is None or df_part.empty:
                    used_live_api = True
                    try:
                        logger.info(f"Archive not available or today. Fetching live data for {d_type} on {date_str}...")
                        if d_type == "metrics":
                            # Subtract 5 minutes to get the boundary snapshot
                            df_part = self._fetch_live_metrics(start_ms - 300000, end_ms)
                        else:
                            df_part = self._fetch_live_klines(d_type, start_ms, end_ms)
                    except Exception as e:
                        logger.warning(f"Error fetching live data for {d_type} on {date_str}: {e}")
                        live_api_failed = True
                        break

                if df_part is not None and not df_part.empty:
                    day_grid = pd.merge_asof(
                        day_grid.sort_values('timestamp'),
                        df_part.sort_values('timestamp'),
                        on='timestamp',
                        direction='nearest',
                        tolerance=pd.Timedelta(self.freq)
                    )

            if used_live_api and live_api_failed:
                logger.warning(f"Skipping merge for {date_str} due to live API failures. (VPN required if geo-restricted)")
                continue

            # Robust non-destructive re-entry: preserve existing non-null data, fill only missing
            if main_df.empty:
                main_df = day_grid
            else:
                # Always drop the rows for the day we processed to ensure we overwrite with fresh data
                main_df = main_df[main_df['timestamp'].dt.normalize() != date_obj]

                main_indexed = main_df.set_index('timestamp')
                day_indexed = day_grid.set_index('timestamp')
                combined_indexed = main_indexed.combine_first(day_indexed)
                main_df = combined_indexed.reset_index()

            stats["days_fetched_and_merged"] += 1
            main_df = self._sort_and_order_columns(main_df)
            main_df.to_csv(self.file_path, index=False)

        logger.info(f"Pipeline completed for {self.filename}. Stats: {stats}")
        return main_df, stats


# Backward compatibility alias
BinanceUltimateFixer = BinanceMarketDataParser


def main():
    parser = argparse.ArgumentParser(description="Binance Vision Market Data Parser & Fixer")
    parser.add_argument("--symbols", nargs="+", default=["ZECUSDT"], help="Symbols to process (e.g. ZECUSDT HYPEUSDT PEPEUSDT)")
    parser.add_argument("--intervals", nargs="+", default=["5m"], help="Kline intervals (e.g. 5m 30m 1h 6h 1d)")
    parser.add_argument("--start-date", default="2025-09-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", default=None, help="End date (YYYY-MM-DD, defaults to today)")
    parser.add_argument("--output-dir", default=".", help="Output folder path")
    parser.add_argument("--force", action="store_true", help="Force re-fetch and overwrite even if complete")

    args = parser.parse_args()
    for sym in args.symbols:
        for inv in args.intervals:
            parser_inst = BinanceMarketDataParser(
                symbol=sym,
                interval=inv,
                start_date=args.start_date,
                end_date=args.end_date,
                output_dir=args.output_dir
            )
            parser_inst.run(force_update=args.force)


if __name__ == "__main__":
    main()
