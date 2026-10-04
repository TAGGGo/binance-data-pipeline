"""
Deribit options snapshot (free public API, no key). One call per currency per run.

Deribit has no history API for option open interest / skew, so like Hyperliquid OI we snapshot every hourly
run and build forward (first snapshot 2026-10-02).

Tables
  deribit_opt_summary (currency, ts, spot, dvol, call_oi, put_oi, pc_oi, call_vol24, put_vol24, pc_vol24,
                       oi_usd, front_expiry, front_max_pain, main_expiry, main_max_pain, main_oi_usd, atm_iv_front, rr25_front, atm_iv_30d, rr25_30d)
  deribit_opt_expiry  (currency, ts, expiry, days, call_oi, put_oi, oi_usd, max_pain, atm_iv, rr25,
                       top_call_strike, top_call_oi, top_put_strike, top_put_oi)
  OI / volume are in coins (1 contract = 1 BTC / 1 ETH). IV in vol points. rr25 = 25-delta call IV minus
  25-delta put IV (positive = calls richer = upside demand; negative = puts richer = hedging/fear).
  main_* = the expiry with the most OI within 45 days (usually the monthly), the one whose max pain matters.
  Expiries shorter than 1 day are skipped for IV/skew (noisy); max pain is computed for every expiry.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from mdh import settings
from mdh.core import db

BOOK = "https://www.deribit.com/api/v2/public/get_book_summary_by_currency"
DVOL = "https://www.deribit.com/api/v2/public/get_volatility_index_data"


def _ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _delta(S, K, T, iv, is_call):
    if T <= 0 or iv <= 0:
        return np.nan
    d1 = (math.log(S / K) + 0.5 * iv * iv * T) / (iv * math.sqrt(T))
    return _ncdf(d1) if is_call else _ncdf(d1) - 1


def parse_book(rows: list[dict], now: pd.Timestamp) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    parts = df["instrument_name"].str.split("-", expand=True)
    df["expiry"] = pd.to_datetime(parts[1], format="%d%b%y") + pd.Timedelta(hours=8)   # 08:00 UTC settlement
    df["strike"] = parts[2].astype(float)
    df["is_call"] = parts[3] == "C"
    df["T"] = (df["expiry"] - now).dt.total_seconds() / (365 * 86400)
    df["iv"] = df["mark_iv"].astype(float) / 100
    df["S"] = df["underlying_price"].astype(float)
    df["delta"] = [_delta(s, k, t, v, c) for s, k, t, v, c in zip(df.S, df.strike, df["T"], df.iv, df.is_call)]
    return df[df["T"] > 0]


def max_pain(g: pd.DataFrame) -> float | None:
    if g["open_interest"].sum() <= 0:
        return None
    ks = np.sort(g["strike"].unique())
    c = g[g.is_call]
    p = g[~g.is_call]
    pay = [(c.open_interest * np.maximum(P - c.strike, 0)).sum() + (p.open_interest * np.maximum(p.strike - P, 0)).sum()
           for P in ks]
    return float(ks[int(np.argmin(pay))])


def _iv_at_delta(side: pd.DataFrame, target: float):
    s = side.dropna(subset=["delta"]).sort_values("delta")
    if len(s) < 2 or not (s.delta.min() <= target <= s.delta.max()):
        return np.nan
    return float(np.interp(target, s.delta.values, s.iv.values)) * 100


def expiry_stats(g: pd.DataFrame) -> dict:
    S = float(g.S.median())
    c, p = g[g.is_call], g[~g.is_call]
    atm_k = g.strike.iloc[(g.strike - S).abs().argsort().iloc[0]]
    atm = g[g.strike == atm_k].iv.mean() * 100
    rr = _iv_at_delta(c, 0.25) - _iv_at_delta(p, -0.25)
    tc = c.loc[c.open_interest.idxmax()] if len(c) and c.open_interest.max() > 0 else None
    tp = p.loc[p.open_interest.idxmax()] if len(p) and p.open_interest.max() > 0 else None
    return {
        "days": float(g["T"].iloc[0] * 365),
        "call_oi": float(c.open_interest.sum()), "put_oi": float(p.open_interest.sum()),
        "max_pain": max_pain(g),
        "atm_iv": float(atm) if g["T"].iloc[0] * 365 >= 1 else np.nan,
        "rr25": float(rr) if g["T"].iloc[0] * 365 >= 1 else np.nan,
        "top_call_strike": None if tc is None else float(tc.strike), "top_call_oi": None if tc is None else float(tc.open_interest),
        "top_put_strike": None if tp is None else float(tp.strike), "top_put_oi": None if tp is None else float(tp.open_interest),
    }


def _interp_30d(ex: pd.DataFrame, col: str):
    e = ex.dropna(subset=[col]).sort_values("days")
    if len(e) < 2 or not (e.days.min() <= 30 <= e.days.max()):
        return np.nan
    return float(np.interp(30, e.days.values, e[col].values))


def snapshot(book_rows: list[dict], currency: str, now: pd.Timestamp, spot: float, dvol: float | None):
    df = parse_book(book_rows, now)
    ts = now.floor("min")
    ex = pd.DataFrame([{"expiry": k, **expiry_stats(g)} for k, g in df.groupby("expiry")]).sort_values("expiry")
    ex["oi_usd"] = (ex.call_oi + ex.put_oi) * spot
    ex.insert(0, "ts", ts)
    ex.insert(0, "currency", currency)
    calls, puts = df[df.is_call], df[~df.is_call]
    front = ex[ex.days >= 1].iloc[0] if (ex.days >= 1).any() else ex.iloc[0]
    near = ex[ex.days <= 45]
    main = near.loc[near.oi_usd.idxmax()] if len(near) else front   # the big expiry that matters (usually the monthly)
    summ = {
        "currency": currency, "ts": ts, "spot": spot, "dvol": dvol,
        "call_oi": float(calls.open_interest.sum()), "put_oi": float(puts.open_interest.sum()),
        "call_vol24": float(calls.volume.sum()), "put_vol24": float(puts.volume.sum()),
    }
    summ["pc_oi"] = summ["put_oi"] / summ["call_oi"] if summ["call_oi"] else None
    summ["pc_vol24"] = summ["put_vol24"] / summ["call_vol24"] if summ["call_vol24"] else None
    summ["oi_usd"] = (summ["call_oi"] + summ["put_oi"]) * spot
    summ.update({"front_expiry": front.expiry, "front_max_pain": front.max_pain, "atm_iv_front": front.atm_iv,
                 "rr25_front": front.rr25,
                 "main_expiry": main.expiry, "main_max_pain": main.max_pain, "main_oi_usd": main.oi_usd, "atm_iv_30d": _interp_30d(ex, "atm_iv"), "rr25_30d": _interp_30d(ex, "rr25")})
    return pd.DataFrame([summ]), ex


class deribit_options:
    NAME = "deribit_options"

    @staticmethod
    def run(ctx, full=False):
        now = pd.Timestamp(datetime.now(timezone.utc).replace(tzinfo=None))
        n1 = n2 = 0
        for cur in settings.DERIBIT_OPTIONS:
            rows = ctx.http.get_json(BOOK, params={"currency": cur, "kind": "option"})["result"]
            spot = float(np.median([r["estimated_delivery_price"] for r in rows]))
            ms = int(now.timestamp() * 1000)
            dv = ctx.http.get_json(DVOL, params={"currency": cur, "resolution": "60",
                                                 "start_timestamp": ms - 3 * 3600_000, "end_timestamp": ms})
            data = dv["result"]["data"]
            dvol = float(data[-1][4]) if data else None
            summ, ex = snapshot(rows, cur, now, spot, dvol)
            n1 += db.upsert(ctx.con, "deribit_opt_summary", summ, ["currency", "ts"])
            n2 += db.upsert(ctx.con, "deribit_opt_expiry", ex, ["currency", "ts", "expiry"])
        return {"deribit_opt_summary": n1, "deribit_opt_expiry": n2}
