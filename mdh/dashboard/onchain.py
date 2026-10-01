"""
On-chain / exchange-balance cards for the coin pages. Each function returns (series for coins/<SYM>.json,
summary for data.json["coins"][SYM]) or (None, None) when there is no data.

  exchange_balance   every coin DefiLlama tracks: coins held on exchanges that publish their wallets
  xrp_whales         XRP only: accumulation by large unlabeled XRPL accounts (a Glassnode-style breadth score)
  near_intents       NEAR only: Intents volume / revenue vs the new supply NEAR issues each year

Numbers are descriptive; none of these has been backtested as a signal.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from mdh.core import db


def _r(v, nd=2):
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return round(v, nd)


def _sig(v, n=6):
    v = _r(v, 12)
    if v is None or v == 0:
        return v
    d = n - int(math.floor(math.log10(abs(v)))) - 1
    return round(v, max(d, 0)) if d >= 0 else float(round(v, d))


def _t(ix) -> list[int]:
    return [int(pd.Timestamp(t).timestamp()) for t in ix]


# ============================================================================ exchange balances (DefiLlama CEX data)
XB_START = "2023-01-01"
XB_JUMP = 0.25       # an exchange's balance moving more than 25% in one day is treated as a wallet-list change
XB_MIN_USD = 25e6    # hide the card when the tracked balance is smaller than this
XB_FFILL = 3         # an exchange missing for up to 3 days keeps its last value; longer gaps restart it


def exchange_flows(wide: pd.DataFrame, jump: float = XB_JUMP, ffill: int = XB_FFILL) -> pd.DataFrame:
    """wide = date x exchange balances (coins). Returns per date: raw total, net flow with wallet-list steps
    removed, the step-adjusted balance (anchored to today's raw total), and the excluded steps.

    flow(t)   = sum over exchanges of balance(t) - balance(t-1), counting only days where the exchange had a value
                on both days and moved by at most `jump` of its balance. Bigger one-day moves are almost always an
                exchange adding / dropping wallets on its published list (checked by hand for BTC/ETH/XRP/SOL/NEAR
                2025-2026), so they are left out of flows (a real move that large is lost too).
    adjusted(t) = total(today) - sum of flows after t: the balance path you get if the wallet lists never changed."""
    w = wide.sort_index().ffill(limit=ffill)
    prev = w.shift(1)
    d = w - prev
    ok = prev.notna() & w.notna() & (prev > 0)
    step = ok & (d.abs() > jump * prev)
    flow = d.where(ok & ~step, 0.0).sum(axis=1)
    total = w.sum(axis=1, min_count=1)
    after = flow[::-1].cumsum()[::-1].shift(-1).fillna(0.0)
    out = pd.DataFrame({"total": total, "flow": flow, "adjusted": total.iloc[-1] - after,
                        "n": w.notna().sum(axis=1), "steps": step.sum(axis=1),
                        "step_coins": d.where(step, 0.0).sum(axis=1)})
    out.attrs["step_list"] = [(t, ex, float(d.at[t, ex]), float(prev.at[t, ex])) for t, ex in step.stack()[step.stack()].index]
    return out


def exchange_balance(con, asset: str, price: float | None) -> tuple[dict | None, dict | None]:
    if not db.table_exists(con, "cex_reserves_daily"):
        return None, None
    df = con.execute("""SELECT date, exchange, amount FROM cex_reserves_daily WHERE symbol = ? AND date >= ?
                        ORDER BY date""", [asset, XB_START]).df()
    if df.empty:
        return None, None
    wide = df.pivot_table(index="date", columns="exchange", values="amount", aggfunc="last")
    wide.index = pd.to_datetime(wide.index)
    wide = wide.loc[:, wide.iloc[-60:].max() > 0]            # drop exchanges that no longer hold the coin
    if wide.empty or wide.iloc[-1].sum() <= 0:
        return None, None
    x = exchange_flows(wide)
    total = float(x["total"].iloc[-1])
    if price and total * price < XB_MIN_USD:                  # e.g. ZEC: DefiLlama sees ~$0.5M of it, meaningless
        return None, None
    def net(n):
        return float(x["flow"].iloc[-n:].sum()) if len(x) > n else None
    f30 = x["flow"].rolling(30).sum()
    hist = f30.dropna().iloc[-365:]
    pct30 = float((hist < hist.iloc[-1]).mean() * 100) if len(hist) > 60 else None
    last = wide.iloc[-1].dropna().sort_values(ascending=False)
    ago30 = wide.iloc[-31] if len(wide) > 30 else None
    top = [{"ex": ex, "amt": _sig(v), "share": _r(v / total * 100, 1),
            "chg30": _sig(v - ago30[ex]) if ago30 is not None and not pd.isna(ago30.get(ex)) else None}
           for ex, v in last.head(6).items()]
    steps30 = [s for s in x.attrs["step_list"] if s[0] >= x.index[-30]]
    s = {"t": x.index[-1].date().isoformat(), "total": _sig(total), "usd": _r(total * price, 0) if price else None,
         "n_ex": int(x["n"].iloc[-1]), "net1": _sig(net(1)), "net7": _sig(net(7)), "net30": _sig(net(30)),
         "net30_pct": _r(net(30) / total * 100, 2) if net(30) is not None else None,
         "net7_usd": _r(net(7) * price, 0) if price and net(7) is not None else None,
         "net30_usd": _r(net(30) * price, 0) if price and net(30) is not None else None,
         "pct30_1y": _r(pct30, 0), "top": top,
         "steps30": [{"t": t.date().isoformat(), "ex": ex, "d": _sig(dv), "pct": _r(dv / pv * 100, 0)} for t, ex, dv, pv in steps30][-6:]}
    s["readout"] = xb_readout(asset, s)
    tail = x.iloc[-400:]
    series = {"t": _t(tail.index), "total": [_sig(v) for v in tail["total"]], "adj": [_sig(v) for v in tail["adjusted"]],
              "flow": [_sig(v) for v in tail["flow"]]}
    return series, s


def _amt(v, a):
    if v is None:
        return "–"
    av = abs(v)
    u = f"{av / 1e9:,.2f}B" if av >= 1e9 else f"{av / 1e6:,.2f}M" if av >= 1e6 else f"{av / 1e3:,.1f}K" if av >= 1e4 else f"{av:,.0f}"
    return ("−" if v < 0 else "+") + u + " " + a


def xb_readout(a: str, s: dict) -> dict:
    n30, p = s["net30"], s["pct30_1y"]
    side = ("coins leaving exchanges (usually withdrawals to self-custody or funds: less supply on hand to sell)" if n30 < 0
            else "coins arriving on exchanges (usually deposits ahead of selling or to post as margin)")
    rank = ""
    if p is not None:
        rank = (f" That 30-day net flow is lower than {100 - p:.0f}% of the past year's 30-day windows (a large outflow)." if p <= 15
                else f" That 30-day net flow is higher than {p:.0f}% of the past year's 30-day windows (a large inflow)." if p >= 85
                else " That is within the normal range of the past year.")
    text = (f"{s['n_ex']} exchanges that publish wallets hold {_amt(s['total'], a).lstrip('+')}. "
            f"Net {_amt(s['net7'], a)} over 7 days and {_amt(n30, a)} over 30 days ({s['net30_pct']:+.2f}% of their balance): {side}.{rank}")
    tone = "bull" if p is not None and p <= 15 else "bear" if p is not None and p >= 85 else ""
    return {"k": "Exchange balances", "text": text, "tone": tone}


# ============================================================================ XRP whales (XRPL, unlabeled >= 1M XRP)
WHALE_MIN = 1e6
WHALE_WINDOWS = (7, 30)


def whale_matrix(con) -> pd.DataFrame | None:
    """date x account balances for the whale cohort: accounts XRPScan hasn't labeled, not flagged as custodial by
    their ledger settings (v_xrpl_richlist category 'unlabeled'), holding >= 1M XRP on some day we have.
    Days before 2026-09-29 come from the account_tx backfill (xrpl_balance_hist), later days from the daily rich
    list snapshots. An account that drops out of the top 10,000 counts as 0 from then on."""
    if not db.table_exists(con, "xrpl_richlist"):
        return None
    snap = con.execute("""SELECT date, account, balance_xrp, category FROM v_xrpl_richlist""").df()
    cats = snap.sort_values("date").groupby("account")["category"].last()
    cohort = set(cats[cats == "unlabeled"].index)
    big = set(snap.loc[snap["balance_xrp"] >= WHALE_MIN, "account"])
    parts = [snap[snap["account"].isin(cohort)][["date", "account", "balance_xrp"]]]
    if db.table_exists(con, "xrpl_balance_hist"):
        h = con.execute("SELECT date, account, balance_xrp FROM xrpl_balance_hist").df()
        big |= set(h.loc[h["balance_xrp"] >= WHALE_MIN, "account"])
        busy = set(con.execute("SELECT account FROM xrpl_backfill_accounts WHERE capped OR error IS NOT NULL").df()["account"]) \
            if db.table_exists(con, "xrpl_backfill_accounts") else set()
        cohort -= busy               # too many transactions to rebuild: behaves like a service, leave it out
        parts.append(h[h["account"].isin(cohort)])
    m = pd.concat(parts).pivot_table(index="date", columns="account", values="balance_xrp", aggfunc="last")
    m.index = pd.to_datetime(m.index)
    m = m.loc[:, [c for c in m.columns if c in cohort and c in big]]
    snap_days = pd.to_datetime(sorted(snap["date"].unique()))
    m.loc[m.index.isin(snap_days)] = m.loc[m.index.isin(snap_days)].fillna(0.0)   # absent from a snapshot = below top 10k
    return m.sort_index()


def whale_scores(m: pd.DataFrame, n: int, min_start: float = WHALE_MIN) -> pd.DataFrame:
    """For each day t, over the window (t-n, t]:
      score    = balance-weighted share of whales whose balance rose, among whales whose balance changed:
                 sum(b0 for d > 0) / sum(b0 for d != 0), b0 = balance at t-n, d = b(t) - b0. 1 = every active whale
                 added, 0 = every active whale cut, 0.5 = even. Dormant whales (d = 0) don't count either way.
      Only accounts that were already whales (>= min_start) at t-n count as "existing".
      net      = sum of d over those existing whales (coins)
      new      = balance at t of accounts that were not whales at t-n (fresh or near-empty wallets that got filled:
                 new buyers, or a holder / institution spreading coins over new addresses; on-chain data alone
                 can't tell them apart. Example: 13 fresh wallets got exactly 90-100M XRP each around 2026-09-23)."""
    b0 = m.shift(n)
    d = m - b0
    live = b0 >= min_start
    moved = live & (d.abs() > np.maximum(1.0, 1e-6 * b0))          # ignore dust (fees, rounding)
    up = b0.where(moved & (d > 0), 0).sum(axis=1)
    act = b0.where(moved, 0).sum(axis=1)
    out = pd.DataFrame({"score": up / act.replace(0, np.nan), "net": d.where(live, 0).sum(axis=1),
                        "new": m.where(~live & (m > 0), 0).sum(axis=1),
                        "held": m.sum(axis=1, min_count=1), "n": m.notna().sum(axis=1)})
    out.loc[b0.notna().sum(axis=1) < 0.5 * m.notna().sum(axis=1), ["score", "net", "new"]] = np.nan   # window starts before our data
    return out


def xrp_whales(con, price: float | None) -> tuple[dict | None, dict | None]:
    m = whale_matrix(con)
    if m is None or m.shape[1] < 50:
        return None, None
    sc = {n: whale_scores(m, n) for n in WHALE_WINDOWS}
    s30, s7 = sc[30], sc[7]
    last = s30.index[-1]
    px = None
    try:
        p = con.execute("""SELECT CAST(ts AS DATE) d, close FROM bn_kline_1d WHERE market='spot' AND symbol='XRP' AND ts >= ?
                           ORDER BY ts""", [m.index[0].to_pydatetime()]).df()
        px = pd.Series(p["close"].to_numpy(float), index=pd.to_datetime(p["d"]))
    except Exception:
        pass
    def pick(frame, k):
        v = frame[k].dropna()
        return v.iloc[-1] if len(v) else None
    s = {"t": last.date().isoformat(), "whales": int(m.iloc[-1].gt(0).sum()), "held": _sig(m.iloc[-1].sum()),
         "score30": _r(pick(s30, "score"), 3), "score7": _r(pick(s7, "score"), 3),
         "net30": _sig(pick(s30, "net")), "net7": _sig(pick(s7, "net")), "new30": _sig(pick(s30, "new")),
         "net30_usd": _r((pick(s30, "net") or 0) * price, 0) if price else None,
         "first": m.index[0].date().isoformat(), "backfill_until": None}
    sd = s30["score"].dropna()
    if len(sd) > 20:
        s["score30_min"], s["score30_max"] = _r(sd.min(), 3), _r(sd.max(), 3)
        s["score30_20d_ago"] = _r(sd.iloc[-21], 3) if len(sd) > 21 else None
    if db.table_exists(con, "xrpl_balance_hist"):
        s["backfill_until"] = str(con.execute("SELECT max(date) FROM xrpl_balance_hist").fetchone()[0])
    s["readout"] = whale_readout(s)
    ix = s30.index
    series = {"t": _t(ix), "score30": [_r(v, 3) for v in s30["score"]], "score7": [_r(v, 3) for v in s7["score"].reindex(ix)],
              "net30": [_sig(v) for v in s30["net"]], "held": [_sig(v) for v in s30["held"]],
              "px": [_sig(v) for v in (px.reindex(ix) if px is not None else pd.Series(index=ix, dtype=float))]}
    return series, s


def whale_readout(s: dict) -> dict:
    sc = s["score30"]
    if sc is None:
        return {"k": "XRP whales", "text": "Not enough history yet for a 30-day read.", "tone": ""}
    lvl = ("strong accumulation: most whales that moved were adding" if sc >= 0.7 else
           "mild accumulation" if sc >= 0.55 else "balanced: adders and sellers roughly even" if sc > 0.45 else
           "mild distribution" if sc > 0.3 else "distribution: most whales that moved were cutting")
    trend = ""
    if s.get("score30_20d_ago") is not None:
        dd = sc - s["score30_20d_ago"]
        trend = f" It was {s['score30_20d_ago']:.2f} twenty days ago ({'rising' if dd > 0.05 else 'falling' if dd < -0.05 else 'about flat'})."
    if s.get("score30_min") is not None:
        trend += f" Since {s['first'][5:]} it has ranged {s['score30_min']:.2f}–{s['score30_max']:.2f}."
    text = (f"30-day score {sc:.2f} ({lvl}); 7-day {s['score7']:.2f}.{trend} {s['whales']:,} unlabeled wallets with 1M+ XRP "
            f"hold {s['held'] / 1e9:,.2f}B XRP; the ones that existed a month ago changed by {_amt(s['net30'], 'XRP')}, "
            f"and new 1M+ wallets hold {_amt(s['new30'], 'XRP').lstrip('+')}.")
    return {"k": "XRP whales", "text": text, "tone": "bull" if sc >= 0.6 else "bear" if sc <= 0.4 else ""}


# ============================================================================ NEAR Intents vs issuance
INFLATION_CAP = 0.025      # NEAR protocol max inflation after the 2025 halving vote (5% -> 2.5%)


def near_intents(con, price: float | None) -> tuple[dict | None, dict | None]:
    if not db.table_exists(con, "near_intents_daily"):
        return None, None
    d = con.execute("SELECT date, volume_usd, fees_usd, revenue_usd FROM near_intents_daily ORDER BY date").df()
    d["date"] = pd.to_datetime(d["date"])
    d = d[d["date"] < pd.Timestamp.utcnow().tz_localize(None).normalize()].set_index("date")   # drop today's partial
    if d.empty:
        return None, None
    s = {"t": d.index[-1].date().isoformat(), "vol_all": _r(d["volume_usd"].sum(), 0),
         "vol_7": _r(d["volume_usd"].iloc[-7:].sum(), 0), "vol_30": _r(d["volume_usd"].iloc[-30:].sum(), 0),
         "vol_prev30": _r(d["volume_usd"].iloc[-60:-30].sum(), 0),
         "rev_30": _r(d["revenue_usd"].iloc[-30:].sum(), 0), "fees_30": _r(d["fees_usd"].iloc[-30:].sum(), 0)}
    s["rev_annual"] = _r(s["rev_30"] * 365 / 30, 0) if s["rev_30"] is not None else None
    s["take_bp"] = _r(s["rev_30"] / s["vol_30"] * 1e4, 2) if s["vol_30"] else None
    sup = con.execute("SELECT ts, total_supply FROM near_supply ORDER BY ts").df() if db.table_exists(con, "near_supply") else pd.DataFrame()
    if len(sup) >= 2:
        sup["ts"] = pd.to_datetime(sup["ts"])
        now = sup.iloc[-1]
        s["supply"], s["supply_t"] = _r(now["total_supply"], 0), now["ts"].isoformat()[:16]
        def rate(days):
            old = sup[sup["ts"] <= now["ts"] - pd.Timedelta(days=days)]
            if old.empty:
                return None
            o = old.iloc[-1]
            yrs = (now["ts"] - o["ts"]).total_seconds() / (365.25 * 86400)
            return (now["total_supply"] / o["total_supply"]) ** (1 / yrs) - 1
        r365, r90 = rate(365), rate(90)
        s["infl_365"], s["infl_90"] = _r(r365 * 100 if r365 is not None else None, 3), _r(r90 * 100 if r90 is not None else None, 3)
        r = r90 if r90 is not None else r365
        if r is not None:
            s["issue_coins"] = _r(now["total_supply"] * r, 0)
            if price:
                s["issue_usd"] = _r(now["total_supply"] * r * price, 0)
                s["cover_pct"] = _r(s["rev_annual"] / s["issue_usd"] * 100, 1) if s.get("rev_annual") and s["issue_usd"] > 0 else None
                s["breakeven_px"] = _r(s["rev_annual"] / (now["total_supply"] * r), 4) if r > 0 else None
        if price:
            s["issue_cap_usd"] = _r(now["total_supply"] * INFLATION_CAP * price, 0)
    try:
        e = con.execute("""SELECT date, token_holdings, aum_usd, shares_outstanding FROM etf_fund_daily
                           WHERE ticker = 'NRR' ORDER BY date""").df()
        if len(e):
            s["nrr"] = {"t": str(e["date"].iloc[-1])[:10], "coins": _r(e["token_holdings"].iloc[-1], 0), "aum": _r(e["aum_usd"].iloc[-1], 0),
                        "chg_coins": _r(e["token_holdings"].iloc[-1] - e["token_holdings"].iloc[-2], 0) if len(e) > 1 else None,
                        "days": len(e)}
    except Exception:
        pass
    s["readout"] = near_readout(s)
    tail = d.iloc[-365:]
    series = {"t": _t(tail.index), "vol": [_r(v, 0) for v in tail["volume_usd"]], "rev": [_r(v, 0) for v in tail["revenue_usd"]]}
    if len(sup):
        series["sup_t"] = _t(sup["ts"])
        series["sup"] = [_r(v, 0) for v in sup["total_supply"]]
    return series, s


def _usd(v):
    if v is None:
        return "–"
    return f"${v / 1e9:,.2f}B" if abs(v) >= 1e9 else f"${v / 1e6:,.1f}M" if abs(v) >= 1e6 else f"${v:,.0f}"


def near_readout(s: dict) -> dict:
    t = (f"Intents did {_usd(s['vol_30'])} in 30 days ({_usd(s['vol_all'])} since launch), keeping {_usd(s['rev_30'])} "
         f"as revenue (≈{_usd(s['rev_annual'])} a year).")
    if s.get("issue_usd"):
        t += (f" NEAR's supply is growing {s['infl_90'] if s.get('infl_90') is not None else s['infl_365']:.2f}% a year: "
              f"≈{s['issue_coins'] / 1e6:,.1f}M new NEAR, {_usd(s['issue_usd'])} at today's price. Revenue covers "
              f"{s['cover_pct']:.0f}% of that; a full buyback would offset issuance only below ≈${s['breakeven_px']:.2f}.")
    tone = "bull" if (s.get("cover_pct") or 0) >= 100 else ""
    return {"k": "Intents vs inflation", "text": t, "tone": tone}
