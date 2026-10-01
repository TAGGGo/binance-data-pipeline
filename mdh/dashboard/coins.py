"""
Coin-page export: one JSON file per coin (data/dashboard/coins/<SYM>.json) plus a small summary that goes
into data.json. Numbers come from mdh/indicators.py; this module only formats them and writes the plain-English
readout lines shown at the top of each coin page (and reused by the daily digest).
"""
from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd

from mdh import indicators as ind
from mdh import settings

# column -> (json key, rounding) ; rounding "sig" = 7 significant digits, int = decimals
COLS = [
    ("open", "o", "sig"), ("high", "h", "sig"), ("low", "l", "sig"), ("close", "c", "sig"),
    ("vol", "v", 0), ("perp_vol", "pv", 0), ("spot_delta", "sd", 0), ("perp_delta", "pdl", 0),
    ("oi", "oi", 1), ("oi_usd", "oiu", 0), ("funding_bp", "fb", 3), ("funding_apr", "fa", 2),
    *[(f"sma{n}", f"sma{n}", "sig") for n in ind.MA_LENGTHS],
    *[(f"ema{n}", f"ema{n}", "sig") for n in ind.MA_LENGTHS],
    ("macd", "macd", "sig6"), ("signal", "sig", "sig6"), ("hist", "hist", "sig6"), ("rsi", "rsi", 2),
]


def _r(v, how):
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return None
    v = float(v)
    if how in ("sig", "sig6"):
        if v == 0:
            return 0.0
        d = (7 if how == "sig" else 6) - int(math.floor(math.log10(abs(v)))) - 1
        return round(v, max(d, 0)) if d >= 0 else float(round(v, d))
    return int(round(v)) if how == 0 else round(v, how)


def frame_json(c: pd.DataFrame) -> dict:
    out = {"t": [int(t.timestamp()) for t in c.index]}
    for col, key, how in COLS:
        out[key] = [_r(v, how) for v in c[col].to_numpy(dtype=float)] if col in c else []
    out["partial_last"] = bool(c["partial"].iloc[-1]) if len(c) else False
    return out


# ----------------------------------------------------------------------------- plain-English readout
def _pct(a, b):
    return (a / b - 1) * 100 if a and b and not (math.isnan(a) or math.isnan(b)) else None


def _fmt_px(x):
    return f"${x:,.0f}" if x >= 1000 else f"${x:,.2f}" if x >= 10 else f"${x:,.4f}"


DEFAULT_BP_8H = 1.0   # Binance funding when perps trade at fair value: interest 0.01% per 8h
CLAMP_BP_8H = 5.0     # funding = premium + clamp(interest - premium, +/-0.05%)


def funding_context(con, asset: str) -> dict | None:
    """Funding read against Binance's 1 bp default instead of a percentile (a coin whose funding sat below
    default most of the year ranks 'high' just by returning to default). Per-8h bp from the settlements,
    plus the live premium index from binance_1h, which leads the 8-hourly settlements."""
    try:
        f = con.execute("""SELECT ts, funding_rate * 8 / interval_hours * 1e4 AS bp8 FROM deriv_funding
                           WHERE venue = 'binance' AND symbol = ? ORDER BY ts""", [asset]).df()
    except Exception:
        return None
    if f.empty:
        return None
    f["ts"] = pd.to_datetime(f["ts"])
    last_ts = f["ts"].iloc[-1]
    wk = f[f["ts"] > last_ts - pd.Timedelta(days=7)]["bp8"]
    yr = f[f["ts"] > last_ts - pd.Timedelta(days=365)]["bp8"]
    at_default = lambda x: (x - DEFAULT_BP_8H).abs() < 0.01
    out = {
        "last_bp8": _r(f["bp8"].iloc[-1], 2), "last_ts": last_ts.isoformat(),
        "avg7_bp8": _r(wk.mean(), 2), "min7_bp8": _r(wk.min(), 2),
        "pct_default_7d": _r(at_default(wk).mean() * 100, 0),
        "median1y_bp8": _r(yr.median(), 2), "pct_below_default_1y": _r((yr < DEFAULT_BP_8H - 0.01).mean() * 100, 0),
    }
    try:
        p = con.execute("""SELECT "timestamp" AS ts, funding_rate * 1e4 AS prem, (futures_close / spot_close - 1) * 1e4 AS basis
                           FROM binance_1h WHERE symbol = ? AND "timestamp" >= ? ORDER BY 1""",
                        [f"{asset}USDT", (last_ts - pd.Timedelta(hours=24)).to_pydatetime()]).df()
    except Exception:
        p = pd.DataFrame()
    if len(p):
        p["ts"] = pd.to_datetime(p["ts"])
        day = p[p["ts"] > p["ts"].iloc[-1] - pd.Timedelta(hours=24)]
        since = p[p["ts"] >= last_ts]["prem"].dropna()
        cur = since if len(since) else day["prem"].tail(8).dropna()
        if len(cur):
            pa = float(cur.mean())
            out["est_next_bp8"] = _r(pa + min(max(DEFAULT_BP_8H - pa, -CLAMP_BP_8H), CLAMP_BP_8H), 2)
        out["prem_24h_bp"] = _r(day["prem"].mean(), 2)
        out["prem_neg_hours_24h"] = int((day["prem"] < 0).sum())
        out["prem_hours_24h"] = int(day["prem"].notna().sum())
        out["basis_24h_bp"] = _r(day["basis"].mean(), 1)
        out["prem_last_bp"] = _r(p["prem"].dropna().iloc[-1], 2) if p["prem"].notna().any() else None
    return out


