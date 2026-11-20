#!/usr/bin/env python3
"""Load finished backfill days (data/backfill/cb_taker/*.csv from backfill_cb_trades.py) into cb_taker_15m.
Called from run_update.sh (which already owns the DB). Only inserts (symbol, ts) rows that are not there yet,
so live rows are never overwritten and re-running is harmless. Merged file names go to data/state/cb_backfill_merged.txt."""
from pathlib import Path
import duckdb

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "data" / "backfill" / "cb_taker"
DONE = REPO / "data" / "state" / "cb_backfill_merged.txt"
done = set(DONE.read_text().split()) if DONE.exists() else set()
todo = sorted(p for p in SRC.glob("*.csv") if p.name not in done)
if todo:
    con = duckdb.connect(str(REPO / "data" / "market.duckdb"))
    before = con.execute("select count(*) from cb_taker_15m").fetchone()[0]
    files = ",".join(f"'{p}'" for p in todo)
    con.execute(f"""insert into cb_taker_15m (symbol, ts, buy_usd, sell_usd, buy_qty, sell_qty, trades, delta_usd, vwap)
      select n.symbol, n.ts, n.buy_usd, n.sell_usd, n.buy_qty, n.sell_qty, n.trades, n.delta_usd, n.vwap
      from read_csv([{files}], header=true, columns={{'symbol':'VARCHAR','ts':'TIMESTAMP','buy_usd':'DOUBLE','sell_usd':'DOUBLE',
           'buy_qty':'DOUBLE','sell_qty':'DOUBLE','trades':'BIGINT','delta_usd':'DOUBLE','vwap':'DOUBLE'}}) n
      anti join cb_taker_15m o on o.symbol = n.symbol and o.ts = n.ts""")
    added = con.execute("select count(*) from cb_taker_15m").fetchone()[0] - before
    con.close()
    with DONE.open("a") as fh:
        fh.write("\n".join(p.name for p in todo) + "\n")
    mn = min(p.stem.split("_")[1] for p in todo)
    print(f"merge_cb_backfill: {len(todo)} day files, +{added} rows (oldest day {mn})")
