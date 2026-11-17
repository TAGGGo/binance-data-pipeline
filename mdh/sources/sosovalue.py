"""
US spot crypto ETF daily net flows (BTC, ETH, SOL, XRP) from SoSoValue.

Free-tier reality (tested 2026-09-28):
  * v1 /etfs/summary-history   -> last 30 days, all four assets, 10 req/min, 10k/month
  * v2 historicalInflowChart   -> last 300 days, BTC/ETH/SOL only (XRP empty)
  * v2 currentEtfDataMetrics   -> today's per-fund numbers (net assets, daily flow, premium, value traded) and
                                  the total token holdings, BTC/ETH/SOL only. No history, so every run stores a
                                  snapshot keyed by SoSoValue's lastUpdateDate (tables etf_fund_snapshot,
                                  etf_holdings_daily); per-fund history builds up from 2026-09-29.
v2 host moved: api.sosovalue.xyz returns a Cloudflare 403 from 2026-10-09; the same v2 paths work on
openapi.sosovalue.com (fixed 2026-10-20). Per-fund snapshots for sessions 10/9-10/16 were missed (no history API).
Older history lives in seeds/etf/ (collected once from Farside + SoSoValue's site) and is
loaded by `seed_etf.py`. A daily run of this module keeps everything current.
"""
from __future__ import annotations

import os

import pandas as pd

from mdh import settings
from mdh.core import db

NAME = "sosovalue"
TABLE = "etf_flows_daily"
V1 = "https://openapi.sosovalue.com/openapi/v1/etfs/summary-history"
V2 = "https://openapi.sosovalue.com/openapi/v2/etf/historicalInflowChart"
V2_TYPES = {"BTC": "us-btc-spot", "ETH": "us-eth-spot", "SOL": "us-sol-spot"}
V2_CURRENT = "https://openapi.sosovalue.com/openapi/v2/etf/currentEtfDataMetrics"
CMC_ETF_FLOWS = "https://api.coinmarketcap.com/data-api/v3/etf/detail/netflow/list"


def cmc_fund_flows(ctx) -> dict[tuple[str, str], float]:
    """Per-fund daily USD net flows from CoinMarketCap (Farside/Bloomberg mirror), keyed by (YYYY-MM-DD, TICKER).

    Never use CMC's aggregate `total` directly (it mixes non-US ETFs and omits newer US funds);
    we only read individual fund cells here to fill pending funds on the latest session.
    """
    out: dict[tuple[str, str], float] = {}
    try:
        j = ctx.http.get_json(
            CMC_ETF_FLOWS,
            params={"category": "all", "size": 10, "page": 1},
            headers={"User-Agent": "Mozilla/5.0"},
        )
    except Exception as e:
        ctx.log.warning("cmc etf fallback failed: %s", e)
        return out
    for p in (j.get("data") or {}).get("points") or []:
        raw_d = p.get("date")
        if not raw_d or str(raw_d) == "0":
            continue
        d_str = pd.to_datetime(int(raw_d), unit="ms", utc=True).strftime("%Y-%m-%d")
        for k, v in p.items():
            if k not in ("date", "total") and isinstance(v, dict) and v.get("usd") is not None:
                out[(d_str, k.upper())] = float(v["usd"])
    return out


def _is_reported(fund: dict, day: str, num) -> bool:
    inf = fund.get("dailyNetInflow") or {}
    return inf.get("status") == "1" and inf.get("lastUpdateDate") == day and pd.notna(num(inf))


def _resolve_pending(pending: list[str], day: str, cmc: dict[tuple[str, str], float]) -> dict[str, float] | None:
    resolved: dict[str, float] = {}
    for tk in pending:
        if (day, tk) in cmc:
            resolved[tk] = cmc[(day, tk)]
        elif tk == "ETHB" and (day, "ETHA") in cmc:
            # CMC does not list BlackRock's staked ETH ETF (ETHB); once ETHA has printed on CMC,
            # unblock the ETH fast path with 0 until SoSoValue posts ETHB's exact number.
            resolved[tk] = 0.0
        else:
            return None
    return resolved


def _frame(asset, rows, date_key, m):
    df = pd.DataFrame(rows)
    return pd.DataFrame({
        "date": pd.to_datetime(df[date_key].str[:10]).dt.date,
        "asset": asset,
        "source": "sosovalue",
        "net_inflow_usd": pd.to_numeric(df[m["flow"]], errors="coerce"),
        "value_traded_usd": pd.to_numeric(df[m["traded"]], errors="coerce"),
        "net_assets_usd": pd.to_numeric(df[m["assets"]], errors="coerce"),
        "cum_net_inflow_usd": pd.to_numeric(df[m["cum"]], errors="coerce"),
    })