def _live_from(con, sql_src: str, params: list):
    return con.execute(f"""
        WITH x AS ({sql_src}), l AS (SELECT max(ts) ts FROM x)
        SELECT l.ts, (SELECT close FROM x WHERE ts = l.ts),
               (SELECT close FROM x WHERE ts = l.ts - INTERVAL 24 HOUR),
               (SELECT close FROM x WHERE ts = l.ts - INTERVAL 7 DAY),
               (SELECT close FROM x WHERE ts = l.ts - INTERVAL 30 DAY) FROM l""", params).fetchone()


def live_changes(con, asset: str) -> dict | None:
    """Latest price and rolling 24h / 7d / 30d % changes from 1h spot candles (what an exchange app shows).
    Binance spot first; if Binance lacks the history (e.g. HYPE listed 2026-09-24) use the SPOT_FALLBACK venue's
    own 1h candles for the whole calculation (HYPE: Hyperliquid spot, its main on-chain market)."""
    srcs = [("binance", "SELECT ts, close FROM bn_kline_1h WHERE market = 'spot' AND symbol = ?", [asset])]
    if asset in settings.SPOT_FALLBACK:
        v = settings.SPOT_FALLBACK[asset][0]
        srcs.append((v, "SELECT ts, close FROM cex_spot WHERE venue = ? AND symbol = ? AND interval = '1h'", [v, asset]))
    best = None
    for name, q, params in srcs:
        try:
            r = _live_from(con, q, params)
        except Exception:
            continue
        if not r or r[0] is None or not r[1]:
            continue
        best = best or (name, r)
        if r[3]:            # has a 7-day lookback: use this venue
            best = (name, r)
            break
    if not best:
        return None
    name, (ts, p, p24, p7, p30) = best
    return {"t": (pd.Timestamp(ts) + pd.Timedelta(hours=1)).isoformat(), "venue": name, "price": _r(p, "sig"),
            "chg24_pct": _r(_pct(p, p24), 2) if p24 else None, "chg7_pct": _r(_pct(p, p7), 1) if p7 else None,
            "chg30_pct": _r(_pct(p, p30), 1) if p30 else None}


