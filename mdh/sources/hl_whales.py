"""
Hyperliquid big accounts: positions, liquidation prices and stop / take-profit trigger orders.

Hyperliquid has no "all positions" endpoint, so we track a fixed watch list (added 2026-10-08):
  * once a day: download the public leaderboard (~40 MB, ~47k accounts) and keep the top
    settings.HL_WHALE_TOP_VALUE by account value + top settings.HL_WHALE_TOP_VOLUME by 30-day volume
    (account value >= $1M). Saved in data/state/hl_whales.json.
  * every run: clearinghouseState for each address (positions + liquidationPx), then frontendOpenOrders
    for addresses holding a tracked position >= HL_WHALE_MIN_POS_USD (trigger orders = their stops / TPs). ~2 calls/s, ~2-3 min.
Only coins in settings.HL_WHALE_COINS; positions >= HL_WHALE_MIN_POS_USD; trigger orders >= HL_WHALE_MIN_ORDER_USD.

Tables
  hl_whale_pos    (ts, address, coin, size, notional_usd, entry_px, liq_px, leverage, upnl_usd, account_value)
                  size > 0 long, < 0 short; liq_px NULL = no liquidation price (low leverage / cross with spare margin)
  hl_whale_orders (ts, address, coin, side, kind, trigger_px, limit_px, size, notional_usd, reduce_only)
                  side B = buy (closes shorts / opens longs), A = sell. kind = 'Stop Market', 'Take Profit Limit', ...
Read-out: scripts/hl_whales_brief.py [COIN]  (long/short totals, liquidation and stop clusters near price).
Caveat: a watch list, not the whole exchange; many big wallets are vaults/HLP or sub-accounts with no positions.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import pandas as pd
import requests

from mdh import settings
from mdh.core import db

INFO = "https://api.hyperliquid.xyz/info"
BOARD = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
STATE = settings.STATE_DIR / "hl_whales.json"


def _watchlist(log):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        st = json.loads(STATE.read_text())
        if st.get("date") == today and st.get("addresses"):
            return st["addresses"]
    except Exception:  # noqa: BLE001
        st = {}
    try:
        rows = requests.get(BOARD, timeout=120).json()["leaderboardRows"]
    except Exception as e:  # noqa: BLE001 - keep yesterday's list
        log.warning("hl_whales leaderboard: %s", str(e)[:150])
        return st.get("addresses") or []
    def vol(r):
        return float(dict(r["windowPerformances"]).get("month", {}).get("vlm", 0) or 0)
    by_val = sorted(rows, key=lambda r: -float(r["accountValue"]))[: settings.HL_WHALE_TOP_VALUE]
    big = [r for r in rows if float(r["accountValue"]) >= 1e6]
    by_vol = sorted(big, key=lambda r: -vol(r))[: settings.HL_WHALE_TOP_VOLUME]
    addrs = list(dict.fromkeys(r["ethAddress"] for r in by_val + by_vol))
    STATE.write_text(json.dumps({"date": today, "addresses": addrs}))
    return addrs


class hl_whales:
    NAME = "hl_whales"

    @staticmethod
    def run(ctx, full=False):
        addrs = _watchlist(ctx.log)
        coins = set(settings.HL_WHALE_COINS)
        ts = pd.Timestamp(datetime.now(timezone.utc).replace(tzinfo=None)).floor("min")
        s = requests.Session()
        def info(body, pause=None):
            for i in range(3):
                r = s.post(INFO, json=body, timeout=20)
                if r.status_code == 429:
                    time.sleep(5 * (i + 1))
                    continue
                r.raise_for_status()
                time.sleep(settings.HL_WHALE_SLEEP if pause is None else pause)
                return r.json()
            return {}
        pos, orders, holders = [], [], []
        for a in addrs:
            try:
                st = info({"type": "clearinghouseState", "user": a})
            except Exception as e:  # noqa: BLE001
                ctx.log.warning("hl_whales %s: %s", a[:10], str(e)[:120])
                continue
            av = float((st.get("marginSummary") or {}).get("accountValue") or 0)
            mine = False
            for p in st.get("assetPositions") or []:
                q = p.get("position") or {}
                if q.get("coin") not in coins:
                    continue
                v = float(q.get("positionValue") or 0)
                if v < settings.HL_WHALE_MIN_POS_USD:
                    continue
                mine = True
                pos.append({"ts": ts, "address": a, "coin": q["coin"], "size": float(q["szi"]), "notional_usd": v,
                            "entry_px": float(q.get("entryPx") or 0),
                            "liq_px": float(q["liquidationPx"]) if q.get("liquidationPx") else None,
                            "leverage": float((q.get("leverage") or {}).get("value") or 0),
                            "upnl_usd": float(q.get("unrealizedPnl") or 0), "account_value": av})
            if mine:
                holders.append(a)
        for a in holders:
            try:
                oo = info({"type": "frontendOpenOrders", "user": a}, pause=1.0) or []   # weight 20: stay < 1200/min
            except Exception as e:  # noqa: BLE001
                ctx.log.warning("hl_whales orders %s: %s", a[:10], str(e)[:120])
                continue
            for o in oo:
                if o.get("coin") not in coins or not o.get("isTrigger"):
                    continue
                trig = float(o.get("triggerPx") or 0)
                sz = float(o.get("sz") or 0) or float(o.get("origSz") or 0)
                if sz * trig < settings.HL_WHALE_MIN_ORDER_USD:
                    continue
                orders.append({"ts": ts, "address": a, "coin": o["coin"], "side": o.get("side"), "kind": o.get("orderType"),
                               "trigger_px": trig, "limit_px": float(o.get("limitPx") or 0), "size": sz,
                               "notional_usd": sz * trig, "reduce_only": bool(o.get("reduceOnly"))})
        n1 = db.upsert(ctx.con, "hl_whale_pos", pd.DataFrame(pos), ["ts", "address", "coin"]) if pos else 0
        n2 = db.upsert(ctx.con, "hl_whale_orders", pd.DataFrame(orders).drop_duplicates(
            ["ts", "address", "coin", "side", "trigger_px"]), ["ts", "address", "coin", "side", "trigger_px"]) if orders else 0
        ctx.log.info("hl_whales: %d addresses, %d with positions, %d positions, %d trigger orders",
                     len(addrs), len(holders), n1, n2)
        return {"hl_whale_pos": n1, "hl_whale_orders": n2}
