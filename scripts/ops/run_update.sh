#!/bin/bash
# Hourly entry point used by the launchd job (scripts/ops/install_scheduler.sh).
#
# 1. Updates every non-Binance source on your normal connection.
# 2. Binance step:
#      MDH_VPN_MODE=off      (default) run it as-is; today's live futures fill in next day from the archive
#      MDH_VPN_MODE=binance  connect ExpressVPN (expressvpnctl) just for the Binance step, then
#                            restore whatever VPN state you had before
#      MDH_VPN_MODE=always   you keep ExpressVPN on yourself; nothing is toggled
# Settings live in .env:  MDH_VPN_MODE=binance   MDH_VPN_LOCATION="Japan - Tokyo"
set -uo pipefail
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:/Applications/ExpressVPN.app/Contents/MacOS:$PATH"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO"
if [[ -x "$REPO/.venv/bin/python3" ]]; then PY="$REPO/.venv/bin/python3"; else PY="$(command -v python3)"; fi
set -a; [[ -f .env ]] && . ./.env; set +a
VPN_MODE="${MDH_VPN_MODE:-off}"
VPN_LOCATION="${MDH_VPN_LOCATION:-smart}"
CTL="$(command -v expressvpnctl || true)"
log() { echo "$(date -u '+%Y-%m-%d %H:%M:%S') run_update: $*"; }

# never run two updates at once (a slow run can overlap the next hour)
LOCK="$REPO/data/state/run.lock"; mkdir -p "$REPO/data/state"
if ! mkdir "$LOCK" 2>/dev/null; then
  if [[ -n "$(find "$LOCK" -maxdepth 0 -mmin +120 2>/dev/null)" ]]; then rmdir "$LOCK"; mkdir "$LOCK"; else log "previous run still going; skipping"; exit 0; fi
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

log "start (vpn mode: $VPN_MODE)"
VPN_SRC="binance bybit binance_funding"   # need a non-US connection (see VPN_SOURCES in mdh/settings.py)
OTHERS=$("$PY" -m mdh sources | grep -vxE 'binance|bybit|binance_funding' | tr '\n' ' ')
"$PY" -m mdh update $OTHERS

vpn_connected() { "$CTL" status 2>/dev/null | grep -qiE '^[[:space:]]*connected|connected to' && ! "$CTL" status 2>/dev/null | grep -qiE 'disconnected|not connected'; }
WE_CONNECTED=0
if [[ "$VPN_MODE" == "binance" ]]; then
  if [[ -z "$CTL" ]]; then
    log "expressvpnctl not found; running Binance without VPN"
  elif vpn_connected; then
    log "VPN already connected; leaving it as is"
  else
    # MDH_VPN_LOCATION may be an exact region id (japan-tokyo) or just a prefix (japan): take the first match
    REGION="$("$CTL" get regions 2>/dev/null | grep -i "^${VPN_LOCATION}" | head -1)"
    VPN_LOCATION="${REGION:-$VPN_LOCATION}"
    log "connecting ExpressVPN ($VPN_LOCATION)"
    "$CTL" connect "$VPN_LOCATION" >/dev/null 2>&1 &
    for _ in $(seq 1 30); do sleep 2; vpn_connected && break; done
    if vpn_connected; then WE_CONNECTED=1; log "VPN connected"; else log "VPN did not connect in 60s; running Binance without it"; fi
  fi
fi

"$PY" -m mdh update $VPN_SRC

if [[ "$WE_CONNECTED" == 1 ]]; then
  "$CTL" disconnect >/dev/null 2>&1 && log "VPN disconnected (restored previous state)"
fi
# BTC on-chain metrics from BGeometrics free API (1 request per metric per UTC day; quota 10/h, 15/day)
"$PY" scripts/ops/fetch_bgeometrics.py 2>&1 | while read -r line; do log "$line"; done
# rebuild the dashboard data file (data/dashboard/data.json); publishing it to the artifact is a separate step
if "$PY" -m mdh export >/dev/null; then log "dashboard data.json exported"; else log "mdh export failed"; fi
# daily ETF report to WeChat via PushPlus (sends once per new session, after 13:00 UTC; needs PUSHPLUS_TOKEN in .env)
"$PY" scripts/ops/push_etf_report.py 2>&1 | while read -r line; do log "$line"; done

log "done"
