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
VPN_SRC="binance bybit binance_funding oi5m"   # need a non-US connection (see VPN_SOURCES in mdh/settings.py)
OTHERS=$("$PY" -m mdh sources | grep -vxE 'binance|bybit|binance_funding|oi5m' | tr '\n' ' ')
"$PY" -m mdh update $OTHERS

# VPN on/off goes through ~/Projects/vpn_session.py, shared with the btc-volatility monitor so one job
# never disconnects the VPN while another is still using it (last one out disconnects, only if a job connected it).
VPNS="$REPO/../../vpn_session.py"
IN_VPN=0
if [[ "$VPN_MODE" == "binance" ]]; then
  if [[ -z "$CTL" || ! -f "$VPNS" ]]; then
    log "expressvpnctl or vpn_session.py not found; running Binance without VPN"
  else
    log "VPN on for the Binance step ($VPN_LOCATION)"
    IN_VPN=1
    if /usr/bin/python3 "$VPNS" enter datahub $$ "$VPN_LOCATION" >/dev/null; then log "VPN connected"; else log "VPN did not connect in 60s; running Binance without it"; fi
  fi
fi

"$PY" -m mdh update $VPN_SRC

if [[ "$IN_VPN" == 1 ]]; then
  log "VPN: $(/usr/bin/python3 "$VPNS" leave datahub $$)"
fi
# BTC on-chain metrics from BGeometrics free API (1 request per metric per UTC day; quota 10/h, 15/day)
"$PY" scripts/ops/fetch_bgeometrics.py 2>&1 | while read -r line; do log "$line"; done
# 水博乱乱 mrluanluan.com: once per UTC day, ONLY data we lack (IBIT options OI + GEX, exchange stablecoin reserves, his USDT ammo score)
"$PY" scripts/ops/fetch_mrluanluan.py 2>&1 | while read -r line; do log "$line"; done
# Deribit BTC option trades (history.deribit.com, public): daily call/put taker premium by bucket, catch-up of closed UTC days (backfill since 2019 done 2026-10-17)
"$PY" scripts/ops/fetch_deribit_opt_trades.py 2>&1 | while read -r line; do log "$line"; done
# Coinbase taker-flow history backfill (backfill_cb_trades.py, own launchd job) -> merge finished days into cb_taker_15m (added 2026-10-30)
"$PY" scripts/ops/merge_cb_backfill.py 2>&1 | while read -r line; do log "$line"; done
# Binance USDT-M perp order-book depth history (data.binance.vision bookDepth, +-0.2..5%, 5-min rows) -> data/bookdepth/ (added 2026-10-30; initial backfill ~120 days/run)
"$PY" scripts/ops/fetch_bn_bookdepth.py --max-days 120 --seconds 600 2>&1 | while read -r line; do log "$line"; done
# data completeness check (Avalon 10/23: any missing hour must be investigated): prints GAP lines, writes data/state/gaps.json
"$PY" scripts/ops/check_gaps.py 2>&1 | while read -r line; do log "$line"; done
# rebuild the dashboard data file (data/dashboard/data.json); publishing it to the artifact is a separate step
if "$PY" -m mdh export >/dev/null; then log "dashboard data.json exported"; else log "mdh export failed"; fi
# daily ETF report to WeChat via PushPlus (sends once per new session, after 13:00 UTC; needs PUSHPLUS_TOKEN in .env)
"$PY" scripts/ops/push_etf_report.py 2>&1 | while read -r line; do log "$line"; done
# 币聊今日日报的数据块（持仓体检、异动、信号灯、板块、资金面、宏观、大事）
"$PY" scripts/ops/report_blocks.py 2>&1 | while read -r line; do log "$line"; done
# 日报更新后给爸爸发一句微信提醒（任务写完日报会立刻发；这里是兜底）
"$PY" scripts/ops/push_report_notice.py 2>&1 | while read -r line; do log "$line"; done

# US stock trend alerts (ES/NQ/RSP, LEI-style lines) to Avalon's WeChat; daily report after the US close
"$PY" scripts/ops/us_trend_alert.py 2>&1 | while read -r line; do log "us_trend: $line"; done

"$PY" scripts/ops/node_status.py 2>&1 | while read -r line; do log "$line"; done
"$PY" scripts/onchain/node_urpd.py 2>&1 | while read -r line; do log "$line"; done
# BTC supply concentration near5 (daily + live hourly + Q1 v1.1 status) from the node URPD -> data/onchain/node_conc_daily.csv, node_conc_live.json
"$PY" scripts/onchain/node_conc.py 2>&1 | while read -r line; do log "$line"; done
# Supply-quantile cost basis (Murphy 分位成本线) q50..q75 -> data/onchain/cost_quantiles_daily.csv (added 2026-10-27)
"$PY" scripts/onchain/node_quantiles.py 2>&1 | while read -r line; do log "$line"; done

# Raw on-chain table from AWS Open Data (data/chain/raw: tx/outputs/inputs per UTC day; added 2026-10-29, see claude/ats-build-research-2026-10-29.md)
# re-pulls today+yesterday, then backfills newest-first down to MDH_CHAIN_FLOOR within MDH_CHAIN_BUDGET seconds
"$PY" scripts/chain/aws_backfill.py 2>&1 | while read -r line; do log "$line"; done
# Accumulation Trend Score v1 (Glassnode method, exact balances from genesis; waits until the raw backfill reaches 2009, then
# builds index -> clusters -> flows in ~25-minute slices each hour, then keeps up daily). Output data/onchain/ats_v1_score.csv
"$PY" scripts/chain/ats_v1.py 2>&1 | while read -r line; do log "$line"; done

log "done"