def run(ctx, full=False):
    key = os.environ.get("SOSOVALUE_API_KEY")
    if not key:
        raise RuntimeError("SOSOVALUE_API_KEY missing from .env")
    total = 0
    v1_dates: dict[str, set] = {}
    if full:   # 300-day window where available
        for asset, t in V2_TYPES.items():
            j = ctx.http.post(V2, json={"type": t}, headers={"x-soso-api-key": key}).json()
            if j.get("data"):
                total += db.upsert(ctx.con, TABLE, _frame(asset, j["data"], "date", {
                    "flow": "totalNetInflow", "traded": "totalValueTraded",
                    "assets": "totalNetAssets", "cum": "cumNetInflow"}), ["date", "asset", "source"])
    for asset in settings.ETF_ASSETS:   # last 30 days, every asset
        j = ctx.http.get_json(V1, params={"symbol": asset, "country_code": "US"},
                              headers={"x-soso-api-key": key})
        if j.get("code") != 0 or not j.get("data"):
            ctx.log.warning("sosovalue %s: %s", asset, j.get("message"))
            continue
        df_v1 = _frame(asset, j["data"], "date", {
            "flow": "total_net_inflow", "traded": "total_value_traded",
            "assets": "total_net_assets", "cum": "cum_net_inflow"})
        v1_dates[asset] = set(df_v1["date"])
        total += db.upsert(ctx.con, TABLE, df_v1, ["date", "asset", "source"])
    # per-fund snapshot of the latest session (+ total token holdings)
    snap_n = hold_n = 0
    num = lambda x: pd.to_numeric((x or {}).get("value"), errors="coerce")
    cmc_cache: dict[tuple[str, str], float] | None = None

    def get_cmc() -> dict[tuple[str, str], float]:
        nonlocal cmc_cache
        if cmc_cache is None:
            cmc_cache = cmc_fund_flows(ctx)
        return cmc_cache

    for asset, t in V2_TYPES.items():
        j = ctx.http.post(V2_CURRENT, json={"type": t}, headers={"x-soso-api-key": key}).json()
        dd = j.get("data") or {}
        day = (dd.get("dailyNetInflow") or {}).get("lastUpdateDate")
        if not day:
            continue
        day_date = pd.Timestamp(day).date()
        hold = pd.DataFrame([{"date": day_date, "asset": asset, "source": "sosovalue",
                              "token_holdings": num(dd.get("totalTokenHoldings")),
                              "net_assets_usd": num(dd.get("totalNetAssets")),
                              "daily_net_inflow_usd": num(dd.get("dailyNetInflow"))}])
        hold_n += db.upsert(ctx.con, "etf_holdings_daily", hold, ["date", "asset", "source"])
        # FAST PATH: the current-metrics endpoint shows the new session hours before summary-history does,
        # but its total is PARTIAL until every fund has reported (BlackRock IBIT/ETHA/ETHB come last,
        # status "3" = pending). When every fund is in, write SoSoValue's exact total; if some funds are
        # still pending, fill only the missing funds from CoinMarketCap's per-fund mirror and overwrite once
        # SoSoValue catches up (all before V1 summary-history populates `day_date`).
        funds = dd.get("list") or []
        reported = [f for f in funds if _is_reported(f, day, num)]
        pending = [str(f.get("ticker") or "").strip().upper() for f in funds if not _is_reported(f, day, num)]
        ctx.log.info("sosovalue current %s %s: %d/%d funds in%s", asset, day, len(reported), len(funds),
                     f" (pending {','.join(pending)})" if pending else " (complete)")
        asset_v1 = v1_dates.get(asset, set())
        is_newer_than_v1 = day_date not in asset_v1 and (not asset_v1 or day_date >= max(asset_v1))
        cmc = get_cmc() if pending else {}
        if funds and not pending and is_newer_than_v1:
            fast = pd.DataFrame([{"date": day_date, "asset": asset, "source": "sosovalue",
                                  "net_inflow_usd": num(dd.get("dailyNetInflow")),
                                  "value_traded_usd": num(dd.get("dailyTotalValueTraded")),
                                  "net_assets_usd": num(dd.get("totalNetAssets")),
                                  "cum_net_inflow_usd": num(dd.get("cumNetInflow"))}])
            total += db.upsert(ctx.con, TABLE, fast, ["date", "asset", "source"])
            ctx.log.info("sosovalue fast path: %s %s written from current metrics", asset, day)
        elif funds and pending and reported and is_newer_than_v1:
            resolved = _resolve_pending(pending, day, cmc)
            if resolved is not None:
                soso_sum = sum(float(num(f.get("dailyNetInflow"))) for f in reported)
                cmc_sum = sum(resolved.values())
                fast = pd.DataFrame([{"date": day_date, "asset": asset, "source": "sosovalue",
                                      "net_inflow_usd": soso_sum + cmc_sum,
                                      "value_traded_usd": num(dd.get("dailyTotalValueTraded")),
                                      "net_assets_usd": num(dd.get("totalNetAssets")),
                                      "cum_net_inflow_usd": float(num(dd.get("cumNetInflow")) or 0.0) + cmc_sum}])
                total += db.upsert(ctx.con, TABLE, fast, ["date", "asset", "source"])
                ctx.log.info("sosovalue+cmc fast path: %s %s written (pending %s filled from CMC)",
                             asset, day, ",".join(pending))
        rows = []
        for f in funds:
            tk = str(f.get("ticker") or "").strip()
            tk_u = tk.upper()
            if _is_reported(f, day, num):
                flow_val = num(f.get("dailyNetInflow"))
            elif (day, tk_u) in cmc:
                flow_val = cmc[(day, tk_u)]
            else:
                continue
            rows.append({"date": day_date, "asset": asset,
                         "ticker": tk, "institute": f.get("institute"), "source": "sosovalue",
                         "net_assets_usd": num(f.get("netAssets")), "daily_net_inflow_usd": flow_val,
                         "cum_net_inflow_usd": num(f.get("cumNetInflow")), "value_traded_usd": num(f.get("dailyValueTraded")),
                         "premium": num(f.get("discountPremiumRate")), "fee": num(f.get("fee"))})
        if rows:
            snap_n += db.upsert(ctx.con, "etf_fund_snapshot", pd.DataFrame(rows), ["date", "asset", "ticker", "source"])
    return {TABLE: total, "etf_fund_snapshot": snap_n, "etf_holdings_daily": hold_n}
