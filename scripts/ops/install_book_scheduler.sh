#!/bin/bash
# Two extra launchd jobs (added 2026-10-30):
#   com.market-data-hub.book        every 60 s : scripts/ops/book_snapshot.py   -> data/orderbook/book_YYYY-MM.csv
#   com.market-data-hub.cbbackfill  every hour : scripts/ops/backfill_cb_trades.py (55 min, resumable; exits at once when done)
#                                                finished days are merged into cb_taker_15m by run_update.sh
#   ./scripts/ops/install_book_scheduler.sh            install / refresh
#   ./scripts/ops/install_book_scheduler.sh uninstall  remove both
# Logs: data/logs/book.log, data/logs/cb_backfill.log   (the Mac must be awake)
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
if [[ -x "$REPO/.venv/bin/python3" ]]; then PY="$REPO/.venv/bin/python3"; else PY="$(command -v python3)"; fi
AG="$HOME/Library/LaunchAgents"; mkdir -p "$AG" "$REPO/data/logs"

install_job() {  # label interval log args...
  local LABEL="$1" INT="$2" LOG="$3"; shift 3
  local PLIST="$AG/$LABEL.plist" ARGS=""
  for a in "$@"; do ARGS+="<string>$a</string>"; done
  cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>$ARGS</array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>StartInterval</key><integer>$INT</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$REPO/data/logs/$LOG</string>
  <key>StandardErrorPath</key><string>$REPO/data/logs/$LOG</string>
</dict></plist>
PL
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  for i in 1 2 3 4 5; do
    launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null && break
    sleep 2; [[ $i == 5 ]] && launchctl bootstrap "gui/$(id -u)" "$PLIST"
  done
  echo "installed $LABEL (every ${INT}s) -> data/logs/$LOG"
}

if [[ "${1:-}" == "uninstall" ]]; then
  for L in com.market-data-hub.book com.market-data-hub.cbbackfill; do
    launchctl bootout "gui/$(id -u)/$L" 2>/dev/null || true; rm -f "$AG/$L.plist"; echo "removed $L"
  done; exit 0
fi

install_job com.market-data-hub.book 60 book.log "$PY" "$REPO/scripts/ops/book_snapshot.py"
install_job com.market-data-hub.cbbackfill 3600 cb_backfill.log "$PY" "$REPO/scripts/ops/backfill_cb_trades.py" --minutes 55
echo "python: $PY"
echo "check: tail -f $REPO/data/logs/cb_backfill.log ; tail -3 $REPO/data/orderbook/book_\$(date -u +%Y-%m).csv"
