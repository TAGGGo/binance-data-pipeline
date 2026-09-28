#!/bin/bash
# Install an hourly launchd job on macOS that runs `python3 -m mdh update`.
#   ./scripts/install_scheduler.sh            install / refresh
#   ./scripts/install_scheduler.sh uninstall  remove
# Logs: data/logs/update.log   (launchd runs even when Terminal is closed; the Mac must be awake)
set -euo pipefail
LABEL="com.market-data-hub.update"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
# prefer the repo virtualenv if there is one (python3 -m venv .venv)
if [[ -x "$REPO/.venv/bin/python3" ]]; then PY="$REPO/.venv/bin/python3"; else PY="$(command -v python3)"; fi

if [[ "${1:-}" == "uninstall" ]]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"; echo "removed $LABEL"; exit 0
fi

mkdir -p "$REPO/data/logs" "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$PY</string><string>-m</string><string>mdh</string><string>update</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>StartCalendarInterval</key><dict><key>Minute</key><integer>7</integer></dict>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$REPO/data/logs/update.log</string>
  <key>StandardErrorPath</key><string>$REPO/data/logs/update.log</string>
</dict></plist>
EOF
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "installed $LABEL: runs every hour at :07 using $PY"
echo "tail -f $REPO/data/logs/update.log"