def summarize(asset: str, frames: dict[str, pd.DataFrame], fctx: dict | None = None, live: dict | None = None) -> dict:
    s: dict = {"asset": asset, "tf": {}}
    for tf, c in frames.items():
        closed = c          # live: include the still-forming candle so the hourly update shows the latest price/RSI/MAs
        if len(closed) < 2:
            continue
        last, prev = closed.iloc[-1], closed.iloc[-2]
        x = {"t": closed.index[-1].isoformat(), "live": bool(c["partial"].iloc[-1]), "close": _r(last["close"], "sig"),
             "chg_pct": _r(_pct(last["close"], prev["close"]), 2), "rsi": _r(last["rsi"], 1)}
        x["above"] = {f"sma{n}": (None if math.isnan(last[f"sma{n}"]) else bool(last["close"] > last[f"sma{n}"]))
                      for n in ind.MA_LENGTHS}
        # MACD: side and most recent cross
        h = closed["hist"].dropna()
        if len(h):
            side = np.sign(h.to_numpy())
            flips = np.nonzero(side[1:] != side[:-1])[0]
            x["macd_side"] = "bull" if side[-1] > 0 else "bear"
            x["macd_hist"] = _r(h.iloc[-1], "sig6")
            x["macd_hist_prev"] = _r(h.iloc[-2], "sig6") if len(h) > 1 else None
            if len(flips):
                k = flips[-1] + 1
                x["macd_cross_t"] = h.index[k].isoformat()
                x["macd_cross_bars_ago"] = int(len(h) - 1 - k)
        s["tf"][tf] = x
    d = frames.get("1d")
    if d is not None and len(d) > 31:
        closed = d          # live (includes today's forming candle)
        last = closed.iloc[-1]
        def ago(col, n):
            v = closed[col].iloc[-1 - n] if len(closed) > n else np.nan
            return None if pd.isna(v) else float(v)
        s["deriv"] = {
            "oi": _r(last["oi"], 1), "oi_usd": _r(last["oi_usd"], 0),
            "oi_chg7_pct": _r(_pct(last["oi"], ago("oi", 7)), 1), "oi_chg30_pct": _r(_pct(last["oi"], ago("oi", 30)), 1),
            "price_chg7_pct": _r(_pct(last["close"], ago("close", 7)), 1),
            "price_chg30_pct": _r(_pct(last["close"], ago("close", 30)), 1),
            "funding_apr_7d": _r(closed["funding_apr"].tail(7).mean(), 2),
            "funding_apr_1d": _r(last["funding_apr"], 2),
            "funding_apr_pctile_1y": None,
            "spot_cvd_7d": _r(closed["spot_delta"].tail(7).sum(min_count=7), 0),
            "perp_cvd_7d": _r(closed["perp_delta"].tail(7).sum(min_count=7), 0),
        }
        fa = closed["funding_apr"].dropna().tail(365)
        if len(fa) > 60 and not pd.isna(last["funding_apr"]):
            s["deriv"]["funding_apr_pctile_1y"] = _r((fa < fa.iloc[-1]).mean() * 100, 0)
    if fctx and "deriv" in s:
        s["deriv"]["funding"] = fctx
    if live:        # rolling windows from 1h candles: same numbers as the exchange app and the WeChat report
        s["live"] = live
        if "1d" in s["tf"]:
            s["tf"]["1d"]["close"] = live["price"]
            if live["chg24_pct"] is not None:
                s["tf"]["1d"]["chg_pct"] = live["chg24_pct"]
        if "deriv" in s:
            for k, lk in (("price_chg7_pct", "chg7_pct"), ("price_chg30_pct", "chg30_pct")):
                if live[lk] is not None:
                    s["deriv"][k] = live[lk]
    s["readout"] = readout(asset, s)
    return s


