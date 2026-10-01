"""
Binance spot + USDT-M futures bars, OI, long/short ratios, funding (via parser.py, unchanged logic).

parser.py writes one CSV per symbol/interval into data/raw/binance/ (its idempotent format);
this wrapper runs it for settings.BINANCE_SYMBOLS and mirrors the CSVs into DuckDB
tables `binance_<interval>` (e.g. binance_1h) with a `symbol` column.

Note: api.binance.com / fapi.binance.com refuse US IPs (HTTP 451). History comes from the
data.binance.vision archive (not geo-blocked); live spot uses data-api.binance.vision. Live
futures for today (T-0) need a non-US connection (VPN) — otherwise that day is filled next run.
"""
import logging

from mdh import settings
from mdh.core import db

NAME = "binance"
RAW = settings.RAW_DIR / "binance"


def load_csvs(con):
    out = {}
    for interval in settings.BINANCE_INTERVALS:
        files = sorted(RAW.glob(f"*_{interval}.csv"))
        if not files:
            continue
        selects = " UNION ALL BY NAME ".join(
            f"SELECT '{f.name.split('_')[0].upper()}' AS symbol, * "
            f"FROM read_csv_auto('{f.as_posix()}', header=true, timestampformat='%Y-%m-%d %H:%M:%S')"
            for f in files)
        table = f"binance_{interval}"
        con.execute(f'CREATE OR REPLACE TABLE "{table}" AS SELECT * FROM ({selects}) ORDER BY symbol, "timestamp"')
        out[table] = con.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
    return out


def run(ctx, full=False, symbols=None, start=None):
    from .parser import BinanceMarketDataParser
    logging.getLogger("BinanceMarketDataParser").setLevel(logging.INFO)
    RAW.mkdir(parents=True, exist_ok=True)
    for interval in settings.BINANCE_INTERVALS:
        for sym in symbols or settings.BINANCE_SYMBOLS:
            p = BinanceMarketDataParser(sym, interval=interval, start_date=start or settings.BINANCE_START_OVERRIDES.get(sym, settings.BINANCE_START),
                                        output_dir=str(RAW))
            p.run(force_update=False)
    return load_csvs(ctx.con)
