"""
XRP whale tracker: a daily snapshot of the XRPL rich list (top 10,000 accounts by XRP held, from XRPScan) with
entity labels, plus account flags read from the ledger to tag custodial accounts XRPScan has not labeled.

Tables
  xrpl_richlist  (date, account, balance_xrp, escrow_xrp, name, label_category, snapshot_ts)   one row per account per day
  xrpl_accounts  (account, domain, require_dest, exists, checked_at)                           ledger-flag cache
The final category is assigned in the view v_xrpl_richlist (mdh/derived/views.sql), so flags looked up later
apply to every day consistently.

Categories
  ripple            XRPScan name "Ripple" (operational + escrow accounts)
  ripple_insider    named Ripple founders / early executives
  exchange          any other XRPScan-labeled custodial service: exchanges, brokers, custodians, casinos
  other_labeled     labeled but not custodial: bridges, treasury companies, protocols, hack wallets
  likely_custodial  unlabeled, >= 1M XRP, and the account requires a destination tag or publishes a domain.
                    Exchanges need destination tags to credit deposits; individuals rarely turn it on. Heuristic.
  unlabeled         everything else. This is the "whale" set, and it still contains some unlabeled services.

Limits
  * The rich list is current-only: there is no history before the first run. Snapshots are taken once per UTC
    day (the first run after 00:00 UTC); later runs that day are skipped unless --full.
  * Balances only; escrowed XRP is stored separately and not counted as held.
  * Ledger flags are looked up at most MAX_FLAG_LOOKUPS accounts per run (largest first) and cached, so the
    first day or two after install some big custodial accounts still show as "unlabeled".
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from mdh.core import db

NAME = "xrpl_whales"
RICHLIST_URL = "https://api.xrpscan.com/api/v1/balances"
RPC_URL = "https://s2.ripple.com:51234/"
DROPS = 1_000_000
LSF_REQUIRE_DEST = 0x00020000
FLAG_MIN_XRP = 1_000_000          # look up ledger flags for unlabeled accounts at or above this balance
FLAG_REFRESH_DAYS = 30
MAX_FLAG_LOOKUPS = 400            # per run (~100 s at the configured rate)

RIPPLE = {"Ripple"}
RIPPLE_INSIDERS = {"chrislarsen", "ahbritto", "JedMcCaleb", "jedmccaleb", "Jed McCaleb"}
NON_CUSTODIAL = {  # labeled, but not a custodial balance sheet
    "Evernorth", "Flare Core Vault", "Axelar Bridge", "XPR Bridge", "NEAR", "PulseX Sacrifice",
    "Doppler Finance", "First Ledger", "Nobitex Hack", "Binance Charity", "Coinbase cbXRP", "Union Chain",
    "Bitrue Insurance Fund",
}


def _category(name) -> str | None:
    if not isinstance(name, str) or not name:   # pandas 3 stores a missing name as NaN, which is truthy
        return None
    if name in RIPPLE:
        return "ripple"
    if name in RIPPLE_INSIDERS:
        return "ripple_insider"
    if name in NON_CUSTODIAL:
        return "other_labeled"
    return "exchange"


def _flags(ctx, accounts: list[str]) -> pd.DataFrame:
    rows, now = [], datetime.now(timezone.utc).replace(tzinfo=None)
    for a in accounts:
        j = ctx.http.post(RPC_URL, json={"method": "account_info",
                                         "params": [{"account": a, "ledger_index": "validated"}]}).json()
        res = j.get("result", {})
        d = res.get("account_data")
        if d is None:
            rows.append({"account": a, "domain": None, "require_dest": False, "exists": False, "checked_at": now})
            continue
        dom = d.get("Domain")
        try:
            dom = bytes.fromhex(dom).decode("utf-8", "replace") if dom else None
        except ValueError:
            dom = None
        rows.append({"account": a, "domain": dom, "require_dest": bool(int(d.get("Flags", 0)) & LSF_REQUIRE_DEST),
                     "exists": True, "checked_at": now})
    return pd.DataFrame(rows)


def _snapshot(ctx) -> int:
    rl = ctx.http.get_json(RICHLIST_URL)
    df = pd.DataFrame({
        "account": [r["account"] for r in rl],
        "balance_xrp": [r.get("balance", 0) / DROPS for r in rl],
        "escrow_xrp": [(r.get("escrow") or 0) / DROPS for r in rl],
        "name": [(r.get("name") or {}).get("name") for r in rl],
    })
    df["label_category"] = df["name"].map(_category)
    df.insert(0, "date", datetime.now(timezone.utc).date())
    df["snapshot_ts"] = datetime.now(timezone.utc).replace(tzinfo=None)
    return db.upsert(ctx.con, "xrpl_richlist", df, ["date", "account"])


def _top_up_flags(ctx) -> int:
    """Ledger flags for the biggest unlabeled accounts (latest snapshot) that are not cached or are stale."""
    cached = "xrpl_accounts" if db.table_exists(ctx.con, "xrpl_accounts") else None
    q = f"""SELECT r.account FROM xrpl_richlist r
            {f"LEFT JOIN {cached} a ON a.account = r.account" if cached else ""}
            WHERE r.date = (SELECT max(date) FROM xrpl_richlist) AND r.label_category IS NULL
              AND r.balance_xrp >= {FLAG_MIN_XRP}
              {f"AND (a.account IS NULL OR a.checked_at < now() - INTERVAL {FLAG_REFRESH_DAYS} DAY)" if cached else ""}
            ORDER BY r.balance_xrp DESC LIMIT {MAX_FLAG_LOOKUPS}"""
    need = [r[0] for r in ctx.con.execute(q).fetchall()]
    return db.upsert(ctx.con, "xrpl_accounts", _flags(ctx, need), ["account"]) if need else 0


def run(ctx, full=False):
    today = datetime.now(timezone.utc).date()
    have_today = db.table_exists(ctx.con, "xrpl_richlist") and ctx.con.execute(
        "SELECT count(*) FROM xrpl_richlist WHERE date = ?", [today]).fetchone()[0] > 0
    n = _snapshot(ctx) if full or not have_today else 0
    return {"xrpl_richlist": n, "xrpl_accounts": _top_up_flags(ctx)}