def readout(asset: str, s: dict) -> list[dict]:
    """Short plain-English lines. Each says what the number is and what it usually means."""
    out = []
    d1, w1 = s["tf"].get("1d"), s["tf"].get("1w")
    if d1:
        ab = d1["above"]
        known = {k: v for k, v in ab.items() if v is not None}
        n_above = sum(known.values())
        trend = ("above all" if n_above == len(known) else "below all" if n_above == 0
                 else f"above {n_above} of {len(known)}")
        out.append(("Trend (daily)",
                    f"{asset} closed at {_fmt_px(d1['close'])}, {trend} of its 20/30/50/100-day averages. "
                    + ("Price above every average is a clean uptrend." if n_above == len(known) else
                       "Price below every average is a clean downtrend." if n_above == 0 else
                       "Mixed: short and long averages disagree, usually a turning or sideways market."),
                    "bull" if n_above == len(known) else "bear" if n_above == 0 else ""))
    for tf, name in (("1d", "daily"), ("1w", "weekly")):
        x = s["tf"].get(tf)
        if not x or "macd_side" not in x:
            continue
        cross = ""
        if x.get("macd_cross_bars_ago") is not None and x["macd_cross_bars_ago"] <= 5:
            unit = "day" if tf == "1d" else "week"
            n = x["macd_cross_bars_ago"]
            when = "on the last close" if n == 0 else f"{n} {unit}{'s' if n > 1 else ''} ago"
            cross = f" It crossed {'up' if x['macd_side'] == 'bull' else 'down'} {when}."
        grow = (x.get("macd_hist_prev") is not None and abs(x["macd_hist"]) > abs(x["macd_hist_prev"]))
        bull = x["macd_side"] == "bull"
        mom = (("the gap is widening, so upward momentum is building" if grow else
                "the gap is narrowing, so upward momentum is fading") if bull else
               ("the gap is widening, so downward momentum is building" if grow else
                "the gap is narrowing, so downward momentum is fading"))
        out.append((f"MACD ({name})", f"{'above' if bull else 'below'} its signal line and {mom}.{cross}",
                    ("bull" if x["macd_side"] == "bull" else "bear") if grow else ""))
    if d1 and d1.get("rsi") is not None:
        r = d1["rsi"]
        tag = ("overbought (above 70): strong run, often followed by a pause" if r >= 70 else
               "oversold (below 30): heavy selling, often near a short-term low" if r <= 30 else
               "neutral range (30 to 70)")
        out.append(("RSI (daily)", f"{r:.0f}, {tag}.", "bear" if r >= 70 else "bull" if r <= 30 else ""))
    dv = s.get("deriv")
    if dv and dv.get("oi_chg7_pct") is not None:
        oi7, px7 = dv["oi_chg7_pct"], dv.get("price_chg7_pct") or 0
        if oi7 > 5 and px7 > 0:
            m = "new leveraged longs are chasing the move"
        elif oi7 > 5 and px7 <= 0:
            m = "leverage is building while price falls, often new shorts"
        elif oi7 < -5 and px7 > 0:
            m = "price rose while leverage left, typical of shorts closing (a squeeze)"
        elif oi7 < -5:
            m = "leverage is being flushed out as price falls (deleveraging)"
        else:
            m = "leverage roughly unchanged"
        out.append(("Open interest (Binance)", f"{oi7:+.1f}% in coins over 7 days while price moved {px7:+.1f}%: {m}.", ""))
    fx = (dv or {}).get("funding")
    if fx and fx.get("avg7_bp8") is not None:
        a = fx["avg7_bp8"]
        side = ("well above Binance's 1 bp default: longs are paying up, longs are the crowded side" if a > 2 else
                "at Binance's 1 bp default (10.95% a year): no crowding either way" if a >= 0.5 else
                "below the 1 bp default: perps trade soft, little long demand" if a >= 0 else
                "negative: shorts pay longs, shorts are the crowded side")
        hist = (f" Over the last year the median was {fx['median1y_bp8']:+.2f} bp and "
                f"{fx['pct_below_default_1y']:.0f}% of settlements were below default."
                if fx.get("median1y_bp8") is not None else "")
        out.append(("Funding (per 8h)", f"last settlement {fx['last_bp8']:+.2f} bp, 7-day average {a:+.2f} bp, {side}.{hist}",
                    "bear" if a > 2 else ""))
        if fx.get("prem_24h_bp") is not None:
            pr = fx["prem_24h_bp"]
            what = ("perps trade above spot, buyers are pushing on perps" if pr > 2 else
                    "perps trade below spot, selling pressure is on perps (often shorts)" if pr < -2 else
                    "perps near fair value")
            nxt = (f" Next settlement on track for about {fx['est_next_bp8']:+.2f} bp." if fx.get("est_next_bp8") is not None else "")
            out.append(("Premium index (24h)", f"average {pr:+.1f} bp, negative in {fx['prem_neg_hours_24h']} of "
                        f"{fx['prem_hours_24h']} hours: {what}.{nxt}", ""))
    elif dv and dv.get("funding_apr_7d") is not None:
        f = dv["funding_apr_7d"]
        side = ("longs pay shorts, so longs are the crowded side" if f > 22 else
                "shorts pay longs, so shorts are the crowded side" if f < 0 else "around Binance's default (10.95% a year)")
        out.append(("Funding (7-day avg)", f"{f:+.1f}% a year, {side}.", ""))
    if dv and dv.get("spot_cvd_7d") is not None and dv.get("perp_cvd_7d") is not None:
        sc, pc = dv["spot_cvd_7d"], dv["perp_cvd_7d"]
        m = lambda v: f"{'+' if v >= 0 else '−'}${abs(v) / 1e6:,.0f}M"
        if sc > 0 and pc < 0:
            t = "spot buyers are absorbing perp selling, usually the healthier setup"
        elif sc < 0 and pc > 0:
            t = "perp buyers are leading while spot sells, a leverage-driven move"
        elif sc > 0 and pc > 0:
            t = "buyers are in control on both spot and perps"
        else:
            t = "sellers are in control on both spot and perps"
        out.append(("CVD (7 days)", f"spot {m(sc)}, perp {m(pc)}: {t}.",
                    "bull" if sc > 0 and pc <= 0 or (sc > 0 and pc > 0) else "bear" if sc < 0 and pc < 0 else ""))
    return [{"k": k, "text": t, "tone": tone} for k, t, tone in out]


