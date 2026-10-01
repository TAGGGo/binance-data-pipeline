"""
Do the coin-page signals say anything about the NEXT 7 / 30 days?  (asked 2026-09-30)

    python3 scripts/research/backtest_coin_signals.py [--coins XRP SOL ...]

Signals, all on closed UTC days using only data available that day:
  macd_bear      daily MACD below its signal line                     (dashboard "MACD (daily)")
  macd_xdown     daily MACD crossed below signal that day
  macd_xup       daily MACD crossed above signal that day
  xb_inflow      30-day net exchange inflow in the top 15% of the prior 365 days   (DefiLlama, from 2022-11)
  xb_outflow     ... bottom 15% (large outflow)
  alpha_neg      60-day return after beta vs BTC < -5%                (dashboard "vs BTC")
  alpha_pos      ... > +5%
  rebound_setup  >= 3 of the 5 flow/positioning rebound lights green  (dashboard "Rebound signals")
  xrp_bad        XRP-only today-like combo: MACD bear AND xb_inflow
Outcome: forward return over 7 and 30 days, and the same minus BTC's (so a bull market doesn't flatter it).
Every signal is compared with the coin's own base rate over the days the signal COULD be evaluated.
Events are declustered: after an event, the next one counts only after the horizon has passed, so each
30-day window is used once ("indep" column). Pooled = all coins together. No parameter was tuned.
"""
import argparse
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from mdh import indicators as ind, settings  # noqa: E402
from mdh.dashboard import coins as cn, onchain  # noqa: E402

H = (7, 30)


def xb_pct(con, asset, idx):
    df = con.execute("SELECT date, exchange, amount FROM cex_reserves_daily WHERE symbol=? ORDER BY date", [asset]).df()
    if df.empty:
        return pd.Series(np.nan, index=idx)
    w = df.pivot_table(index="date", columns="exchange", values="amount", aggfunc="last")
    w.index = pd.to_datetime(w.index)
    f30 = onchain.exchange_flows(w)["flow"].rolling(30).sum()
    pct = f30.rolling(365, min_periods=180).apply(lambda a: (a[:-1] < a[-1]).mean() * 100, raw=True)
    return pct.reindex(idx)


def frame(con, asset, btc):
    fr = ind.build_all(con, asset)["1d"]
    d = fr[~fr["partial"]].copy()
    out = pd.DataFrame(index=d.index)
    c = d["close"]
    for h in H:
        out[f"f{h}"] = (c.shift(-h) / c - 1) * 100
        b = btc.reindex(d.index)
        out[f"x{h}"] = out[f"f{h}"] - (b.shift(-h) / b - 1) * 100
    side = np.sign(d["hist"])
    out["macd_bear"] = (side < 0).where(d["hist"].notna())
    out["macd_xdown"] = ((side < 0) & (side.shift(1) > 0)).where(d["hist"].notna())
    out["macd_xup"] = ((side > 0) & (side.shift(1) < 0)).where(d["hist"].notna())
    p = xb_pct(con, asset, d.index)
    out["xb_inflow"] = (p >= 85).where(p.notna())
    out["xb_outflow"] = (p <= 15).where(p.notna())
    if asset != "BTC":
        rel = ind.vs_btc(c, btc)
        al = rel["alpha60_pct"].reindex(d.index)
        out["alpha_neg"] = (al < -5).where(al.notna())
        out["alpha_pos"] = (al > 5).where(al.notna())
    inp = cn.signal_inputs(con, asset)
    sg = ind.rebound_signals(d, inp.get("top_ls"), inp.get("cb_prem"), inp.get("etf"))
    flow = [k for k in ind.SIGNALS if k != "reclaim"]
    av = sg[flow].notna().sum(axis=1)
    out["rebound_setup"] = ((sg[flow] == 2).sum(axis=1) >= 3).where(av >= 3)
    if asset == "XRP":
        out["xrp_bad"] = (out["macd_bear"].astype("boolean") & out["xb_inflow"].astype("boolean"))
    return out


def events(s: pd.Series, h: int) -> list:
    """declustered event dates: a new event only after the previous one's horizon has passed"""
    ev, last = [], None
    for t in s[s.fillna(False).astype(bool)].index:
        if last is None or (t - last).days >= h:
            ev.append(t)
            last = t
    return ev


def stats(fr: pd.DataFrame, sig: str, h: int, rel=False):
    col = f"x{h}" if rel else f"f{h}"
    ok = fr[sig].notna() & fr[col].notna()
    base = fr.loc[ok, col]
    ev = events(fr.loc[ok, sig], h)
    r = fr.loc[ev, col]
    return len(ev), r, base


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--coins", nargs="*", default=settings.COIN_PAGES)
    a = ap.parse_args()
    con = duckdb.connect(str(settings.DB_PATH), read_only=True)
    bf = ind.build_all(con, "BTC")["1d"]
    btc = bf.loc[~bf["partial"], "close"]
    frames = {}
    for c in a.coins:
        try:
            frames[c] = frame(con, c, btc)
        except Exception as e:
            print("skip", c, e)
    sigs = ["macd_bear", "macd_xdown", "macd_xup", "xb_inflow", "xb_outflow", "alpha_neg", "alpha_pos", "rebound_setup", "xrp_bad"]
    rows = []
    for c, fr in list(frames.items()) + [("POOLED", None)]:
        for s in sigs:
            for h in H:
                for rel in (False, True):
                    if c == "POOLED":
                        parts = [stats(f, s, h, rel) for f in frames.values() if s in f]
                        if not parts:
                            continue
                        n = sum(p[0] for p in parts)
                        r = pd.concat([p[1] for p in parts]); base = pd.concat([p[2] for p in parts])
                    else:
                        if s not in fr:
                            continue
                        n, r, base = stats(fr, s, h, rel)
                    if n == 0:
                        continue
                    rows.append({"coin": c, "signal": s, "h": h, "vs": "BTC" if rel else "abs", "indep": n,
                                 "mean": r.mean(), "median": r.median(), "up%": (r > 0).mean() * 100,
                                 "base_mean": base.mean(), "base_median": base.median(), "base_up%": (base > 0).mean() * 100,
                                 "edge_median": r.median() - base.median(), "edge_up%": (r > 0).mean() * 100 - (base > 0).mean() * 100})
    out = pd.DataFrame(rows)
    pd.set_option("display.width", 250); pd.set_option("display.max_rows", 500)
    out.to_csv(settings.DATA_DIR / "backtest_coin_signals.csv", index=False)
    print(out.round(1).to_string(index=False))


if __name__ == "__main__":
    main()
