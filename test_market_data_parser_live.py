#!/usr/bin/env python3
import unittest
from unittest.mock import patch, MagicMock
import pandas as pd
import numpy as np
import os
import shutil
from datetime import datetime, timedelta
from market_data_parser import BinanceMarketDataParser

class TestLiveFetchingAndBlending(unittest.TestCase):
    def setUp(self):
        self.test_dir = "./test_output_live"
        os.makedirs(self.test_dir, exist_ok=True)
        # Use a fixed date that will be considered "today" by the test mock
        self.today_str = "2026-07-09"
        self.today_dt = pd.to_datetime(self.today_str)
        
        self.parser = BinanceMarketDataParser(
            "ZECUSDT", 
            interval="1h", 
            start_date=self.today_str, 
            end_date=self.today_str, 
            output_dir=self.test_dir
        )

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def get_mock_kline(self, open_time, open_price, close_price):
        return [
            open_time,          # Open time
            str(open_price),    # Open
            str(max(open_price, close_price) + 1),  # High
            str(min(open_price, close_price) - 1),  # Low
            str(close_price),   # Close
            "1000.0",           # Volume
            open_time + 3599999,# Close time
            "100000.0",         # Quote volume
            100,                # Trades
            "500.0",            # Taker buy base
            "50000.0",          # Taker buy quote
            "0"
        ]

    @patch('market_data_parser.pd.Timestamp.utcnow')
    @patch('market_data_parser.requests.Session.get')
    def test_live_fetching_success(self, mock_get, mock_utcnow):
        # Mock utcnow to return our fixed "today"
        mock_utcnow.return_value = pd.Timestamp(self.today_str + " 12:00:00", tz='UTC')

        # Prepare mock responses for each data type
        mock_responses = {}
        
        # timestamps for 00:00 and 01:00
        ts0 = int(self.today_dt.value / 10**6)
        ts1 = ts0 + 3600000
        
        # Kline mocks (spot, futures, premium_index, mark_price, index_price)
        kline_data = [
            self.get_mock_kline(ts0, 100.0, 101.0),
            self.get_mock_kline(ts1, 101.0, 102.0)
        ]
        
        # Metrics mocks
        oi_data = [
            {"symbol":"ZECUSDT", "sumOpenInterest":"10000.0", "sumOpenInterestValue":"1000000.0", "timestamp":ts0},
            {"symbol":"ZECUSDT", "sumOpenInterest":"10100.0", "sumOpenInterestValue":"1010000.0", "timestamp":ts1}
        ]
        top_trader_acc_data = [
            {"symbol":"ZECUSDT", "longShortRatio":"1.5", "longAccount":"0.6", "shortAccount":"0.4", "timestamp":ts0},
            {"symbol":"ZECUSDT", "longShortRatio":"1.6", "longAccount":"0.615", "shortAccount":"0.385", "timestamp":ts1}
        ]
        top_trader_pos_data = [
            {"symbol":"ZECUSDT", "longShortRatio":"1.2", "longAccount":"0.545", "shortAccount":"0.455", "timestamp":ts0},
            {"symbol":"ZECUSDT", "longShortRatio":"1.3", "longAccount":"0.565", "shortAccount":"0.435", "timestamp":ts1}
        ]
        global_acc_data = [
            {"symbol":"ZECUSDT", "longShortRatio":"1.1", "longAccount":"0.524", "shortAccount":"0.476", "timestamp":ts0},
            {"symbol":"ZECUSDT", "longShortRatio":"1.15", "longAccount":"0.535", "shortAccount":"0.465", "timestamp":ts1}
        ]
        taker_ratio_data = [
            {"buySellRatio":"1.05", "buyVol":"512.0", "sellVol":"488.0", "timestamp":ts0},
            {"buySellRatio":"1.08", "buyVol":"520.0", "sellVol":"480.0", "timestamp":ts1}
        ]

        def side_effect(url, *args, **kwargs):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            
            if "api.binance.com/api/v3/klines" in url:
                mock_resp.json.return_value = kline_data
            elif "fapi.binance.com/fapi/v1/klines" in url:
                mock_resp.json.return_value = kline_data
            elif "premiumIndexKlines" in url:
                mock_resp.json.return_value = kline_data
            elif "markPriceKlines" in url:
                mock_resp.json.return_value = kline_data
            elif "indexPriceKlines" in url:
                mock_resp.json.return_value = kline_data
            elif "openInterestHist" in url:
                mock_resp.json.return_value = oi_data
            elif "topLongShortAccountRatio" in url:
                mock_resp.json.return_value = top_trader_acc_data
            elif "topLongShortPositionRatio" in url:
                mock_resp.json.return_value = top_trader_pos_data
            elif "globalLongShortAccountRatio" in url:
                mock_resp.json.return_value = global_acc_data
            elif "takerlongshortRatio" in url:
                mock_resp.json.return_value = taker_ratio_data
            else:
                mock_resp.status_code = 404
                
            return mock_resp

        mock_get.side_effect = side_effect

        # Run parser
        df, stats = self.parser.run()

        self.assertEqual(stats["days_fetched_and_merged"], 1)
        self.assertFalse(df.empty)
        
        # Verify columns exist and are populated
        self.assertIn("spot_open", df.columns)
        self.assertIn("futures_open", df.columns)
        self.assertIn("sum_open_interest", df.columns)
        self.assertIn("funding_rate", df.columns) # mapped from premiumIndexKlines close
        
        # Verify values at 00:00:00
        row0 = df[df['timestamp'] == self.today_dt].iloc[0]
        self.assertEqual(row0['spot_open'], 100.0)
        self.assertEqual(row0['futures_open'], 100.0)
        self.assertEqual(row0['sum_open_interest'], 10000.0)
        self.assertEqual(row0['funding_rate'], 101.0) # close of mock kline is 101
        self.assertEqual(row0['count_toptrader_long_short_ratio'], 1.5)
        self.assertEqual(row0['sum_toptrader_long_short_ratio'], 1.2)
        self.assertEqual(row0['count_long_short_ratio'], 1.1)
        self.assertEqual(row0['sum_taker_long_short_vol_ratio'], 1.05)

    @patch('market_data_parser.pd.Timestamp.utcnow')
    @patch('market_data_parser.requests.Session.get')
    def test_live_fetching_geo_restricted_fallback(self, mock_get, mock_utcnow):
        mock_utcnow.return_value = pd.Timestamp(self.today_str + " 12:00:00", tz='UTC')

        # Simulate geo-restriction (HTTP 451)
        mock_resp = MagicMock()
        mock_resp.status_code = 451
        mock_resp.text = "Service unavailable from a restricted location"
        
        from requests.exceptions import HTTPError
        mock_resp.raise_for_status.side_effect = HTTPError("451 Client Error")
        
        mock_get.return_value = mock_resp

        # Run parser
        df, stats = self.parser.run()

        # Should skip today because live API failed
        self.assertEqual(stats["days_fetched_and_merged"], 0)
        self.assertTrue(df.empty or not os.path.exists(self.parser.file_path))

    @patch('market_data_parser.pd.Timestamp.utcnow')
    @patch('market_data_parser.requests.Session.get')
    def test_live_data_overwrites_existing_today(self, mock_get, mock_utcnow):
        mock_utcnow.return_value = pd.Timestamp(self.today_str + " 12:00:00", tz='UTC')

        # 1. Create existing CSV with incomplete today's data (e.g. only 1 row with old values)
        existing_df = pd.DataFrame({
            "timestamp": [self.today_dt],
            "spot_open": [999.0], # Old/incomplete value
            "spot_close": [999.0]
        })
        existing_df.to_csv(self.parser.file_path, index=False)

        # 2. Setup mock live API to return new data
        ts0 = int(self.today_dt.value / 10**6)
        kline_data = [self.get_mock_kline(ts0, 100.0, 101.0)]
        
        def side_effect(url, *args, **kwargs):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            if "klines" in url:
                mock_resp.json.return_value = kline_data
            else:
                mock_resp.json.return_value = [] # Empty for metrics to simplify
            return mock_resp
        mock_get.side_effect = side_effect

        # 3. Run parser
        df, stats = self.parser.run()

        # 4. Verify that the 999.0 value was overwritten by 100.0
        df_result = pd.read_csv(self.parser.file_path)
        self.assertEqual(df_result.loc[0, "spot_open"], 100.0)
        self.assertNotEqual(df_result.loc[0, "spot_open"], 999.0)

    @patch('market_data_parser.pd.Timestamp.utcnow')
    @patch('market_data_parser.requests.Session.get')
    def test_past_day_archive_missing_live_fallback(self, mock_get, mock_utcnow):
        # Set today to tomorrow so that the test date (today_str) is considered "yesterday" (past day)
        tomorrow_str = (self.today_dt + timedelta(days=1)).strftime("%Y-%m-%d")
        mock_utcnow.return_value = pd.Timestamp(tomorrow_str + " 12:00:00", tz='UTC')

        # We configure the parser to run for self.today_str (which is now "yesterday")
        parser_past = BinanceMarketDataParser(
            "ZECUSDT", 
            interval="1h", 
            start_date=self.today_str, 
            end_date=self.today_str, 
            output_dir=self.test_dir
        )

        # Setup mock:
        # - Archive URLs (containing 'data.binance.vision') return 404
        # - Live API URLs return 200 with data
        ts0 = int(self.today_dt.value / 10**6)
        kline_data = [self.get_mock_kline(ts0, 200.0, 201.0)]

        def side_effect(url, *args, **kwargs):
            mock_resp = MagicMock()
            if "data.binance.vision" in url:
                mock_resp.status_code = 404
            else:
                mock_resp.status_code = 200
                if "klines" in url:
                    mock_resp.json.return_value = kline_data
                else:
                    mock_resp.json.return_value = [] # Empty for metrics to simplify
            return mock_resp
            
        mock_get.side_effect = side_effect

        # Run parser
        df, stats = parser_past.run()

        # Should successfully fallback and merge
        self.assertEqual(stats["days_fetched_and_merged"], 1)
        self.assertFalse(df.empty)
        
        df_result = pd.read_csv(parser_past.file_path)
        self.assertEqual(df_result.loc[0, "spot_open"], 200.0)

if __name__ == "__main__":
    unittest.main()