def _spot_closes(con, asset: str) -> pd.Series:
    """Closed UTC days only (today's forming candle is dropped)."""
    d = con.execute("""SELECT ts, close FROM bn_kline_1d WHERE market = 'spot' AND symbol = ?
                       AND ts < date_trunc('day', now() AT TIME ZONE 'UTC') ORDER BY ts""", [asset]).df()
    return pd.Series(d["close"].to_numpy(dtype=float), index=pd.to_datetime(d["ts"]))


def rel_json(rel: pd.DataFrame) -> dict:
    return {"t": [int(t.timestamp()) for t in rel.index],
            "ratio": [_r(v, "sig") for v in rel["ratio"]], "hi": [_r(v, "sig") for v in rel["high_1y"]],
            "dd": [_r(v, 2) for v in rel["dd_pct"]], "beta": [_r(v, 3) for v in rel["beta60"]],
            "r2": [_r(v, 3) for v in rel["r2_60"]], "ma200": [_r(v, "sig") for v in rel["ma200"]]}


def _d(t):
    return None if t is None or pd.isna(t) else pd.Timestamp(t).date().isoformat()


def rel_summary(rel: pd.DataFrame) -> dict:
    last = rel.iloc[-1]
    ago = lambda n: rel["ratio"].iloc[-1 - n] if len(rel) > n else np.nan
    eps = ind.rel_episodes(rel)
    done = [e for e in eps if e["end"] is not None]
    return {
        "t": _d(rel.index[-1]), "ratio": _r(last["ratio"], "sig"),
        "chg30_pct": _r(_pct(last["ratio"], ago(30)), 1), "chg90_pct": _r(_pct(last["ratio"], ago(90)), 1),
        "last_high": _d(last["last_high"]), "days_since": int(last["days_since"]), "dd_pct": _r(last["dd_pct"], 1),
        "beta60": _r(last["beta60"], 2), "r2_60": _r(last["r2_60"], 2), "alpha60_pct": _r(last["alpha60_pct"], 1),
        "episodes": [{"start": _d(e["start"]), "trough": _d(e["trough"]), "end": _d(e["end"]),
                      "bleed_days": e["bleed_days"], "bleed_pct": _r(e["bleed_pct"], 1),
                      "catch_days": e["catch_days"], "catch_pct": _r(e["catch_pct"], 1), "total_days": e["total_days"]} for e in eps],
        "median_total_days": int(np.median([e["total_days"] for e in done])) if done else None,
        "median_bleed_pct": _r(float(np.median([e["bleed_pct"] for e in done])), 1) if done else None,
        "ma200": _r(last["ma200"], "sig"), "vs_ma200_pct": _r(_pct(last["ratio"], last["ma200"]), 1),
        "ma50": _r(last["ma50"], "sig"), "ma50_vs_200_pct": _r(_pct(last["ma50"], last["ma200"]), 1),
        "streak200": None if pd.isna(last["streak200"]) else int(last["streak200"]),
        "tries90": [{"start": _d(x["start"]), "days": x["days"], "running": x["running"]} for x in ind.ma200_tries(rel)],
    }


def rel_readout(asset: str, r: dict) -> dict:
    b, r2, al = r["beta60"], r["r2_60"], r["alpha60_pct"]
    fit = ("moves mostly with BTC" if r2 >= 0.75 else "partly its own moves" if r2 >= 0.5 else "largely its own moves")
    side = ("running ahead of what its beta implies" if al > 5 else "lagging what its beta implies" if al < -5
            else "about in line with its beta")
    if r["days_since"] == 0:
        where = "at a new 1-year high"
    else:
        where = "{:.0f}% below its last 1-year high ({}, {} days ago)".format(abs(r["dd_pct"]), r["last_high"], r["days_since"])
    text = ("{a}/BTC {w}; {c30:+.0f}% vs BTC over 30 days. 60-day beta {b:.2f}, BTC explains {r2:.0f}% of daily moves ({fit}); "
            "{al:+.0f}% over 60 days after beta, {side}.").format(a=asset, w=where, c30=r["chg30_pct"], b=b, r2=r2 * 100,
                                                                 fit=fit, al=al, side=side)
    if r.get("median_total_days") and r["days_since"] >= ind.REL_MIN_GAP:
        text += (" Past stretches without a new 1-year high lasted a median {} days with a median {:.0f}% drop in the ratio "
                 "before the ratio broke out again.").format(r["median_total_days"], abs(r["median_bleed_pct"]))
    tone = "bull" if r["days_since"] == 0 or al > 5 else "bear" if al < -5 else ""
    return {"k": "vs BTC", "text": text, "tone": tone}


