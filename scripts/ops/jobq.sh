#!/bin/bash
# Every-minute job queue runner (launchd com.market-data-hub.jobq, installed by run_update.sh 2026-11-01).
# Started through /bin/bash on purpose: bash has Full Disk Access, so jobs can use /Volumes/chain (a python
# started directly by launchd cannot: "Operation not permitted").
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
if [[ -x "$REPO/.venv/bin/python3" ]]; then PY="$REPO/.venv/bin/python3"; else PY="$(command -v python3)"; fi
"$PY" "$REPO/scripts/ops/jobq.py"
# live spot prices for the board / Claude (2026-11-08): data/live/prices.json, every minute. Runs after the queue,
# in the foreground (launchd kills leftover background children when the job exits); ~1 s, 20 s network timeout.
"$PY" "$REPO/scripts/ops/live_prices.py" >> "$REPO/data/logs/live_prices.log" 2>&1
