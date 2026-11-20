# market-data-hub (repo: binance-data-pipeline)

Local DuckDB (`data/market.duckdb`) of crypto + macro data, a static dashboard, and research/backtest scripts.
Everything runs from the repo root with `.venv/bin/python` (or `python3`). Overview: README.md. Columns: docs/DATA_DICTIONARY.md. Source quirks: docs/SOURCES.md.

## Answering market questions (e.g. "why did ZEC drop?")
1. Query the local DB first: `python3 -m mdh sql "..."`; check freshness with `python3 -m mdh status`.
2. Coin price/OI/funding/CVD tables: `bn_kline_1d`, `bn_kline_1h`, `bn_metrics_1h_hist`; exchange-level OI/funding/premium come from `mdh/sources/cex.py`; indicators in `mdh/indicators.py`.
3. If data is stale, run `python3 -m mdh update <source>` before concluding. Say which numbers came from the DB and which from outside.

## Layout
- `mdh/` package: `sources/` one module per provider (register in `sources/__init__.py`), `core/` http + db, `derived/views.sql`, `dashboard/` JSON export + page.html, `settings.py` what to collect.
  - `mdh/sources/universe.py` (updated 2026-11-20): hourly auto-discovery of CoinGecko/CMC Top 150 coins (`market_cap_rank <= 150` or top 100 non-stable/non-wrapped/non-gold-pegged coins on Binance Spot, excluding `PAXG`/`XAUT`/stablecoins/wrapped/tokenized treasury funds). Auto-adds up to 3 new coins/day into `data/state/universe.json`, triggers full history + derivatives collection, and auto-downloads missing PNG logos into `~/Projects/crypto-chat/public/static/logos/{SYM}.png`.
- `scripts/ops/` the hourly pipeline: `run_update.sh` (entry, run by launchd), `install_scheduler.sh`, `fetch_bgeometrics.py`, `push_etf_report.py`, `render_etf_report.cjs`. If you move these, re-run `scripts/ops/install_scheduler.sh`.
- `scripts/research/` backtests and one-off analyses (`backtest_*`, `alt_index_vs_btc`). New analyses go here.
- `scripts/tools/` manual utilities: `probe_apis.py` (source health), `verify_candles.py`, `load_xrpl_backfill.py` (loads into the DB when run; ignores --help).
- All scripts run from the repo root and locate it via `Path(__file__).parents[2]`.
- `data/` gitignored. `data/outputs/` finished charts/reports, `data/scratch/` throwaway dumps, `data/logs/`, `data/state/`.
- Intermediate/scratch debug scripts left on disk (`charts/`, `scripts/std/retired/`, `scripts/ops/{pip_install,sysload,sysload2,spawn_ats2}.py`, `scripts/chain/{ats_v0_*,ats_diag,ats_kit2,ats_lab2,cb_*,coinbase_find,ea_chain,ea_reset,pool_all,por_check}.py`) are intentionally kept locally and listed in `.gitignore` (2026-11-20).
- `seeds/` tracked one-time history. `tests/` run with `python3 -m unittest discover tests`.
- `market_data_parser.py` is the legacy Binance CLI (still works; 4 live-parser tests currently fail, a mock of `requests` is stale).

## Conventions
- New provider: `mdh/sources/<name>.py` with `run(ctx, full=False) -> {table: rows}`; all HTTP through `mdh/core/http.py` (rate limits, quotas).
- New research script: docstring at top stating question, data, and date; write results to `data/outputs/`, not the repo root.
- Backtest verification (Avalon 2026-11-09): every backtest must be audited by a separate verification subagent (checking lookahead bias, survivorship bias, delisted-coin handling, post-hoc threshold snooping, and sample size across cycles) and report caveats honestly.
- Independent analysis, zero flattery & mobile formatting (Avalon 2026-11-11): never send wide tables (>3 columns, unreadable on mobile). Never use flattering, praising, or dramatic filler phrases ("你看盘比我准", "你的盘感非常准", "你这一问把我问醒了", "你说得完全对")—state facts in a calm, flat, matter-of-fact tone like Claude. Never be a sycophant or swing from one extreme to the other when challenged—present hard facts objectively with both pros/cons (what matches vs what differs), and always re-read raw `.txt` output files before writing summary numbers. Never make subjective probability claims ("more likely", "high hope", "extremely dangerous") unless backed by an explicit calculated backtest win rate (and if the edge is slight or sample size is small, say so plainly); when there is no statistical win rate to decide between paths, simply list the pros and cons side by side.
- Secrets live in `.env` (gitignored); never print or commit them.
- Binance/Bybit need a non-US route; the hourly job handles this with `MDH_VPN_MODE` (see `scripts/run_update.sh`).