def ma200_readout(asset: str, r: dict) -> dict | None:
    """The ratio vs its own 200-day line: how far above/below, for how many closes, and recent failed tries."""
    st, v = r.get("streak200"), r.get("vs_ma200_pct")
    if st is None or v is None:
        return None
    lv = r.get("live") or {}
    now = (" Live ({}): {:+.1f}% vs the line.".format(pd.Timestamp(lv["as_of"]).strftime("%b %d %H:%M UTC"), lv["vs_ma200_pct"])
           if lv.get("vs_ma200_pct") is not None else "")
    side = "above" if st > 0 else "below"
    text = "{a}/BTC closed {v:+.1f}% vs its 200-day average, {n} close{s} {side} it in a row.{now}".format(
        a=asset, v=v, n=abs(st), s="" if abs(st) == 1 else "s", side=side, now=now)
    tries = r.get("tries90") or []
    done = [x for x in tries if not x["running"]]
    if tries:
        text += " Crossed back above {} time{} in 90 days".format(len(tries), "" if len(tries) == 1 else "s")
        text += "; earlier tries held {} closes at most.".format(max(x["days"] for x in done)) if done else "."
    g = r.get("ma50_vs_200_pct")
    if g is not None:
        text += " 50-day average is {:.1f}% {} the 200-day.".format(abs(g), "above" if g >= 0 else "below")
    tone = "bull" if st >= 14 else "bear" if st < 0 and v < -3 else ""
    return {"k": "vs BTC · 200-day line", "text": text, "tone": tone}


# ----------------------------------------------------------------------------- rebound signals (see indicators.rebound_signals)
SIG_LABEL = {"flush": "Leverage flushed", "shorts": "Shorts crowded", "us": "US buying (Coinbase premium)",
             "spot": "Spot buying (Binance CVD)", "etf": "ETF inflows", "reclaim": "Price back above its week"}


def signal_inputs(con, asset: str) -> dict:
    out = {}
    try:
        t = con.execute("""SELECT "timestamp" AS ts, sum_toptrader_long_short_ratio AS v FROM binance_1h
                           WHERE symbol = ? AND sum_toptrader_long_short_ratio IS NOT NULL ORDER BY 1""", [f"{asset}USDT"]).df()
        if len(t):
            out["top_ls"] = t.set_index(pd.to_datetime(t["ts"]))["v"].resample("D").last()
    except Exception:
        pass
    try:   # Coinbase USD vs Binance USDT (converted with Coinbase USDT-USD), hourly, averaged per UTC day
        p = con.execute("""
            WITH cb AS (SELECT ts, close FROM cex_spot WHERE venue='coinbase' AND interval='1h' AND symbol=?),
                 u  AS (SELECT ts, close AS usdt FROM cex_spot WHERE venue='coinbase' AND interval='1h' AND symbol='USDT'),
                 bn AS (SELECT ts, close FROM bn_kline_1h WHERE market='spot' AND symbol=?)
            SELECT cb.ts, (cb.close / (bn.close * coalesce(u.usdt, 1)) - 1) * 1e4 AS bp
            FROM cb JOIN bn USING (ts) LEFT JOIN u USING (ts)
            WHERE cb.ts < date_trunc('hour', now() AT TIME ZONE 'UTC') - INTERVAL 1 HOUR ORDER BY 1""", [asset, asset]).df()
        if len(p):
            p = p.set_index(pd.to_datetime(p["ts"]))["bp"]
            out["cb_prem_1h"] = p
            out["cb_prem"] = p.resample("D").mean()
    except Exception:
        pass
    try:
        e = con.execute("SELECT date, net_inflow_usd FROM v_etf_flows WHERE asset = ? ORDER BY date", [asset]).df()
        if len(e):
            out["etf"] = e.set_index(pd.to_datetime(e["date"]))["net_inflow_usd"]
    except Exception:
        pass
    return out


