"""DuckDB storage: one file (data/market.duckdb), idempotent upserts, ingest log."""
from __future__ import annotations

from datetime import datetime, timezone

import duckdb
import pandas as pd

from mdh import settings

LOG_DDL = """
CREATE TABLE IF NOT EXISTS _ingest_log (
    run_at      TIMESTAMP,
    source      VARCHAR,
    table_name  VARCHAR,
    rows        BIGINT,
    status      VARCHAR,
    message     VARCHAR,
    seconds     DOUBLE
)"""


def connect(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(settings.DB_PATH), read_only=read_only)
    if not read_only:
        con.execute(LOG_DDL)
    return con


def table_exists(con, table: str) -> bool:
    return con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [table]
    ).fetchone()[0] > 0


def upsert(con, table: str, df: pd.DataFrame, keys: list[str]) -> int:
    """Insert rows; rows whose key already exists are replaced. New columns are added."""
    if df is None or df.empty:
        return 0
    df = df.drop_duplicates(subset=keys, keep="last").reset_index(drop=True)
    # pandas 3 keeps the parsed resolution (s / ms / us); DuckDB can't cast between TIMESTAMP_S/_MS
    # columns, so always store plain TIMESTAMP (microseconds)
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]) and getattr(df[c].dt, "tz", None) is None:
            df[c] = df[c].astype("datetime64[us]")
    con.register("_incoming", df)
    try:
        if not table_exists(con, table):
            con.execute(f'CREATE TABLE "{table}" AS SELECT * FROM _incoming LIMIT 0')
        desc = con.execute(f'DESCRIBE "{table}"').fetchall()
        for name, dtype, *_ in desc:   # repair tables created with a non-microsecond timestamp type
            if dtype in ("TIMESTAMP_S", "TIMESTAMP_MS", "TIMESTAMP_NS"):
                con.execute(f'ALTER TABLE "{table}" ALTER COLUMN "{name}" TYPE TIMESTAMP')
        existing = {r[0] for r in desc}
        for name, dtype, *_ in con.execute("DESCRIBE _incoming").fetchall():
            if name not in existing:
                con.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {dtype}')
        cond = " AND ".join(f'"{table}"."{k}" = _incoming."{k}"' for k in keys)
        con.execute("BEGIN")
        con.execute(f'DELETE FROM "{table}" USING _incoming WHERE {cond}')
        con.execute(f'INSERT INTO "{table}" BY NAME SELECT * FROM _incoming')
        con.execute("COMMIT")
    except Exception:
        try:
            con.execute("ROLLBACK")
        except duckdb.Error:
            pass  # no open transaction
        raise
    finally:
        con.unregister("_incoming")
    return len(df)


def max_value(con, table: str, column: str, where: str = "", params=None):
    if not table_exists(con, table):
        return None
    q = f'SELECT max("{column}") FROM "{table}"' + (f" WHERE {where}" if where else "")
    return con.execute(q, params or []).fetchone()[0]


def log_ingest(con, source, table, rows, status, message="", seconds=0.0):
    con.execute("INSERT INTO _ingest_log VALUES (?,?,?,?,?,?,?)",
                [datetime.now(timezone.utc).replace(tzinfo=None), source, table, rows, status,
                 (message or "")[:500], round(seconds, 2)])
