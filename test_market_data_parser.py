#!/usr/bin/env python3
"""
Comprehensive Unit & Live Schema Verification Tests
===================================================
Verifies:
1. Non-destructive idempotent re-entry (combine_first validation).
2. Interval calculation & frequency parsing across 5m, 30m, 1h, 6h, 1d.
3. Meme coin futures symbol resolution.
4. Live Binance Vision archive schema integrity.
"""

import unittest
import io
import zipfile
import pandas as pd
import numpy as np
from market_data_parser import BinanceMarketDataParser


class TestBinanceMarketDataParser(unittest.TestCase):
    def setUp(self):
        self.parser = BinanceMarketDataParser("ZECUSDT", interval="30m", start_date="2026-06-24", end_date="2026-06-24")

    def test_idempotent_combine_first(self):
        """Verify that combining datasets preserves existing valid data and only fills null cells."""
        df_existing = pd.DataFrame({
            "timestamp": pd.date_range("2026-06-24", periods=3, freq="1h"),
            "spot_close": [415.0, np.nan, 417.0],
            "spot_volume": [100.0, 200.0, np.nan]
        }).set_index("timestamp")

        df_new = pd.DataFrame({
            "timestamp": pd.date_range("2026-06-24", periods=3, freq="1h"),
            "spot_close": [999.0, 416.0, 999.0],
            "spot_volume": [888.0, 888.0, 300.0]
        }).set_index("timestamp")

        res = df_existing.combine_first(df_new).reset_index()
        self.assertEqual(res.loc[0, "spot_close"], 415.0)
        self.assertEqual(res.loc[1, "spot_close"], 416.0)
        self.assertEqual(res.loc[2, "spot_volume"], 300.0)

    def test_multi_interval_calculations(self):
        """Verify periods per day across diverse intervals."""
        test_cases = {
            "5m": ("5min", 288),
            "15m": ("15min", 96),
            "30m": ("30min", 48),
            "1h": ("1h", 24),
            "6h": ("6h", 4),
            "1d": ("1D", 1)
        }
        for inv, (expected_freq, expected_periods) in test_cases.items():
            p = BinanceMarketDataParser("ZECUSDT", interval=inv)
            self.assertEqual(p.freq, expected_freq)
            self.assertEqual(p.periods_per_day, expected_periods)

    def test_symbol_resolution(self):
        """Verify automatic 1000-prefix assignment for meme coins."""
        pepe = BinanceMarketDataParser("PEPEUSDT")
        self.assertEqual(pepe.future_symbol, "1000PEPEUSDT")
        self.assertEqual(pepe._get_url("metrics", "2026-06-24"),
                         "https://data.binance.vision/data/futures/um/daily/metrics/1000PEPEUSDT/1000PEPEUSDT-metrics-2026-06-24.zip")

    def test_is_day_complete(self):
        """Test incomplete dataset detection."""
        # Missing rows (< expected tolerance)
        df_short = pd.DataFrame({
            "timestamp": pd.date_range("2026-06-24", periods=10, freq="30min"),
            "sum_open_interest": [100.0] * 10
        })
        self.assertFalse(self.parser._is_day_complete(df_short, pd.to_datetime("2026-06-24")))

        # Complete rows
        df_complete = pd.DataFrame({
            "timestamp": pd.date_range("2026-06-24", periods=48, freq="30min"),
            "sum_open_interest": [100.0] * 48,
            "count_toptrader_long_short_ratio": [0.75] * 48
        })
        self.assertTrue(self.parser._is_day_complete(df_complete, pd.to_datetime("2026-06-24")))


class TestLiveArchiveSchemas(unittest.TestCase):
    """
    Live verification against Binance Vision archives to confirm upstream compatibility.
    """
    def setUp(self):
        self.parser = BinanceMarketDataParser("ZECUSDT", interval="1h", start_date="2026-06-24", end_date="2026-06-24")
        self.test_date = "2026-06-24"

    def test_live_extraction(self):
        for d_type in self.parser.data_types:
            with self.subTest(data_type=d_type):
                url = self.parser._get_url(d_type, self.test_date)
                r = self.parser.session.get(url, timeout=15)
                if r.status_code != 200:
                    self.skipTest(f"Archive URL {url} HTTP {r.status_code}")
                df = self.parser.process_zip(r.content, d_type)
                self.assertIsNotNone(df)
                self.assertIn("timestamp", df.columns)
                self.assertFalse(df.empty)


if __name__ == "__main__":
    unittest.main(verbosity=2)