def signals_export(con, asset: str, frames: dict) -> tuple[dict, dict] | tuple[None, None]:
    d = frames.get("1d")
    if d is None or len(d) < 30:
        return None, None
    d1 = d              # live: today's forming candle counts, so the lights move with each hourly update
    inp = signal_inputs(con, asset)
    sg = ind.rebound_signals(d1, inp.get("top_ls"), inp.get("cb_prem"), inp.get("etf"))
    hist = sg.tail(240)
    series = {"t": [int(t.timestamp()) for t in hist.index], "greens": [int(v) for v in hist["greens"]],
              "avail": [int(v) for v in hist["available"]], "close": [_r(v, "sig") for v in d1["close"].reindex(hist.index)],
              **{k: [None if pd.isna(v) else int(v) for v in hist[k]] for k in ind.SIGNALS}}
    last = sg.iloc[-1]
    live = inp.get("cb_prem_1h")
    live24 = float(live.tail(24).mean()) if live is not None and len(live) >= 12 else None
    val = lambda k, nd=2: _r(last[k], nd)
    summ = {
        "t": _d(sg.index[-1]), "greens": int(last["greens"]), "available": int(last["available"]),
        "status": {k: (None if pd.isna(last[k]) else int(last[k])) for k in ind.SIGNALS},
        "values": {"oi_vs_high_pct": _r((last["oi_vs_high"] - 1) * 100, 1), "fund8_3d_bp": val("fund8_3d"),
                   "top_ls": val("top_ls"), "top_vs_high_pct": _r((last["top_vs_high"] - 1) * 100, 1),
                   "cb_prem_bp": val("cb_prem_bp", 1), "cb_prem_24h_bp": _r(live24, 1) if live24 is not None else None,
                   "cvd_last": _r(last["cvd"], 0), "cvd_prev": _r(sg["cvd"].iloc[-2], 0) if len(sg) > 1 else None,
                   "etf_3": _r(last["etf_3"], 0), "etf_last": _r(last["etf_last"], 0),
                   "close_vs_7d_pct": _r((last["close_vs_7d"] - 1) * 100, 1), "chg7_pct": val("chg7_pct", 1)},
    }
    # stage: the flow/positioning checks (everything except the price check) vs price itself
    flow_greens = sum(1 for k in ind.SIGNALS if k != "reclaim" and summ["status"][k] == 2)
    flow_avail = sum(1 for k in ind.SIGNALS if k != "reclaim" and summ["status"][k] is not None)
    summ["flow_greens"], summ["flow_available"] = flow_greens, flow_avail
    summ["stage"] = ("confirmed" if flow_greens >= 3 and summ["status"]["reclaim"] == 2 else
                     "setup" if flow_greens >= 3 else "none")
    return series, summ


def signals_readout(s: dict) -> dict:
    g, n = s["greens"], s["available"]
    red = [SIG_LABEL[k].split(" (")[0].lower() for k, v in s["status"].items() if v == 0]
    text = f"{g} of {n} green."
    if s["stage"] == "setup":
        text += (f" {s['flow_greens']} of the {s['flow_available']} flow and positioning checks are green while price is still "
                 "below its 7-day average: buyers are showing up before price turns, the setup to watch.")
    elif s["stage"] == "confirmed":
        text += " Flows and positioning are supportive and price is back above its 7-day average."
    elif red:
        text += " Still missing: " + ", ".join(red) + "."
    return {"k": "Rebound signals", "text": text, "tone": "bull" if s["stage"] != "none" else ""}


def fib_export(f: dict) -> tuple[dict, dict]:
    """indicators.fib_levels -> (chart data for coins/<SYM>.json, summary for data.json)."""
    lv = [{"r": x["r"], "p": _r(x["p"], "sig")} for x in f["levels"] if x["p"] > 0]
    ts = lambda t: int(pd.Timestamp(t).timestamp())
    chart = {"start": ts(f["start"]), "end": ts(f["end"]), "levels": lv}
    ch = f.get("channel")
    summ = {"dir": f["dir"], "high_t": _d(f["high"][0]), "high": _r(f["high"][1], "sig"), "low_t": _d(f["low"][0]),
            "low": _r(f["low"][1], "sig"), "levels": lv, "price": _r(f["price"], "sig"), "retrace_pct": _r(f["retrace"] * 100, 1),
            "support": {"r": f["support"]["r"], "p": _r(f["support"]["p"], "sig")} if f["support"] else None,
            "resist": {"r": f["resist"]["r"], "p": _r(f["resist"]["p"], "sig")} if f["resist"] else None}
    if ch:
        t0 = pd.Timestamp(ch["t0"])
        tt = [t0 + pd.Timedelta(days=i) for i in range(ch["n"])]
        chart["ch"] = {"t": [ts(t) for t in tt], "mid": [_r(v, "sig") for v in ch["mid"]],
                       "up": [_r(v, "sig") for v in ch["upper"]], "lo": [_r(v, "sig") for v in ch["lower"]]}
        summ["ch"] = {"mid": _r(ch["mid"][-1], "sig"), "up": _r(ch["upper"][-1], "sig"), "lo": _r(ch["lower"][-1], "sig"),
                      "slope_pct_day": _r(ch["slope_pct_day"], 3), "width_pct": _r(ch["sd_pct"], 1), "pos": _r(ch["pos"], 2)}
    return chart, summ


