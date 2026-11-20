#!/bin/bash
# Heavy on-chain jobs (added 2026-10-31, updated 2026-11-09).
# Runs synchronously inside run_update.sh (/bin/bash) so child processes inherit /bin/bash's Full Disk Access to /Volumes/chain
# and run sequentially (no multi-process DuckDB thread stampede against sandboxd).
# All daily outputs (miner, sth, spend_link, entity, cohort) only change when raw/inputs has a new settled UTC day (or mtime).
set -uo pipefail
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$REPO"
if [[ -x "$REPO/.venv/bin/python3" ]]; then PY="$REPO/.venv/bin/python3"; else PY="$(command -v python3)"; fi
set -a; [[ -f .env ]] && . ./.env; set +a
log() { echo "$(date -u '+%Y-%m-%d %H:%M:%S') heavy[$1]: $2"; }
run() { local tag="$1"; shift; "$@" 2>&1 | while read -r l; do log "$tag" "$l"; done; }

CH="${MDH_CHAIN_DIR:-/Volumes/chain/datahub/chain}"
CUR_STAMP="$("$PY" -c '
import datetime as dt, os
ch = os.environ.get("MDH_CHAIN_DIR", "/Volumes/chain/datahub/chain")
today = dt.datetime.utcnow().date()
y = today - dt.timedelta(days=1)
yf = os.path.join(ch, "raw", "inputs", f"date={y}.parquet")
settle = dt.datetime.combine(today, dt.time(2, 0), tzinfo=dt.timezone.utc).timestamp()
d = y if (os.path.exists(yf) and os.path.getmtime(yf) >= settle) else (y - dt.timedelta(days=1))
df = os.path.join(ch, "raw", "inputs", f"date={d}.parquet")
if os.path.exists(df):
    print(f"{d}:{int(os.path.getmtime(df))}")
' 2>/dev/null)"
STAMP_FILE="$REPO/data/state/heavy_raw_stamp.txt"

if [[ -n "$CUR_STAMP" && -f "$STAMP_FILE" && "$(cat "$STAMP_FILE" 2>/dev/null)" == "$CUR_STAMP" && -f "$REPO/data/onchain/self/entity_daily.csv" && -f "$REPO/data/onchain/self/cohort_daily.csv" && -f "$REPO/data/onchain/self/sth_daily.csv" ]]; then
  log main "up to date for $CUR_STAMP; refreshing sth_live.json only"
  "$PY" -c '
import datetime as dt, json, os, duckdb
repo = os.getcwd()
f = os.path.join(repo, "data", "onchain", "self", "sth_live.json")
if os.path.exists(f):
    d = json.load(open(f))
    con = duckdb.connect(os.path.join(repo, "data", "market.duckdb"), read_only=True)
    lts, lp = con.execute("select ts, close from bn_kline_1h where symbol=\x27BTC\x27 and market=\x27spot\x27 order by ts desc limit 1").fetchone()
    con.close()
    if d.get("sth_rp"):
        d["computed_utc"] = dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M")
        d["price"] = round(lp, 2)
        d["price_hour_utc"] = str(lts)[:16]
        d["sth_mvrv_live"] = round(lp / d["sth_rp"], 4)
        json.dump(d, open(f, "w"), indent=1)
' 2>&1 | while read -r l; do log "sth_live" "$l"; done
  exit 0
fi

log main "start (stamp: ${CUR_STAMP:-none})"
run miner "$PY" scripts/chain/miner_revenue.py
STH_MEM=2GB STH_TMP=/tmp/sth_ddb run sth "$PY" scripts/chain/sth_metrics.py --budget 21600
LINK_MEM=5GB LINK_TMP=/tmp/link_ddb run link "$PY" scripts/chain/spend_link.py --budget 36000
run exch "$PY" scripts/chain/exch_cluster_labels.py
ENTITY_MEM=3GB ENTITY_TMP=/tmp/entity_ddb ENTITY_BUDGET=14400 run entity "$PY" scripts/chain/entity_balances.py
run cohort "$PY" scripts/chain/cohort_metrics.py
if [[ -n "$CUR_STAMP" && -f "$REPO/data/onchain/self/entity_daily.csv" && -f "$REPO/data/onchain/self/cohort_daily.csv" && -f "$REPO/data/onchain/self/sth_daily.csv" ]]; then
  echo "$CUR_STAMP" > "$STAMP_FILE"
fi
log main "done"