## On-chain entity clustering: check against data/reference/x_charts (Avalon 2026-11-01)
- We don't subscribe to Glassnode. M大 (@Murphychen888, incl. subscriber posts) and 貝格先生 (@market_beggar) often post
  Glassnode charts (many entity-adjusted). They are archived in `data/reference/x_charts/` (images, post lists, README) with
  numbers read from them in `values.csv` (text = exact, chart = ~2%, colour charts = periods only).
- Whenever the entity clustering / entity-adjusted metrics (ATS, STH/LTH entity supply, STH-URPD, exchange balances, whales)
  produce results, COMPARE them with these references before calling them accurate: draw ours for the same date/range and
  put it next to theirs; log the differences. Validation targets already noted there: Glassnode ATS colour periods
  (yellow 2025-08..10, 2025-12..2026-01, 2026-02..05; purple 2026-06..08) and M大's 10/31 STH-URPD (entity-adjusted).
- This is only needed until our clustering is accurate; after that the archive is just history.

## Shared with other AI assistants (2026-11-09)
- `AGENTS.md` is a link to this file, so Google Antigravity (Gemini) reads the same rules. Keep new rules here, not in one assistant's private memory.
- The website (币聊 Crypto Chat, `~/Projects/crypto-chat`) has its own `AGENTS.md`; the scheduled AI tasks (news + 日报, weekly on-chain and exchange-balance checks) are documented with their full prompts in `~/Projects/crypto-chat/docs/ai/scheduled-tasks.md`.
- Avalon's preferences: reply in Chinese, short, conclusion first; coins as BTC/ETH; changes in % only; no time-zone labels; never expose his or his dad's holdings to family.
- Reading X / using Chrome on Mac: always use `chrome-devtools` MCP (`call_mcp_tool` on `chrome-devtools-plugin_chrome-devtools`: `list_pages` / `new_page` / `navigate_page` / `take_snapshot` / `evaluate_script` / `close_page`) directly. Never use `curl` for X, and never invoke the built-in `browser` subagent (which fails on macOS with `Could not find DevToolsActivePort`). Global rules live in `~/.gemini/config/plugins/chrome-devtools-plugin/rules/AGENTS.md`, and `mcp-launcher.js` connects all conversation windows to the dedicated port `9223` Chrome (`~/.gemini/chrome-debug-profile`) with zero confirmation popups ever.

## The chain disk (/Volumes/chain/datahub/chain, ~280 GB, external drive)
- Data only; the code is in this repo's `scripts/chain/` (ea2.py = entity clustering, ea2_post.py = re-apply labels, ats_*.py = accumulation score, aws_extract/aws_backfill = raw tx history).
- `raw/` (inputs, outputs, tx): full BTC transaction history. Huge: never scan, index, copy or rewrite it; read only the specific files a script names.
- `labels/`: exchange labels. Edit only `extra_roots.csv`, `auto_ents.csv`, `not_exch.csv` (back up first), then run ea2_post. Never re-run full ea2 or change the clustering algorithm without asking Avalon (it takes hours).
- `ea2/`, `ea_urpd/`, `derived/`, `ats_*`, `exch_disc/`: generated outputs.
- Long jobs run natively on the Mac via the job queue: write `data/state/jobq/<name>.job` (first line = script path, then KEY=value lines); the Mac picks it up within a minute, log in `data/logs/<name>.log`.
- **Never detach (`start_new_session=True`) processes that access `/Volumes/chain` after `/bin/bash` exits (2026-11-09 kernel deadlock fix)**: Full Disk Access in macOS TCC belongs to `/bin/bash`. When `spawn_heavy.py` / `spawn_ats.py` / `spawn_job.py` used `start_new_session=True` and `run_update.sh` exited immediately, 7 orphaned Python+DuckDB processes (~100 threads) lost `/bin/bash` as their live `Responsible` parent and simultaneously hit `/Volumes/chain`, flooding `sandboxd`'s 64 approval threads (`com.apple.sandboxd.approval`, `Dispatch Thread Soft Limit Reached: 64`) into a permanent kernel semaphore deadlock. Always keep `/Volumes/chain` jobs inside a live `/bin/bash` session (`p.wait()`) and run them sequentially.
