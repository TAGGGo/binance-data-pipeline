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
PARSER_WORKERS = 6


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
    """Each symbol's parser writes only its own CSV, so symbols run in parallel (PARSER_WORKERS threads);
    the DuckDB mirror is rebuilt afterwards on this thread."""
    from concurrent.futures import ThreadPoolExecutor
    from .parser import BinanceMarketDataParser
    logging.getLogger("BinanceMarketDataParser").setLevel(logging.INFO)
    RAW.mkdir(parents=True, exist_ok=True)

    def one(sym, interval):
        p = BinanceMarketDataParser(sym, interval=interval, start_date=start or settings.BINANCE_START_OVERRIDES.get(sym, settings.BINANCE_START),
                                    output_dir=str(RAW))
        p.run(force_update=False)

    jobs = [(sym, interval) for interval in settings.BINANCE_INTERVALS for sym in (symbols or settings.BINANCE_SYMBOLS)]
    failed = []
    with ThreadPoolExecutor(max_workers=PARSER_WORKERS) as pool:
        futs = {pool.submit(one, *j): j for j in jobs}
        for f, j in futs.items():
            try:
                f.result()
            except Exception as e:  # noqa: BLE001 - one symbol failing must not stop the others
                failed.append(j[0])
                ctx.log.warning("binance %s %s failed: %s", *j, e)
    out = load_csvs(ctx.con)
    if failed:
        out["failed_symbols"] = len(failed)
    return out
