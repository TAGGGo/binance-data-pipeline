#!/usr/bin/env python3
"""Backwards-compatible entry point. The parser now lives in mdh/sources/binance/parser.py.

Old usage keeps working, e.g.:
    python3 market_data_parser.py --symbols BTCUSDT --intervals 1h --start-date 2024-08-01
(Default output dir is now data/raw/binance so the hub picks the files up.)
"""
import sys

from mdh import settings
from mdh.sources.binance.parser import BinanceMarketDataParser, main  # noqa: F401  (re-export)

if __name__ == "__main__":
    if "--output-dir" not in sys.argv:
        sys.argv += ["--output-dir", str(settings.RAW_DIR / "binance")]
    main()
