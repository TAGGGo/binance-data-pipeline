"""
Load the XRP whale balance backfill (built in the cloud from XRPL account_tx, see claude/xrp-whale-tracker.md)
into market.duckdb:

  data/backfill/xrpl_balance_hist.csv.gz   date, account, balance_xrp   end-of-UTC-day balance, 2026-03-01 .. 2026-09-28
  data/backfill/xrpl_backfill_accounts.csv account, pages, capped, created, n_events, error

Covers every unnamed account that held >= 1M XRP in the 2026-09-29/30 rich lists (so it has survivorship bias:
whales that sold out before 2026-09-29 are missing). Accounts with more than 15 pages x 400 transactions since
2026-03-01 are 'capped' and not rebuilt (treated as services by mdh/dashboard/onchain.py).

    python3 scripts/tools/load_xrpl_backfill.py
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from mdh import settings  # noqa: E402
from mdh.core import db  # noqa: E402

B = settings.DATA_DIR / "backfill"
con = db.connect()
h = pd.read_csv(B / "xrpl_balance_hist.csv.gz", parse_dates=["date"])
h["date"] = h["date"].dt.date
a = pd.read_csv(B / "xrpl_backfill_accounts.csv")
a["capped"] = a["capped"].fillna(False).astype(bool)
print("xrpl_balance_hist", db.upsert(con, "xrpl_balance_hist", h, ["date", "account"]))
print("xrpl_backfill_accounts", db.upsert(con, "xrpl_backfill_accounts", a, ["account"]))
con.close()
