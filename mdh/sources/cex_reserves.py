"""
Exchange balances per coin from DefiLlama's CEX transparency data (free, no key).

DefiLlama tracks the wallets that exchanges publish (proof-of-reserves lists, disclosed cold wallets) and values
their token holdings daily: https://api.llama.fi/protocol/<slug> -> "tokens" = [{date, tokens: {SYMBOL: amount}}].
We keep, for each exchange in settings.CEX_RESERVE_EXCHANGES and each coin-page coin, the last value of every UTC day.

Coverage (checked 2026-09-30): ~30 exchanges incl. Binance, OKX, Bitfinex, Bybit, Robinhood, Gate, Bitget, Gemini,
KuCoin, Crypto.com, Bitstamp. NOT covered: Coinbase, Kraken, Upbit, Bithumb (no published wallet list), so this is
"balances on exchanges that publish their wallets", not all exchanges. ZEC and DASH are not tracked by DefiLlama at
all (shielded / non-EVM chains); HYPE only on a few venues.

Caveat: an exchange adding or retiring a wallet on its list shows up as a step in its balance. The step filter lives
in mdh/dashboard/onchain.py (exchange_balance), not here; this table is the raw daily value.

Table cex_reserves_daily (date, exchange, symbol, amount)   amount in coins
The endpoint returns full history each call (Binance ~45 MB), so it runs once per UTC day (first run after 01:00 UTC)
and rewrites the last 10 days; --full rewrites everything.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from mdh import settings
from mdh.core import db

NAME = "cex_reserves"
TABLE = "cex_reserves_daily"
URL = "https://api.llama.fi/protocol/{slug}"


def parse(j: dict, exchange: str, symbols: dict[str, list[str]]) -> pd.DataFrame:
    """DefiLlama protocol JSON -> one row per (UTC day, symbol) with the day's last amount. Aliases are summed
    (ETH = native ETH + DefiLlama's 'WETH', which is how it labels native ETH on several chains)."""
    rows = []
    for r in j.get("tokens") or []:
        tk = r.get("tokens") or {}
        ts = datetime.fromtimestamp(r["date"], timezone.utc).replace(tzinfo=None)
        for sym, keys in symbols.items():
            vals = [tk[k] for k in keys if k in tk and tk[k] is not None]
            if vals:
                rows.append((ts, sym, float(sum(vals))))
    if not rows:
        return pd.DataFrame(columns=["date", "exchange", "symbol", "amount"])
    df = pd.DataFrame(rows, columns=["ts", "symbol", "amount"]).sort_values("ts")
    df["date"] = df["ts"].dt.date
    df = df.groupby(["date", "symbol"], as_index=False)["amount"].last()
    df.insert(1, "exchange", exchange)
    return df


def run(ctx, full=False):
    now = datetime.now(timezone.utc)
    if not full and db.table_exists(ctx.con, TABLE):
        last = ctx.con.execute(f"SELECT max(date) FROM {TABLE}").fetchone()[0]
        if last is not None and (str(last) >= now.date().isoformat() or now.hour < 1):
            return {TABLE: 0}          # already done today (or too early: DefiLlama's daily point lands ~00:xx UTC)
    symbols = {c: settings.CEX_RESERVE_ALIASES.get(c, [c]) for c in settings.COIN_PAGES}
    first = not db.table_exists(ctx.con, TABLE)
    total, errors = 0, []
    for slug in settings.CEX_RESERVE_EXCHANGES:
        try:
            j = ctx.http.get_json(URL.format(slug=slug), timeout=180)
        except Exception as e:     # one exchange failing shouldn't drop the rest
            errors.append(f"{slug}: {e}")
            continue
        df = parse(j, slug, symbols)
        if not (full or first):
            df = df[df["date"] >= (now.date() - pd.Timedelta(days=10))]
        total += db.upsert(ctx.con, TABLE, df, ["date", "exchange", "symbol"])
    if errors and total == 0:
        raise RuntimeError("; ".join(errors)[:400])
    return {TABLE: total}
