"""
Spot ETF daily report (dashboard tab "ETF Daily" + the shareable daily image), per asset, in coins and USD.

Inputs
  v_etf_flows           daily net flow (USD) and total net assets per asset (SoSoValue; Grayscale for ZEC; Bitwise for NEAR)
  etf_holdings_daily    SoSoValue total token holdings for the latest session (BTC/ETH/SOL), snapshotted daily
  etf_fund_snapshot     SoSoValue per-fund numbers for the latest session (BTC/ETH/SOL), snapshotted daily
  etf_fund_daily        Grayscale's own daily file (ZCSH)
  cex_spot              Coinbase hourly candles: the price at the 4:00 PM New York close, used to turn USD into coins

Method
  * Coins moved on a session = net flow (USD) / price at that session's 4 PM ET close (the NAV strike time).
  * Holdings are anchored on the latest session (SoSoValue's reported token holdings where available, otherwise
    net assets / price) and walked back with the coin flows, so the holdings line and the flow bars always agree.
  * Windows: last session; last 7 sessions ("7-day"); the 7 before that; month / year to date; since launch.
    Percentages divide by holdings just before the window starts.
  * Per-fund coins = fund flow or fund net assets / the implied price (total net assets / total token holdings).
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from mdh import settings
from mdh.core import db

NY = ZoneInfo("America/New_York")
ASSETS = ["BTC", "ETH", "SOL", "XRP", "ZEC", "NEAR", "HYPE"]
SINGLE_FUND = {"ZEC": "ZCSH"}   # Grayscale one-fund ETP (grayscale.py); settings.ISSUER_ETFS assets are handled like it
BTC_PER_DAY = 450.0          # 3.125 BTC x 144 blocks since the April 2024 halving

# fund display names (institute or ticker -> zh / en)
NAMES = {
    "IBIT": ("贝莱德", "BlackRock"), "ETHA": ("贝莱德", "BlackRock"),
    "GBTC": ("灰度", "Grayscale"), "ETHE": ("灰度", "Grayscale"), "BTC": ("灰度迷你", "Grayscale Mini"),
    "ETH": ("灰度迷你", "Grayscale Mini"), "GSOL": ("灰度", "Grayscale"), "ZCSH": ("灰度", "Grayscale"),
    "FBTC": ("富达", "Fidelity"), "FETH": ("富达", "Fidelity"), "FSOL": ("富达", "Fidelity"),
    "BITB": ("Bitwise", "Bitwise"), "ETHW": ("Bitwise", "Bitwise"), "BSOL": ("Bitwise", "Bitwise"),
    "ARKB": ("方舟 21Shares", "ARK 21Shares"), "CETH": ("21Shares", "21Shares"), "TSOL": ("21Shares", "21Shares"),
    "HODL": ("VanEck", "VanEck"), "ETHV": ("VanEck", "VanEck"), "VSOL": ("VanEck", "VanEck"),
    "EZBC": ("富兰克林", "Franklin"), "EZET": ("富兰克林", "Franklin"), "SOEZ": ("富兰克林", "Franklin"),
    "BTCO": ("景顺银河", "Invesco Galaxy"), "QETH": ("景顺银河", "Invesco Galaxy"),
    "BRRR": ("CoinShares", "CoinShares"), "BTCW": ("WisdomTree", "WisdomTree"), "DEFI": ("Hashdex", "Hashdex"),
    "MSBT": ("摩根士丹利", "Morgan Stanley"), "MSOL": ("摩根士丹利", "Morgan Stanley"), "MSSE": ("摩根士丹利", "Morgan Stanley"),
    "ETHB": ("贝莱德质押", "BlackRock Staked"), "NRR": ("Bitwise", "Bitwise"), "BHYP": ("Bitwise", "Bitwise"), "THYP": ("21Shares", "21Shares"), "TETH": ("21Shares", "21Shares"),
}
INSTITUTE_ZH = {"BlackRock": "贝莱德", "Grayscale": "灰度", "Fidelity": "富达", "Franklin": "富兰克林", "Franklin Templeton": "富兰克林",
                "Invesco": "景顺银河", "Invesco Galaxy": "景顺银河", "Morgan Stanley": "摩根士丹利", "ARK": "方舟", "ARK 21Shares": "方舟 21Shares",
                "WisdomTree": "智慧树", "Valkyrie": "Valkyrie", "Canary": "Canary", "REX-Osprey": "REX-Osprey", "Hashdex": "Hashdex"}


def _close_px(con, asset: str, days: list) -> dict:
    """Price at 4:00 PM New York on each session day: Coinbase hourly close of the 3-4 PM ET candle;
    falls back to the Binance daily close."""
    out = {}
    try:
        cb = con.execute("SELECT ts, close FROM cex_spot WHERE venue='coinbase' AND interval='1h' AND symbol=?", [asset]).df()
        cb = cb.set_index(pd.to_datetime(cb["ts"]))["close"]
    except Exception:
        cb = pd.Series(dtype=float)
    try:
        bd = con.execute("SELECT ts, close FROM bn_kline_1d WHERE market='spot' AND symbol=?", [asset]).df()
        bd = bd.set_index(pd.to_datetime(bd["ts"]).dt.date)["close"]
    except Exception:
        bd = pd.Series(dtype=float)
    for d in days:
        t = datetime(d.year, d.month, d.day, 15, 0, tzinfo=NY).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
        v = cb.get(pd.Timestamp(t))
        if v is None or (isinstance(v, float) and math.isnan(v)):
            v = bd.get(d)
        out[d] = float(v) if v is not None and not pd.isna(v) else np.nan
    return out


def _r(x, nd=2):
    return None if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))) else round(float(x), nd)


def build_asset(con, asset: str) -> dict | None:
    f = con.execute("""SELECT date, net_inflow_usd, net_assets_usd, source FROM v_etf_flows WHERE asset=? ORDER BY date""", [asset]).df()
    f = f.dropna(subset=["net_inflow_usd"])
    if len(f) < (1 if asset in SINGLE_FUND or asset in settings.ISSUER_ETFS else 8):   # a new one-fund ETP shows from its first session
        return None
    f["date"] = pd.to_datetime(f["date"]).dt.date
    px = _close_px(con, asset, list(f["date"]))
    f["px"] = f["date"].map(px)
    f["px"] = f["px"].ffill().bfill()
    f["coins"] = f["net_inflow_usd"] / f["px"]
    last = f.iloc[-1]
    # anchor holdings on the latest session
    anchor, anchor_src, implied_px = None, "net assets / price", None
    if db.table_exists(con, "etf_holdings_daily"):
        h = con.execute("SELECT date, token_holdings, net_assets_usd FROM etf_holdings_daily WHERE asset=? ORDER BY date DESC LIMIT 1", [asset]).fetchone()
        if h and h[1] and pd.Timestamp(h[0]).date() == last["date"]:
            anchor, anchor_src = float(h[1]), "SoSoValue token holdings"
            implied_px = float(h[2]) / float(h[1]) if h[2] else None
    if anchor is None and not pd.isna(last["net_assets_usd"]):
        anchor = float(last["net_assets_usd"]) / float(last["px"])
    if anchor is None:
        return None
    f["hold"] = anchor - f["coins"][::-1].cumsum()[::-1].shift(-1).fillna(0)       # holdings after each session
    f["hold_before"] = f["hold"] - f["coins"]
    n = len(f)
    idx = n - 1
    def window(mask):
        w = f[mask]
        if not len(w):
            return {"coins": 0.0, "usd": 0.0, "pct": None, "from": None, "to": None, "n": 0}
        start = w["hold_before"].iloc[0]
        if start < 0.05 * anchor:      # launched from (near) zero: a % of the starting base means nothing
            start = float("nan")
        return {"coins": _r(w["coins"].sum()), "usd": _r(w["net_inflow_usd"].sum(), 0),
                "pct": _r(w["coins"].sum() / start * 100, 2) if start > 0 else None,
                "from": w["date"].iloc[0].isoformat(), "to": w["date"].iloc[-1].isoformat(), "n": int(len(w))}
    pos = np.arange(n)
    ld = last["date"]
    win = {
        "day": window(pos == idx), "prev_day": window(pos == idx - 1),
        "d7": window(pos >= idx - 6), "prev7": window((pos >= idx - 13) & (pos <= idx - 7)),
        "mtd": window([d.year == ld.year and d.month == ld.month for d in f["date"]]),
        "ytd": window([d.year == ld.year for d in f["date"]]),
        "launch": window(pos >= 0),
    }
    dropped = f.iloc[idx - 7] if idx >= 7 else None
    d7_prev = f["coins"].iloc[max(0, idx - 7): idx].sum()
    # streak of same-sign sessions
    sgn = np.sign(f["coins"].round(6).values)
    streak = 0
    for v in sgn[::-1]:
        if v == 0 or (streak and v != np.sign(sgn[-1])):
            break
        streak += 1
    # per-fund
    funds, fund_date = [], None
    if asset in SINGLE_FUND:
        tk = SINGLE_FUND[asset]
        g = con.execute("SELECT date, shares_outstanding, nav_per_share, aum_usd FROM etf_fund_daily WHERE ticker=? ORDER BY date DESC LIMIT 1", [tk]).fetchone()
        if g:
            funds = [{"ticker": tk, "zh": NAMES[tk][0], "en": NAMES[tk][1], "coins": _r(last["coins"]), "usd": _r(last["net_inflow_usd"], 0),
                      "hold": _r(anchor), "share": 100.0, "premium": None, "traded_usd": None}]
            fund_date = ld.isoformat()
    elif asset in settings.ISSUER_ETFS:
        p = float(last["px"])
        tot = 0.0
        for fd in settings.ISSUER_ETFS[asset]:
            tk = fd["ticker"]
            fl = con.execute("SELECT net_flow_usd FROM etf_flows_by_fund WHERE ticker=? AND source='issuer' AND date=?", [tk, ld]).fetchone()
            g = con.execute("SELECT aum_usd FROM etf_fund_daily WHERE ticker=? AND date<=? ORDER BY date DESC LIMIT 1", [tk, ld]).fetchone()
            usd_ = float(fl[0]) if fl else 0.0
            aum_ = float(g[0]) if g and g[0] else 0.0
            tot += aum_
            zh, en = NAMES.get(tk, (tk, tk))
            funds.append({"ticker": tk, "zh": zh, "en": en, "coins": _r(usd_ / p), "usd": _r(usd_, 0), "hold": _r(aum_ / p),
                          "share": None, "premium": None, "traded_usd": None})
        for x in funds:
            x["share"] = _r((x["hold"] or 0) * p / tot * 100, 2) if tot else None
        funds.sort(key=lambda x: -(x["hold"] or 0))
        fund_date = ld.isoformat()
    elif db.table_exists(con, "etf_fund_snapshot"):
        s = con.execute("""SELECT * FROM etf_fund_snapshot WHERE asset=? AND date=(SELECT max(date) FROM etf_fund_snapshot WHERE asset=?)""",
                        [asset, asset]).df()
        if len(s):
            fund_date = pd.Timestamp(s["date"].iloc[0]).date().isoformat()
            p = implied_px or float(last["px"])
            tot_na = s["net_assets_usd"].sum()
            for _, r in s.iterrows():
                inst = r["institute"] or r["ticker"]
                zh, en = NAMES.get(r["ticker"], (INSTITUTE_ZH.get(inst, inst), inst))
                funds.append({"ticker": r["ticker"], "zh": zh, "en": en, "coins": _r(r["daily_net_inflow_usd"] / p),
                              "usd": _r(r["daily_net_inflow_usd"], 0), "hold": _r(r["net_assets_usd"] / p),
                              "share": _r(r["net_assets_usd"] / tot_na * 100, 2) if tot_na else None,
                              "premium": _r(r["premium"] * 100, 3) if not pd.isna(r["premium"]) else None,
                              "traded_usd": _r(r["value_traded_usd"], 0)})
            funds.sort(key=lambda x: -(x["hold"] or 0))
    ins = [x for x in funds if (x["coins"] or 0) > 1e-9]
    outs = [x for x in funds if (x["coins"] or 0) < -1e-9]
    stats = {"n_in": len(ins), "n_out": len(outs), "n_flat": len(funds) - len(ins) - len(outs),
             "sum_in": _r(sum(x["coins"] for x in ins)), "sum_out": _r(sum(x["coins"] for x in outs)),
             "top_in": max(ins, key=lambda x: x["coins"]) if ins else None,
             "top_out": min(outs, key=lambda x: x["coins"]) if outs else None}
    tail = f.tail(90)
    rep = {
        "asset": asset, "date": ld.isoformat(), "prev_date": f["date"].iloc[idx - 1].isoformat(),
        "px": _r(last["px"], 4), "hold": _r(anchor), "hold_usd": _r(anchor * float(last["px"]), 0),
        "anchor_src": anchor_src, "aum_usd": _r(last["net_assets_usd"], 0), "win": win,
        "day_vs_prev_pct": _r((win["day"]["coins"] / win["prev_day"]["coins"] - 1) * 100, 1) if win["prev_day"]["coins"] else None,
        "window_effect": {"d7_prev": _r(d7_prev), "change": _r(win["d7"]["coins"] - d7_prev),
                          "dropped_date": dropped["date"].isoformat() if dropped is not None else None,
                          "dropped_coins": _r(dropped["coins"]) if dropped is not None else None},
        "streak": int(streak), "streak_dir": int(np.sign(sgn[-1])),
        "avg30": _r(f["coins"].tail(30).mean()),
        "series": {"date": [d.isoformat() for d in tail["date"]], "coins": [_r(v) for v in tail["coins"]],
                   "usd": [_r(v, 0) for v in tail["net_inflow_usd"]], "hold": [_r(v) for v in tail["hold"]],
                   "px": [_r(v, 4) for v in tail["px"]]},
        "funds": funds, "fund_date": fund_date, "stats": stats,
        "issuance_days": _r(win["day"]["coins"] / BTC_PER_DAY, 1) if asset == "BTC" else None,
        "source": {"ZEC": "Grayscale ZCSH daily file", "NEAR": "Bitwise NRR site snapshots",
                   "HYPE": "21Shares THYP history + Bitwise BHYP site snapshots"}.get(asset, "SoSoValue"),
    }
    rep["text"] = {"zh": text_zh(rep), "en": text_en(rep)}
    return rep


def _fmt(x, nd=0):
    return f"{abs(x):,.{nd}f}"


def _nd(asset):
    return 2 if asset in ("BTC", "ZEC") else 0


def text_zh(r):
    a, w, nd = r["asset"], r["win"], _nd(r["asset"])
    day, d7 = w["day"]["coins"], w["d7"]["coins"]
    head = f"{r['date'][:4]}年{int(r['date'][5:7])}月{int(r['date'][8:10])}日，${a} 现货 ETF "
    s = head + (f"单日净{'增持' if day >= 0 else '减持'}约 {_fmt(day, nd)} 枚（约 ${_fmt(w['day']['usd'] / 1e6, 1)}M）"
                if abs(day) > 1e-9 else "当日没有申购或赎回，净变化为 0")
    if r["day_vs_prev_pct"] is not None and w["prev_day"]["coins"] and np.sign(w["prev_day"]["coins"]) == np.sign(day):
        s += f"，较上一交易日{'增加' if abs(day) > abs(w['prev_day']['coins']) else '减少'}约 {abs(r['day_vs_prev_pct']):.0f}%"
    elif w["prev_day"]["coins"]:
        s += f"，上一交易日为净{'增持' if w['prev_day']['coins'] > 0 else '减持'} {_fmt(w['prev_day']['coins'], nd)} 枚"
    s += f"；最近七个交易日累计净{'增持' if d7 >= 0 else '减持'}约 {_fmt(d7, nd)} 枚"
    we = r["window_effect"]
    if we["dropped_date"] and we["change"] is not None and abs(we["change"]) > 0:
        dd = f"{int(we['dropped_date'][5:7])}月{int(we['dropped_date'][8:10])}日"
        chg, drop = we["change"], we["dropped_coins"]
        if abs(drop) > abs(day) and np.sign(-drop) == np.sign(chg):
            s += (f"，七日累计较前一日{'上升' if chg > 0 else '下降'} {_fmt(chg, nd)} 枚，主要是 {dd}的"
                  f"{'减持' if drop < 0 else '增持'}（{_fmt(drop, nd)} 枚）移出窗口带来的")
            if day > 0 and chg > 0:
                s += "，当天本身的增持力度并不强"
        else:
            s += f"，七日累计较前一日{'上升' if chg > 0 else '下降'} {_fmt(chg, nd)} 枚，主要来自当日{'增持' if day > 0 else '减持'}"
    s += "。"
    if r["streak"] >= 3:
        s += f"已连续 {r['streak']} 个交易日净{'增持' if r['streak_dir'] > 0 else '减持'}。"
    if r["avg30"]:
        ratio = day / r["avg30"] if r["avg30"] else None
        if ratio is not None and r["avg30"] > 0 and day != 0:
            s += f"当日规模约为近 30 个交易日日均（{_fmt(r['avg30'], nd)} 枚）的 {ratio:.1f} 倍。"
    if r["issuance_days"] is not None:
        s += f"当日净增持相当于约 {r['issuance_days']:.1f} 天的比特币新增产量。"
    s += f"总持仓 {_fmt(r['hold'], nd)} 枚。"
    st = r["stats"]
    if st["top_in"] or st["top_out"]:
        parts = []
        if st["top_in"]:
            parts.append(f"{st['top_in']['zh']} {_fmt(st['top_in']['coins'], nd)} 枚领增")
        if st["top_out"]:
            parts.append(f"{st['top_out']['zh']} {_fmt(st['top_out']['coins'], nd)} 枚领减")
        s += "分基金看，" + "，".join(parts) + "。"
    return s


def text_en(r):
    a, w, nd = r["asset"], r["win"], _nd(r["asset"])
    day, d7 = w["day"]["coins"], w["d7"]["coins"]
    s = (f"On {r['date']}, US spot ${a} ETFs {'added' if day >= 0 else 'shed'} about {_fmt(day, nd)} {a} "
         f"(~${_fmt(w['day']['usd'] / 1e6, 1)}M)") if abs(day) > 1e-9 else f"On {r['date']}, US spot ${a} ETFs saw no creations or redemptions"
    if r["day_vs_prev_pct"] is not None and w["prev_day"]["coins"] and np.sign(w["prev_day"]["coins"]) == np.sign(day):
        s += f", {abs(r['day_vs_prev_pct']):.0f}% {'more' if abs(day) > abs(w['prev_day']['coins']) else 'less'} than the previous session"
    s += f". The last seven sessions net {'+' if d7 >= 0 else '−'}{_fmt(d7, nd)} {a}"
    we = r["window_effect"]
    if we["dropped_date"] and we["change"]:
        chg, drop = we["change"], we["dropped_coins"]
        if abs(drop) > abs(day) and np.sign(-drop) == np.sign(chg):
            s += (f"; the 7-day total {'rose' if chg > 0 else 'fell'} {_fmt(chg, nd)} mostly because {we['dropped_date']}'s "
                  f"{'outflow' if drop < 0 else 'inflow'} ({_fmt(drop, nd)}) left the window")
        else:
            s += f"; the 7-day total {'rose' if chg > 0 else 'fell'} {_fmt(chg, nd)}, driven by the day's own flow"
    s += "."
    if r["streak"] >= 3:
        s += f" That's {r['streak']} {'inflow' if r['streak_dir'] > 0 else 'outflow'} sessions in a row."
    if r["avg30"] and r["avg30"] > 0 and day != 0:
        s += f" The day was {day / r['avg30']:.1f}x the 30-session average ({_fmt(r['avg30'], nd)})."
    if r["issuance_days"] is not None:
        s += f" That equals about {r['issuance_days']:.1f} days of newly mined BTC."
    s += f" Holdings: {_fmt(r['hold'], nd)} {a}."
    return s


def build(con) -> dict:
    out = {}
    for a in ASSETS:
        try:
            r = build_asset(con, a)
        except Exception as e:   # one asset failing shouldn't sink the export
            r = {"error": str(e)}
        if r:
            out[a] = r
    return out
