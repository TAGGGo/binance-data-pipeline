#!/usr/bin/env python3
"""Hourly data-completeness check (added 2026-10-23 after the binance_1h 3-4 PM PT gap went unnoticed for 3 weeks).

Avalon: "任何时候发现缺了都不允许，都要调查". For each hourly table, every symbol must have every closed UTC hour
from the window start up to (now - 2h). Prints one "GAP ..." line per table with missing hours (or "gaps: none"),
and writes data/state/gaps.json. Read-only on the DB.
Usage: python3 scripts/ops/check_gaps.py [days=14]
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[2]
DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 14
now = datetime.now(timezone.utc).replace(tzinfo=None, minute=0, second=0, microsecond=0)
end = now - timedelta(hours=2)          # allow the normal ~1-2h publication lag
start = (now - timedelta(days=DAYS)).replace(hour=0)
CHECKS = [  # table, symbol col, time col, extra filter
    ("binance_1h", "symbol", "timestamp", ""),
    ("bn_kline_1h", "symbol", "ts", "and market='spot'"),
]
con = duckdb.connect(str(ROOT / "data/market.duckdb"), read_only=True)
report = {"checked_at_utc": now.isoformat(), "window": [start.isoformat(), end.isoformat()], "tables": {}}
any_gap = False
for table, sc, tc, extra in CHECKS:
    q = f"""
    with syms as (select {sc} s, greatest(date_trunc('hour', min({tc})), ?::timestamp) f, date_trunc('hour', max({tc})) l
                  from {table} where 1=1 {extra} group by 1 having max({tc}) >= ?),
    grid as (select s, h from syms, range(?::timestamp, ?::timestamp + interval 1 hour, interval 1 hour) t(h) where h >= f),
    have as (select distinct {sc} s, date_trunc('hour', {tc}) h from {table} where {tc} >= ? and {tc} <= ? {extra})
    select g.h, count(*) n_syms, string_agg(g.s, ' ' order by g.s) syms
    from grid g left join have v on g.s = v.s and g.h = v.h where v.s is null group by 1 order by 1"""
    rows = con.execute(q, [start, start, start, end, start, end]).fetchall()
    miss = [{"hour_utc": h.isoformat(), "n_symbols": n, "symbols": s[:300]} for h, n, s in rows]
    report["tables"][table] = miss
    if miss:
        any_gap = True
        hrs = sorted({m["hour_utc"][:13] for m in miss})
        print(f"GAP {table}: {len(miss)} missing hours since {start:%m-%d} (e.g. {', '.join(hrs[:6])}{' ...' if len(hrs) > 6 else ''}); "
              f"max symbols missing in one hour {max(m['n_symbols'] for m in miss)}")
con.close()
(ROOT / "data/state/gaps.json").write_text(json.dumps(report, indent=1))
if not any_gap:
    print(f"gaps: none ({', '.join(t for t, *_ in CHECKS)}, {DAYS}d)")
