"""
NEAR: NEAR Intents activity (DefiLlama) and NEAR total supply (NEAR RPC). Feeds the NEAR page's
"Intents vs inflation" card (mdh/dashboard/onchain.py near_intents).

  near_intents_daily (date, volume_usd, fees_usd, revenue_usd)
      DefiLlama adapter "near-intents": /summary/dexs (volume), /summary/fees?dataType=dailyFees|dailyRevenue.
      Revenue = what the protocol keeps (DefiLlama's definition); this is the pool the announced buyback would use.
      The newest day can be partial. Whole history rewritten each run (a few hundred rows).
  near_supply (ts, height, total_supply)   total_supply in NEAR, from the block header (net of burnt fees)
      One point per run from rpc.mainnet.near.org. `--full` also samples one block a week back to
      settings.NEAR_SUPPLY_START from the archival RPC (fastnear), so realized inflation is measured, not assumed.
      The initial history (2024-01 .. 2026-09) was loaded from data/backfill/near_supply.csv (built in the cloud).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from mdh import settings
from mdh.core import db

NAME = "near"
LLAMA = "https://api.llama.fi/summary/{kind}/{slug}"
RPC = "https://rpc.mainnet.near.org"
ARCHIVAL = "https://archival-rpc.mainnet.fastnear.com"   # near.org archival answers 429 almost always
YOCTO = 1e24


def _chart(ctx, kind, params=None) -> pd.Series:
    j = ctx.http.get_json(LLAMA.format(kind=kind, slug=settings.NEAR_INTENTS_SLUG), params=params, timeout=90)
    ch = j.get("totalDataChart") or []
    return pd.Series({datetime.fromtimestamp(t, timezone.utc).date(): float(v) for t, v in ch}, dtype=float)


def _block(ctx, url, block_id=None):
    p = {"finality": "final"} if block_id is None else {"block_id": int(block_id)}
    j = ctx.http.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "block", "params": p}, timeout=60).json()
    if "result" not in j:
        return None
    h = j["result"]["header"]
    return {"ts": datetime.fromtimestamp(int(h["timestamp"]) / 1e9, timezone.utc).replace(tzinfo=None),
            "height": int(h["height"]), "total_supply": int(h["total_supply"]) / YOCTO}


def _history(ctx, now: dict) -> list[dict]:
    """One block per week back to NEAR_SUPPLY_START. Height estimated from the average block time, then used as is
    (the stored ts is the block's own timestamp). Skipped heights (no block) retry a few heights later."""
    ref = None
    for h in (now["height"] - 20_000_000, now["height"] - 5_000_000):
        ref = _block(ctx, ARCHIVAL, h)
        if ref:
            break
    if not ref:
        return []
    sec_per_block = (now["ts"] - ref["ts"]).total_seconds() / (now["height"] - ref["height"])
    out, t = [], pd.Timestamp(now["ts"]).normalize() - pd.Timedelta(days=7)
    while t >= pd.Timestamp(settings.NEAR_SUPPLY_START):
        est = now["height"] - int((now["ts"] - t.to_pydatetime()).total_seconds() / sec_per_block)
        for k in range(5):
            b = _block(ctx, ARCHIVAL, est + k)
            if b:
                out.append(b)
                break
        t -= pd.Timedelta(days=7)
    return out


def run(ctx, full=False):
    res = {}
    vol = _chart(ctx, "dexs")
    fees = _chart(ctx, "fees", {"dataType": "dailyFees"})
    rev = _chart(ctx, "fees", {"dataType": "dailyRevenue"})
    df = pd.DataFrame({"volume_usd": vol, "fees_usd": fees, "revenue_usd": rev})
    df.index.name = "date"
    res["near_intents_daily"] = db.upsert(ctx.con, "near_intents_daily", df.reset_index(), ["date"])
    now = _block(ctx, RPC)
    rows = [now] if now else []
    if now and full:          # weekly history: only on --full (the archival RPC rate-limits hard; never in the hourly run)
        rows += _history(ctx, now)
    res["near_supply"] = db.upsert(ctx.con, "near_supply", pd.DataFrame(rows), ["height"]) if rows else 0
    return res