def fib_readout(a: str, F: dict) -> dict:
    up = F["dir"] == "up"
    swing = (f"{'Up' if up else 'Down'}-swing {_fmt_px(F['low'] if up else F['high'])} ({F['low_t'] if up else F['high_t']}) → "
             f"{_fmt_px(F['high'] if up else F['low'])} ({F['high_t'] if up else F['low_t']}); price has given back {F['retrace_pct']:.0f}% of it.")
    nxt = []
    if F.get("support"):
        nxt.append(f"nearest level below {_fmt_px(F['support']['p'])} ({F['support']['r']:g})")
    if F.get("resist"):
        nxt.append(f"above {_fmt_px(F['resist']['p'])} ({F['resist']['r']:g})")
    ch = F.get("ch")
    chs = ""
    if ch and ch.get("pos") is not None:
        where = "near the top" if ch["pos"] > 0.8 else "near the bottom" if ch["pos"] < 0.2 else "in the middle"
        chs = (f" Trend channel since the swing start rises {ch['slope_pct_day']:+.2f}% a day; price sits {where} of it "
               f"(lower edge {_fmt_px(ch['lo'])}).")
    return {"k": "Fibonacci (daily)", "text": swing + (" " + ", ".join(nxt).capitalize() + "." if nxt else "") + chs, "tone": ""}


def export(con, out_dir) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {}
    btc = _spot_closes(con, "BTC")
    btc_live = live_changes(con, "BTC")
    for asset in settings.COIN_PAGES:
        frames = ind.build_all(con, asset)
        data = {"asset": asset, "tf": {tf: frame_json(c) for tf, c in frames.items()}}
        rel = None
        if asset != "BTC":
            d1 = frames["1d"]
            closes = d1.loc[~d1["partial"], "close"] if "1d" in frames else _spot_closes(con, asset)
            rel = ind.vs_btc(closes, btc)
            if len(rel) > ind.REL_WIN + 1:
                data["rel"] = rel_json(rel)
            else:
                rel = None
        sig_series, sig_sum = signals_export(con, asset, frames)
        if sig_series:
            data["sig"] = sig_series
        live = live_changes(con, asset)
        s = summarize(asset, frames, funding_context(con, asset), live)
        price = live["price"] if live else None
        extra = {}
        try:
            fb = ind.fib_levels(frames["1d"]) if "1d" in frames else None
            if fb:
                data["fib"], s["fib"] = fib_export(fb)
                extra["fib"] = fib_readout(asset, s["fib"])
        except Exception as e:          # a new card must never take the coin page down
            s["fib_error"] = str(e)[:200]
        from mdh.dashboard import onchain
        for key, fn in (("xb", onchain.exchange_balance), ("whale", onchain.xrp_whales if asset == "XRP" else None),
                        ("intents", onchain.near_intents if asset == "NEAR" else None)):
            if fn is None:
                continue
            try:
                ser, summ = fn(con, asset, price) if key == "xb" else fn(con, price)
            except Exception as e:
                s[key + "_error"] = str(e)[:200]
                continue
            if ser:
                data[key], s[key] = ser, summ
                extra[key] = summ["readout"]
        (out_dir / f"{asset}.json").write_text(json.dumps(data, separators=(",", ":")))
        if asset in settings.SPOT_FALLBACK:
            s["spot_note"] = settings.SPOT_FALLBACK[asset][1]
        if sig_sum:
            s["sig"] = sig_sum
            s["readout"].append(signals_readout(sig_sum))
        if rel is not None:
            s["rel"] = rel_summary(rel)
            if live and btc_live and live.get("price") and btc_live.get("price") and s["rel"].get("ma200"):
                lr = live["price"] / btc_live["price"]
                s["rel"]["live"] = {"ratio": _r(lr, "sig"), "vs_ma200_pct": _r(_pct(lr, s["rel"]["ma200"]), 1),
                                    "as_of": min(live["t"], btc_live["t"])}
            s["readout"].append(rel_readout(asset, s["rel"]))
            m2 = ma200_readout(asset, s["rel"])
            if m2:
                s["readout"].append(m2)
        for k in ("fib", "intents", "whale", "xb"):
            if k in extra:
                s["readout"].append(extra[k])
        summary[asset] = s
    return summary
