# market-data-hub (repo: binance-data-pipeline)

Local DuckDB (`data/market.duckdb`) of crypto + macro data, a static dashboard, and research/backtest scripts.
Everything runs from the repo root with `.venv/bin/python` (or `python3`). Overview: README.md. Columns: docs/DATA_DICTIONARY.md. Source quirks: docs/SOURCES.md.

## Answering market questions (e.g. "why did ZEC drop?")
1. Query the local DB first: `python3 -m mdh sql "..."`; check freshness with `python3 -m mdh status`.
2. Coin price/OI/funding/CVD tables: `bn_kline_1d`, `bn_kline_1h`, `bn_metrics_1h_hist`; exchange-level OI/funding/premium come from `mdh/sources/cex.py`; indicators in `mdh/indicators.py`.
3. If data is stale, run `python3 -m mdh update <source>` before concluding. Say which numbers came from the DB and which from outside.

## Layout
- `mdh/` package: `sources/` one module per provider (register in `sources/__init__.py`), `core/` http + db, `derived/views.sql`, `dashboard/` JSON export + page.html, `settings.py` what to collect.
- `scripts/ops/` the hourly pipeline: `run_update.sh` (entry, run by launchd), `install_scheduler.sh`, `fetch_bgeometrics.py`, `push_etf_report.py`, `render_etf_report.cjs`. If you move these, re-run `scripts/ops/install_scheduler.sh`.
- `scripts/research/` backtests and one-off analyses (`backtest_*`, `alt_index_vs_btc`). New analyses go here.
- `scripts/tools/` manual utilities: `probe_apis.py` (source health), `verify_candles.py`, `load_xrpl_backfill.py` (loads into the DB when run; ignores --help).
- All scripts run from the repo root and locate it via `Path(__file__).parents[2]`.
- `data/` gitignored. `data/outputs/` finished charts/reports, `data/scratch/` throwaway dumps, `data/logs/`, `data/state/`.
- `seeds/` tracked one-time history. `tests/` run with `python3 -m unittest discover tests`.
- `market_data_parser.py` is the legacy Binance CLI (still works; 4 live-parser tests currently fail, a mock of `requests` is stale).

## Conventions
- New provider: `mdh/sources/<name>.py` with `run(ctx, full=False) -> {table: rows}`; all HTTP through `mdh/core/http.py` (rate limits, quotas).
- New research script: docstring at top stating question, data, and date; write results to `data/outputs/`, not the repo root.
- Secrets live in `.env` (gitignored); never print or commit them.
- Binance/Bybit need a non-US route; the hourly job handles this with `MDH_VPN_MODE` (see `scripts/run_update.sh`).
