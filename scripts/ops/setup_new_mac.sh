#!/bin/bash
# One-time setup after migrating the repo to a new Mac (2026-10-04).
#   ./scripts/ops/setup_new_mac.sh          venv + deps + status + one catch-up run
#   ./scripts/ops/setup_new_mac.sh install  ... then also install the hourly launchd job
# Run it in Terminal on the Mac (launchd/venv are macOS-only). Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")/../.."
REPO="$PWD"
xattr -dr com.apple.quarantine . 2>/dev/null || true
chmod +x scripts/ops/*.sh 2>/dev/null || true

PYBIN="$(command -v python3.12 || command -v python3.11 || command -v python3 || true)"
[[ -z "$PYBIN" ]] && { echo "No python3 found. Install: xcode-select --install   (or brew install python@3.12)"; exit 1; }
echo "python: $PYBIN ($($PYBIN --version))"
command -v git >/dev/null || { echo "git missing: run xcode-select --install"; exit 1; }

[[ -x .venv/bin/python3 ]] || "$PYBIN" -m venv .venv
.venv/bin/python3 -m pip install -q -U pip
.venv/bin/python3 -m pip install -q -r requirements.txt
echo "deps ok"

echo "--- freshness before catch-up ---"
.venv/bin/python3 -m mdh status 2>&1 | tail -40 || true

echo "--- VPN ---"
if grep -q '^MDH_VPN_MODE=binance' .env 2>/dev/null; then
  command -v expressvpnctl >/dev/null || [[ -x /Applications/ExpressVPN.app/Contents/MacOS/expressvpnctl ]] \
    && echo "expressvpnctl found" \
    || echo "WARNING: .env wants ExpressVPN but expressvpnctl is not installed here. Install/login ExpressVPN, or Binance futures + Bybit will be skipped/late (US IP is blocked)."
fi

echo "--- one catch-up run (same as the hourly job) ---"
./scripts/ops/run_update.sh
tail -25 data/logs/update.log 2>/dev/null | cut -c1-180 || true

if [[ "${1:-}" == "install" ]]; then
  ./scripts/ops/install_scheduler.sh
fi
echo "done."
