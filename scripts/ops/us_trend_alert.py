#!/usr/bin/env python3
"""
US stock trend alerts (S&P 500 / Nasdaq-100) to WeChat via PushPlus, modelled on LEI (@TheMarketMemo)'s
subscriber notes of Sep-Oct 2026. Runs from the Mac's hourly job; no Claude tokens involved.

LEI's framework (what this script encodes):
  * Daily chart of index futures ES / NQ with three moving-average GROUPS: 20 (MA20 + EMA20),
    60 (MA60 + EMA60), 120 (MA120 + EMA120). Equal-weight RSP for market breadth.
  * "抵扣价" (deduction price) = the close that drops out of an N-day MA next; current price above it
    means that MA keeps rising. All three below price = all three groups rising = bull trend.
  * "底线" (bottom line) = daily EMA60. Below the 20 group = pullback; below EMA60 = bull advantage lost,
    think bearish / buy "black swan insurance"; below the 120 group or 20/60 bearish cross = bears in control.
  * 1-hour EMA20/EMA60 crossing = small-level early warning ("日K走坏一定会先在1小时找到线索").
  * 破底翻: price breaks a prior low and quickly reclaims it = bullish trap for shorts. 假突破 = the mirror.
  * Volume: "缩量上涨涨不停，放量下跌跌不深"; RSP 放量止跌 = buyers stepping in.
  * Black-swan insurance (his 9/30 note): QQQ puts, >90 DTE, delta -0.25~-0.3, ~2% of account.

  python3 scripts/ops/us_trend_alert.py              fetch, push new alerts + the daily report after the close
  python3 scripts/ops/us_trend_alert.py --dry-run    no send / no state change, writes data/state/us_trend/preview.html
  python3 scripts/ops/us_trend_alert.py --replay 2026-09-01   print the daily-close alerts it would have sent
  python3 scripts/ops/us_trend_alert.py --review     forward returns after every logged alert (was it right?)
Recipient: US_ALERT_TOKEN in .env if set, else PUSHPLUS_TOKEN (Avalon). Times shown in US_ALERT_TZ (default LA).
Log: data/state/us_trend/events.jsonl (every alert sent), daily.jsonl (daily snapshots). Not investment advice.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

REPO = Path(__file__).resolve().parents[2]
ST = REPO / "data" / "state" / "us_trend"
EVENTS, DAILY, STATE, PREVIEW = ST / "events.jsonl", ST / "daily.jsonl", ST / "state.json", ST / "preview.html"
NY = ZoneInfo("America/New_York")
TZ = ZoneInfo(os.environ.get("US_ALERT_TZ", "America/Los_Angeles"))

# key: (exchange, symbol, kind, want_1h). kind: fut = CME session (date = ts+12h), tvc = stamped prior evening, cash
SYMS = {
    "ES": ("CME_MINI", "ES1!", "fut", True), "NQ": ("CME_MINI", "NQ1!", "fut", True),
    "SPX": ("SP", "SPX", "cash", False), "NDX": ("NASDAQ", "NDX", "cash", False),
    "RSP": ("AMEX", "RSP", "cash", False), "SPY": ("AMEX", "SPY", "cash", False),
    "QQQ": ("NASDAQ", "QQQ", "cash", False), "VIX": ("TVC", "VIX", "tvc", False),
    "US10Y": ("TVC", "US10Y", "tvc", False),
    "SMH": ("NASDAQ", "SMH", "cash", False),   # semis (LEI 10/7: 半导体趋势逆转 -> 大盘失去支撑)
    "GC": ("COMEX", "GC1!", "fut", True),      # gold (Avalon 10/8 after LEI: 黄金期货1小时破底翻)
}
NAME = {"ES": "标普期货ES", "NQ": "纳指期货NQ", "RSP": "等权标普RSP", "SPX": "标普500", "NDX": "纳指100",
        "SPY": "SPY", "QQQ": "QQQ", "VIX": "VIX",
        "SMH": "半导体SMH", "US10Y": "美10年期国债利率", "GC": "黄金期货GC"}
US10Y_TH = (5.3, 5.5)   # LEI 9/23-29: rates 5.1-5.3% -> cautious
SMH_BAND = 0.005       # SMH must close 0.5% beyond EMA60 to count
TREND = ["ES", "NQ", "RSP"]
GOLD = ["GC"]          # same daily MA framework, no breadth/insurance text; plus 1h cross + 1h 破底翻
TURN_BAND = 0.01      # 20均线拐头: close must be 1% beyond the 20-day 抵扣价 (0.3% fired ~2/month per symbol)
CLOSE_BUF = 0.003   # 10/22 Avalon: a CLOSE must be 0.3% beyond a line to change state / count as 站上·跌破所有均线 (no 0.1% whipsaws)
BUF = 0.003   # 10/22: was 0.0015; intraday drops below EMA60 are pushed now, so require 0.3%          # intraday hysteresis: a line must be crossed by 0.15% before an intraday state change counts
# Plain-language glossary appended to every push (Avalon 10/7: "什么是等权RSP，收破，意味着什么，警示里要说清楚").
# Only the terms that appear in the message are explained. Order = display order.
GLOSSARY = [
    (("RSP",), "等权RSP",
     "标普500等权重ETF：500只股票每只占同样比例（约0.2%）。普通的标普500/SPY按市值加权，前10大科技股占三成以上，"
     "所以指数可以被少数巨头撑着。RSP看的是“大多数股票”过得好不好，用来衡量市场宽度。"),
    (("收破", "收盘跌破"), "收破",
     "收盘价跌破某条线（不是盘中短暂刺穿）。日K以收盘为准，盘中跌破又收回不算。"),
    (("所有均线",), "所有均线",
     "这里指6条日均线：20、60、120日的简单均线（MA）和指数均线（EMA）。站上全部 = 短中长期趋势都在价格下方托着；"
     "跌破全部 = 短中长期都转为压在上方。"),
    (("20组", "20均线组"), "20均线组",
     "MA20与EMA20这两条约一个月的均线。跌破它 = 短期回调，多头趋势本身未必坏。"),
    (("底线", "EMA60"), "底线EMA60",
     "60日指数均线（约3个月），LEI定义的多空“底线”：在它上方是多头回调，收破它 = 多头优势丢失，要偏谨慎。"),
    (("抵扣价",), "抵扣价",
     "N日均线明天会“扣掉”的那天收盘价。现价高于抵扣价，均线就会继续上行；三组抵扣价都在现价下方 = 三组均线同时向上。"),
    (("破底翻",), "破底翻",
     "先跌破前低、随后收盘又收回 = 空头陷阱，常是看多信号；若再跌回那根K线的低点下方则失败。"),
    (("假突破",), "假突破",
     "先突破前高、收盘又跌回 = 多头陷阱，趋势偏弱时常见；重新站上前高则化解。"),
    (("放量", "缩量"), "放量/缩量",
     "成交量相对20日均量的高低（放量 ≥1.3倍）。LEI口诀：缩量上涨涨不停，放量下跌跌不深。"),
    (("1小时 EMA20",), "1小时EMA20/60交叉",
     "小时图上短均线穿过长均线，是日K可能转弱/转强的早期线索，噪音较多，需日K确认。"),
    (("VIX",), "VIX",
     "标普期权隐含波动率，俗称恐慌指数。20以下平静，25以上紧张，30以上恐慌。"),
    (("SMH",), "半导体SMH",
     "半导体ETF（英伟达、台积电、博通等）。半导体是这轮AI行情和大盘的主要支撑，LEI 10/7：半导体若趋势逆转，SPY/QQQ 会失去支撑。"
     "这里看它日K是否收破底线EMA60。"),
    (("10年期",), "美10年期国债利率",
     "美国长期借钱的价格。利率越高，股票估值和大多数企业（尤其中小企业、RSP）压力越大；LEI 在 5.1-5.3% 时转谨慎。"),
    (("黄金",), "黄金期货GC",
     "COMEX黄金期货连续合约（美元/盎司），几乎24小时交易，换月时会有缺口。黄金对美元和实际利率敏感：利率、美元回落通常利好黄金。"
     "这里用和ES/NQ同一套日均线框架看趋势，另看1小时EMA20/60交叉和1小时破底翻（LEI 10/8 用的就是1小时破底翻）。"),
    (("期货ES", "期货NQ"), "ES/NQ",
     "标普500 / 纳指100的股指期货，几乎24小时交易，LEI用它看日K趋势。这里是连续合约，换月时会有缺口。"),
]


def explain(html: str, sym: str | None = None) -> str:
    """'名词解释' block for the terms that appear in html (tags stripped)."""
    import re as _re
    txt = _re.sub(r"<[^>]+>", "", html)
    items = [(name, body) for keys, name, body in GLOSSARY if any(k in txt for k in keys)]
    if not items:
        return ""
    li = "".join(f"<li><b>{n}</b>：{b}</li>" for n, b in items)
    return f"<div style='color:#555;font-size:13px;margin-top:8px'><b>名词解释</b><ul style='padding-left:18px;margin:4px 0'>{li}</ul></div>"


def breadth_meaning(D, e) -> str:
    """For RSP events: what it means given where ES/NQ (cap-weighted) are."""
    if e["sym"] != "RSP":
        return ""
    try:
        st = {k: classify(D[k].iloc[-1], D[k].close.iloc[-1]) for k in ("ES", "NQ") if k in D}
    except Exception:  # noqa: BLE001
        return ""
    if not st:
        return ""
    big = "、".join(f"{NAME[k]}“{STATES[v]}”" for k, v in st.items())
    if e["side"] == "bear" and max(st.values()) <= 1:
        m = ("<b>意味着什么：</b>指数（按市值加权）还强，但大多数股票在走弱 = 上涨变窄，主要靠少数巨头撑着。"
             "这本身不是卖出信号，窄幅上涨可以持续几周甚至几个月；但地基变薄，一旦巨头也转弱，下跌会更快。"
             "要升级警戒的条件：ES/NQ 也收破底线EMA60。对加密：风险偏好在边际降温，留意BTC是否同步走弱。")
    elif e["side"] == "bear":
        m = ("<b>意味着什么：</b>大多数股票和指数一起走弱，是较普遍的转弱，不只是少数股票的问题，保护仓位优先。")
    elif max(st.values()) <= 1:
        m = ("<b>意味着什么：</b>大多数股票跟上了指数，上涨变宽 = 更健康、更可持续的多头。")
    else:
        m = ("<b>意味着什么：</b>普通股票先止跌，但指数仍弱；要看ES/NQ能否重新站上底线EMA60来确认。")
    return f"<p>{m}<br><span style='color:#888'>当前：{big}</span></p>"


INSURANCE = "按LEI的做法，焦虑可买“黑天鹅保险”：QQQ看跌期权，到期>90天，Delta约-0.25~-0.3，约占账户2%；风险解除后平掉。"


# ----------------------------------------------------------------------------- data
def fetch():
    from tvDatafeed import Interval, TvDatafeed
    import logging
    logging.getLogger("tvDatafeed.main").setLevel(logging.ERROR)
    os.environ["TZ"] = "UTC"
    time.tzset()
    tv, out = TvDatafeed(), {}
    (ST / "bars").mkdir(parents=True, exist_ok=True)
    for k, (ex, sym, kind, h1) in SYMS.items():
        for iv, n in [("1d", 400)] + ([("1h", 300)] if h1 else []):
            df = None
            for a in range(3):
                try:
                    df = tv.get_hist(sym, ex, Interval.in_daily if iv == "1d" else Interval.in_1_hour, n_bars=n)
                    if df is not None and len(df):
                        break
                except Exception as e:  # noqa: BLE001
                    print(f"fetch {k} {iv} attempt {a + 1}: {e}")
                time.sleep(2 * (a + 1))
            if df is None or not len(df):
                print(f"fetch {k} {iv}: no data")
                continue
            df = df.reset_index()[["datetime", "open", "high", "low", "close", "volume"]].rename(columns={"datetime": "ts"})
            df.to_csv(ST / "bars" / f"{k}_{iv}.csv", index=False)
            out[(k, iv)] = df
            time.sleep(1)
    return out


def load_cached():
    out = {}
    for k in SYMS:
        for iv in ("1d", "1h"):
            f = ST / "bars" / f"{k}_{iv}.csv"
            if f.exists():
                out[(k, iv)] = pd.read_csv(f)
    return out


def ind(d):
    c = d["close"]
    for n in (20, 60, 120):
        d[f"ma{n}"] = c.rolling(n).mean()
        d[f"ema{n}"] = c.ewm(span=n, adjust=False).mean()
        d[f"ded{n}"] = c.shift(n - 1)      # 抵扣价: the close that drops out of the N-day MA next session
    d["vol20"] = d["volume"].shift(1).rolling(20).mean()
    return d


def prep(bars):
    D, H = {}, {}
    for (k, iv), df in bars.items():
        df = df.copy()
        df["ts"] = pd.to_datetime(df["ts"])
        if iv == "1d":
            kind = SYMS[k][2]
            df["date"] = (df["ts"] + pd.Timedelta(hours=12)).dt.date if kind in ("fut", "tvc") else df["ts"].dt.date
            df = df.drop_duplicates("date", keep="last").set_index("date")
            D[k] = ind(df)
        else:
            d = df.set_index("ts")
            c = d["close"]
            d["ema20"], d["ema60"] = c.ewm(span=20, adjust=False).mean(), c.ewm(span=60, adjust=False).mean()
            d["ema120"] = c.ewm(span=120, adjust=False).mean()
            d["ma20"], d["ma60"] = c.rolling(20).mean(), c.rolling(60).mean()
            H[k] = d
    return D, H


def closed(date, kind, now):
    end = dtime(17, 0) if kind == "fut" else dtime(16, 10)
    return now >= datetime.combine(date, end, NY)


def closed_frames(D, now):
    return {k: (d if closed(d.index[-1], SYMS[k][2], now) else d.iloc[:-1]) for k, d in D.items()}


# ----------------------------------------------------------------------------- classification
STATES = ["强势多头", "多头", "多头回调（仍在20均线组下方）", "警戒：跌破底线EMA60", "空头占优"]
# what a state change MEANS, for push titles (Avalon 10/21: "盘中跌破" title arrived on an up day; the move was a
# reclaim of EMA60). Key = the new level; the line crossed is the one next to the new level.
SHORT_STATE = {0: "强势多头", 1: "多头", 2: "多头回调", 3: "跌破底线", 4: "空头占优"}
UP_HEAD = {0: "进入强势多头（三组均线同时向上）", 1: "站回20均线组", 2: "收回底线EMA60", 3: "收回120均线组"}
DOWN_HEAD = {2: "跌破20均线组", 3: "跌破底线EMA60", 4: "跌入空头占优"}


def move_head(prev_lvl, lvl, when):
    """'↑ 盘中收回底线EMA60' / '↓ 收盘跌破20均线组'"""
    if lvl < prev_lvl:
        return f"↑ {when}{UP_HEAD.get(lvl, STATES[lvl])}"
    return f"↓ {when}{DOWN_HEAD.get(lvl, STATES[lvl])}"


def classify(r, p):
    g20l, g20h = min(r.ma20, r.ema20), max(r.ma20, r.ema20)
    g120l = min(r.ma120, r.ema120)
    if p < g120l or (r.ema20 < r.ema60 and r.ma20 < r.ma60 and p < min(r.ma60, r.ema60)):
        return 4
    if p < r.ema60:
        return 3
    if p < g20l:
        return 2
    if p > g20h and r.ema20 > r.ema60 > r.ema120 and p > r.ded20 and p > r.ded60 and p > r.ded120:
        return 0
    return 1


def classify_live(r, p, prev_lvl):
    """intraday: only move away from prev_lvl if the move survives a +-BUF nudge of the price"""
    lvl = classify(r, p)
    if prev_lvl is None or lvl == prev_lvl:
        return lvl
    if classify(r, p * (1 - BUF)) == lvl and classify(r, p * (1 + BUF)) == lvl:
        return lvl
    return prev_lvl


def flipped(rel, want, band=None):
    """rel = close/line - 1 (daily). True if the latest close is beyond the band on side `want` (+1 above / -1 below)
    and the previous close that was outside the band was on the other side."""
    band = CLOSE_BUF if band is None else band
    side = [1 if x > band else -1 if x < -band else 0 for x in rel.iloc[-60:].fillna(0)]
    prev = next((x for x in reversed(side[:-1]) if x), 0)
    return side[-1] == want and prev == -want


def ev(key, sym, kind, side, msg, price=None, level=None, date=None):
    return {"key": key, "sym": sym, "type": kind, "side": side, "msg": msg, "price": price, "level": level,
            "date": str(date) if date else None}


def f(x, k=""):
    if x is None or pd.isna(x):
        return "-"
    return f"{x:,.0f}" if abs(x) >= 1000 else f"{x:,.2f}"


def pct(a, b):
    return (a / b - 1) * 100


def lines_text(r, p):
    g20 = (min(r.ma20, r.ema20), max(r.ma20, r.ema20))
    g120 = min(r.ma120, r.ema120)
    return (f"20组 {f(g20[0])}~{f(g20[1])}（{pct(p, g20[0]):+.1f}%），底线EMA60 {f(r.ema60)}（{pct(p, r.ema60):+.1f}%），"
            f"120组 {f(g120)}（{pct(p, g120):+.1f}%）")


# ----------------------------------------------------------------------------- daily-close alerts (live + replay)
def close_events(Dc, prev):
    """Dc = frames that end on a CLOSED session. prev = {sym: {"lvl":, "date":}} (updated in place)."""
    out = []
    for k in TREND + GOLD:
        d = Dc.get(k)
        if d is None or len(d) < 130:
            continue
        t, r = d.index[-1], d.iloc[-1]
        lvl = classify(r, r.close)
        p = prev.get(k)
        if p and lvl != p["lvl"] and p["lvl"] in {classify(r, r.close * (1 + s * CLOSE_BUF)) for s in (-1, 1)}:
            lvl = p["lvl"]      # 10/22: within 0.3% of the line that separates old/new state -> keep the old state
        entered_strong = lvl == 0 and p is not None and p["lvl"] != 0      # LEI 9/21: 三个抵扣价均低于现价 = 三组均线同时向上
        if p and str(t) > p["date"] and (max(lvl, 1) != max(p["lvl"], 1) or entered_strong):   # 强势多头->多头 not pushed
            side = "bear" if lvl > p["lvl"] else "bull"
            msg = f"{NAME[k]} 收盘 {f(r.close)}：{STATES[p['lvl']]} → <b>{STATES[lvl]}</b>。{lines_text(r, r.close)}"
            if k in ("ES", "NQ") and lvl >= 3 and p["lvl"] < 3:
                msg += "<br>" + INSURANCE
            if k in ("ES", "NQ") and lvl <= 1 and p["lvl"] >= 3:
                msg += "<br>趋势修复：若买了保险，可考虑逐步平掉。"
            if entered_strong:
                msg += "<br>三个抵扣价都低于现价：20/60/120 三组均线同时向上运行（LEI：牛市最重要的赚钱技能是“不动”）。"
            e = ev(f"{k}:close:{t}:{lvl}", k, "收盘状态", side, msg, r.close, None, t)
            e["head"] = move_head(p["lvl"], lvl, "收盘")
            out.append(e)
        if not p or str(t) > p["date"]:
            prev[k] = {"lvl": lvl, "date": str(t)}
    # 站上所有均线 / 跌破所有均线 (first close across all six daily MAs)
    for k in TREND + GOLD:
        d = Dc.get(k)
        if d is None or len(d) < 130:
            continue
        t = d.index[-1]
        cols = ["ma20", "ema20", "ma60", "ema60", "ma120", "ema120"]
        top = d[cols].max(axis=1)
        bot = d[cols].min(axis=1)
        c, pc = d.close.iloc[-1], d.close.iloc[-2]
        same = [e for e in out if e["key"].startswith(f"{k}:close:{t}:")]
        up, dn = flipped(d.close / top - 1, 1), flipped(d.close / bot - 1, -1)   # 10/22: 0.3% band, side must flip
        if up and same:   # merge into today's state message (and mark the separate key as done: 10/21 duplicate)
            same[0]["msg"] += f"<br>同时<b>站上所有均线</b>（最高一条 {f(top.iloc[-1])}）。"
            same[0].setdefault("also", []).append(f"{k}:above_all:{t}")
        elif dn and same:
            same[0]["msg"] += f"<br>同时<b>跌破所有均线</b>（最低一条 {f(bot.iloc[-1])}）。"
            same[0].setdefault("also", []).append(f"{k}:below_all:{t}")
        elif up:
            out.append(ev(f"{k}:above_all:{t}", k, "站上所有均线", "bull",
                          f"{NAME[k]} 收盘 {f(c)} <b>站上所有均线</b>（最高一条 {f(top.iloc[-1])}）→ "
                          + ("大多数股票重新走强（市场宽度改善）" if k == "RSP" else "日K回到多头有利位置" + ("（LEI 9/17：站上所有均线，底线之上看多做多）" if k in ("ES", "NQ") else "")),
                          c, top.iloc[-1], t))
        elif dn:
            out.append(ev(f"{k}:below_all:{t}", k, "跌破所有均线", "bear",
                          f"{NAME[k]} 收盘 {f(c)} <b>跌破所有均线</b>（最低一条 {f(bot.iloc[-1])}）→ "
                          + ("大多数股票转弱（市场宽度变差）" if k == "RSP" else "日K转弱，保护仓位优先"), c, bot.iloc[-1], t))
    # 10/22 (Avalon OK): 20均线拐头 = LEI's main risk test (10/16 "20抵扣价高于收盘"). ded20 = the close that drops out of
    # MA20 next session; close 1% below it (and the last close outside the band was above) -> MA20 starts falling.
    for k in ("ES", "NQ"):
        d = Dc.get(k)
        if d is None or len(d) < 130:
            continue
        t, r = d.index[-1], d.iloc[-1]
        gap = pct(r.ema20, r.ema60)
        for w, side, word in ((-1, "bear", "拐头向下"), (1, "bull", "重新拐头向上")):
            if flipped(d.close / d.ded20 - 1, w, TURN_BAND):
                msg = (f"{NAME[k]} 收盘 {f(r.close)}，20日抵扣价 {f(r.ded20)}（明天要从20日均线里移出的那天收盘）"
                       + ("高于" if w < 0 else "低于") + f"现价 {abs(pct(r.close, r.ded20)):.1f}% → <b>20均线{word}</b>。"
                       + f"EMA20 比 EMA60 {'高' if gap >= 0 else '低'} {abs(gap):.1f}%")
                if w < 0:
                    msg += "（两线越近越危险；LEI：真正的风险是20/60空头交叉，交叉 = 趋势结束）"
                msg += f"。{lines_text(r, r.close)}"
                out.append(ev(f"{k}:turn20:{w}:{t}", k, "20均线拐头", side, msg, r.close, r.ded20, t))
    # 破底翻 / 假突破 on a 10-session swing (ES, NQ): first close back across the broken swing level
    for k in ("ES", "NQ", "GC"):
        d = Dc.get(k)
        if d is None or len(d) < 140:
            continue
        t, c, pc = d.index[-1], d.close.iloc[-1], d.close.iloc[-2]
        lvl = classify(d.iloc[-1], c)
        for j in (-3, -2, -1):
            lo, hi = d.low.iloc[j - 3:].min(), d.high.iloc[j - 3:].max()   # extreme of the recent poke
            swing_lo, swing_hi = d.low.iloc[j - 13:j - 3].min(), d.high.iloc[j - 13:j - 3].max()
            if d.low.iloc[j] < swing_lo and c > swing_lo * 1.001 and pc <= swing_lo * 1.001:
                out.append(ev(f"{k}:pdf:{t}", k, "破底翻", "bull",
                              f"{NAME[k]} 跌破前低 {f(swing_lo)}（最低 {f(lo)}）后，收盘 {f(c)} 收回 → <b>破底翻</b>（看多信号；再跌回 {f(lo)} 下方则失败）",
                              c, swing_lo, t))
                break
            if lvl >= 2 and d.high.iloc[j] > swing_hi and c < swing_hi * 0.999 and pc >= swing_hi * 0.999:
                out.append(ev(f"{k}:fake:{t}", k, "假突破", "bear",
                              f"{NAME[k]} 突破前高 {f(swing_hi)}（最高 {f(hi)}）后，收盘 {f(c)} 跌回 → <b>假突破</b>（趋势偏弱时的反弹失败；重新站上 {f(swing_hi)} 则化解）",
                              c, swing_hi, t))
                break
    # volume (cash ETFs), LEI: 缩量上涨涨不停，放量下跌跌不深
    for k in ("SPY", "QQQ", "RSP"):
        d = Dc.get(k)
        if d is None or len(d) < 25:
            continue
        t, r = d.index[-1], d.iloc[-1]
        ch, vr = pct(r.close, d.close.iloc[-2]), r.volume / r.vol20
        if vr >= 1.3 and ch <= -1.0:
            out.append(ev(f"{k}:vol:{t}", k, "放量下跌", "bear",
                          f"{NAME[k]} 放量下跌 {ch:+.1f}%，成交量 {vr:.1f}x 20日均量（LEI：放量下跌往往跌不深，看是否守住底线）", r.close, None, t))
        elif vr >= 1.3 and ch >= 1.0:
            out.append(ev(f"{k}:vol:{t}", k, "放量上涨", "bull", f"{NAME[k]} 放量上涨 {ch:+.1f}%，成交量 {vr:.1f}x 20日均量", r.close, None, t))
        if k == "RSP" and ch > 0 and vr >= 1.2 and pct(d.close.iloc[-2], d.close.iloc[-5]) <= -1.5:
            out.append(ev(f"RSP:stop:{t}", k, "放量止跌", "bull",
                          f"等权RSP 连跌后放量收涨 {ch:+.1f}%（{vr:.1f}x 量）→ 止跌迹象；守住今日低 {f(r.low)}、站上前高 {f(d.high.iloc[-2])} 即构成底部构造",
                          r.close, r.low, t))
    # all-time-high closes
    for k in ("SPX", "NDX"):
        d = Dc.get(k)
        if d is None or len(d) < 30:
            continue
        t, c = d.index[-1], d.close.iloc[-1]
        if c > d.close.iloc[:-1].max() and d.close.iloc[-2] <= d.close.iloc[:-2].max():
            out.append(ev(f"{k}:ath:{t}", k, "历史新高", "bull", f"{NAME[k]} 收盘 {f(c)} 再创历史新高（前一日不是新高）", c, None, t))
    # VIX thresholds
    d = Dc.get("VIX")
    if d is not None and len(d) > 2:
        t, c, pc = d.index[-1], d.close.iloc[-1], d.close.iloc[-2]
        for th in (20, 25, 30):
            if c >= th > pc:
                out.append(ev(f"VIX:{th}:{t}", "VIX", "VIX", "bear", f"VIX 收于 {c:.1f}，升破 {th}（恐慌升温）", c, th, t))
        if pc >= 20 > c:
            out.append(ev(f"VIX:down20:{t}", "VIX", "VIX", "bull", f"VIX 回落至 {c:.1f}，跌回 20 下方", c, 20, t))
    # semis: SMH close across daily EMA60 (底线) with a 0.5% band; push only when the side flips vs the last close outside the band
    d = Dc.get("SMH")
    if d is not None and len(d) > 70:
        t, c, e = d.index[-1], d.close.iloc[-1], d.ema60.iloc[-1]
        rel = d.close / d.ema60 - 1
        side = [1 if x > SMH_BAND else -1 if x < -SMH_BAND else 0 for x in rel.iloc[60:]]
        prev = next((x for x in reversed(side[:-1]) if x), 0)
        if side[-1] == -1 and prev == 1:
            out.append(ev(f"SMH:below60:{t}", "SMH", "半导体收破底线", "bear",
                          f"半导体SMH 收盘 {f(c)} <b>收破底线EMA60</b>（{f(e)}，低 {abs(rel.iloc[-1]) * 100:.1f}%）→ 大盘的主要支撑转弱，留意 ES/NQ 是否跟随跌破20均线组/底线", c, e, t))
        elif side[-1] == 1 and prev == -1:
            out.append(ev(f"SMH:above60:{t}", "SMH", "半导体收回底线", "bull",
                          f"半导体SMH 收盘 {f(c)} <b>收回底线EMA60</b>（{f(e)}）→ 半导体支撑恢复", c, e, t))
    # US 10y yield: up-cross of 5.3 / 5.5 re-arms only after closing 0.1 below; 'back below' only below 5.2
    d = Dc.get("US10Y")
    if d is not None and len(d) > 30:
        cl = d.close.iloc[-250:]
        t, c, pc = d.index[-1], cl.iloc[-1], cl.iloc[-2]
        for th in US10Y_TH:
            if c >= th > pc:
                last_above = [i for i, x in enumerate(cl.iloc[:-1]) if x >= th]
                if not last_above or (cl.iloc[last_above[-1]:-1] < th - 0.1).any():
                    out.append(ev(f"US10Y:{th}:{t}", "US10Y", "10年期利率", "bear",
                                  f"美10年期国债利率 收于 {c:.2f}%，升破 {th}%（高利率压估值，RSP/中小盘承压更大）", c, th, t))
        lo = US10Y_TH[0] - 0.1
        if pc >= lo > c and (cl.iloc[-30:-1] >= US10Y_TH[0]).any():
            out.append(ev(f"US10Y:down:{t}", "US10Y", "10年期利率", "bull",
                          f"美10年期国债利率 回落至 {c:.2f}%，跌回 {lo:.1f}% 下方（利率压力缓解）", c, lo, t))
    return out


# ----------------------------------------------------------------------------- intraday alerts
def live_events(D, H, state, now):
    out = []
    for k in ("ES", "NQ"):
        d = D.get(k)
        if d is None or len(d) < 130:
            continue
        t, r = d.index[-1], d.iloc[-1]
        p = r.close
        s = state.setdefault("live", {}).setdefault(k, {})
        prev_lvl = s.get("lvl")
        lvl = classify_live(r, p, prev_lvl)
        if s.get("day") != str(t):
            s.update(day=str(t), worst=lvl, best=lvl)
        s["worst"], s["best"] = max(s["worst"], lvl), min(s["best"], lvl)
        if prev_lvl is not None and max(lvl, 1) != max(prev_lvl, 1) and not closed(t, "fut", now):
            side = "bear" if lvl > prev_lvl else "bull"
            msg = f"{NAME[k]} 盘中 {f(p)}：{STATES[prev_lvl]} → <b>{STATES[lvl]}</b>（未收盘确认）。{lines_text(r, p)}"
            if lvl >= 3 > prev_lvl:
                msg += "<br>" + INSURANCE
            e = ev(f"{k}:live:{t}:{prev_lvl}>{lvl}", k, "盘中状态", side, msg, p, None, t)
            e["head"] = move_head(prev_lvl, lvl, "盘中")
            out.append(e)
        s["lvl"] = lvl
        # 1h EMA20/EMA60 cross on closed hourly bars
        h = H.get(k)
        if h is not None and len(h) > 70:
            hc = h.iloc[:-1]
            a, b = hc.ema20.iloc[-1] - hc.ema60.iloc[-1], hc.ema20.iloc[-2] - hc.ema60.iloc[-2]
            ts = hc.index[-1]
            if a < 0 <= b:
                out.append(ev(f"{k}:1h:dn:{ts}", k, "1h死叉", "bear",
                              f"{NAME[k]} 1小时 EMA20 下穿 EMA60（小级别转弱，日K走坏的先兆）。现价 {f(p)}，日K底线EMA60 {f(r.ema60)}", p, None, t))
            elif a > 0 >= b:
                out.append(ev(f"{k}:1h:up:{ts}", k, "1h金叉", "bull",
                              f"{NAME[k]} 1小时 EMA20 上穿 EMA60（小级别转强）。现价 {f(p)}", p, None, t))
        # at the close: intraday break that was reclaimed (破底翻 at the moving-average lines) / faded breakout
        if closed(t, "fut", now) and s.get("judged") != str(t):
            s["judged"] = str(t)
            cl = classify(r, p)
            if s["worst"] > cl and s["worst"] >= 2:
                out.append(ev(f"{k}:reclaim:{t}", k, "盘中跌破收回", "bull",
                              f"{NAME[k]} 盘中曾跌到“{STATES[s['worst']]}”，收盘 {f(p)} 收回到“{STATES[cl]}”→ 破底翻迹象（多头抵抗成功）", p, None, t))
            if s["best"] < cl and cl >= 2:
                out.append(ev(f"{k}:fade:{t}", k, "盘中站上又跌回", "bear",
                              f"{NAME[k]} 盘中曾回到“{STATES[s['best']]}”，收盘 {f(p)} 又跌回“{STATES[cl]}”→ 反弹失败", p, None, t))
    out += gold_live(D, H)
    return out


def gold_live(D, H):
    """GC on closed 1h bars: EMA20/60 cross, and 1h 破底翻 (poke below the prior ~2-day low within the last 6 bars,
    then the first close back above it by 0.1%). At most one 破底翻 per session."""
    out, d, h = [], D.get("GC"), H.get("GC")
    if d is None or h is None or len(h) < 80:
        return out
    t, r = d.index[-1], d.iloc[-1]
    hc = h.iloc[:-1]
    p, ts = hc.close.iloc[-1], hc.index[-1]
    a, b = hc.ema20.iloc[-1] - hc.ema60.iloc[-1], hc.ema20.iloc[-2] - hc.ema60.iloc[-2]
    day = f"日K：{STATES[classify(r, r.close)]}，底线EMA60 {f(r.ema60)}"
    if a < 0 <= b:
        out.append(ev(f"GC:1h:dn:{ts}", "GC", "1h死叉", "bear", f"黄金期货GC 1小时 EMA20 下穿 EMA60（小级别转弱）。现价 {f(p)}；{day}", p, None, t))
    elif a > 0 >= b:
        out.append(ev(f"GC:1h:up:{ts}", "GC", "1h金叉", "bull",
                      f"黄金期货GC 1小时 EMA20 上穿 EMA60（小时级多头排列成形，LEI：形成多头排列将打开上行空间）。现价 {f(p)}；{day}", p, None, t))
    swing_lo = hc.low.iloc[-54:-6].min()
    poke = hc.low.iloc[-6:].min()
    if poke < swing_lo and p > swing_lo * 1.001 >= hc.close.iloc[-2]:
        out.append(ev(f"GC:1hpdf:{t}", "GC", "1小时破底翻", "bull",
                      f"黄金期货GC 1小时跌破前低 {f(swing_lo)}（最低 {f(poke)}）后收回，现价 {f(p)} → <b>1小时破底翻</b>"
                      f"（看多信号；1小时EMA20上穿EMA60 = 多头排列确认；再跌回 {f(poke)} 下方则失败）。{day}", p, swing_lo, t))
    return out


# ----------------------------------------------------------------------------- daily report
def report(D, H, Dc, events_today, now):
    rows, heads = [], []
    for k in ("ES", "NQ", "RSP"):
        d = D.get(k)
        if d is None or len(d) < 130:
            continue
        r, pr = d.iloc[-1], d.iloc[-2]
        dc = Dc[k]
        rc, prc = dc.iloc[-1], dc.iloc[-2]
        lvl = classify(rc, rc.close)
        heads.append(f"{k}{STATES[lvl][:4]}")
        ded = " ".join(f"{n}{'↑' if rc.close > rc[f'ded{n}'] else '↓'}" for n in (20, 60, 120))
        h1 = ""
        if k in H:
            hc = H[k].iloc[:-1]
            bull = hc.ema20.iloc[-1] > hc.ema60.iloc[-1]
            h1 = f"<br>1小时：{'EMA20在EMA60上方（小级别偏多）' if bull else 'EMA20在EMA60下方（小级别偏空）'}"
        rows.append(
            f"<p><b>{NAME[k]}</b> 收 {f(rc.close)}（{pct(rc.close, prc.close):+.2f}%）｜<b>{STATES[lvl]}</b><br>"
            f"{lines_text(rc, rc.close)}<br>均线方向(抵扣价)：{ded}{h1}<br>"
            f"今日高/低 {f(rc.high)} / {f(rc.low)}：站上高点偏多，跌破低点偏空</p>")
    vol = []
    for k in ("SPY", "QQQ", "RSP"):
        d = Dc.get(k)
        if d is None:
            continue
        r = d.iloc[-1]
        ch, vr = pct(r.close, d.close.iloc[-2]), r.volume / r.vol20
        tag = "放量" if vr >= 1.3 else "缩量" if vr <= 0.8 else "平量"
        note = {("缩量", True): "健康（缩量上涨涨不停）", ("放量", False): "警惕（但放量下跌常跌不深）",
                ("放量", True): "资金进场", ("缩量", False): "抛压不重"}.get((tag, ch >= 0), "")
        vol.append(f"{k} {ch:+.2f}% {tag}{'上涨' if ch >= 0 else '下跌'}（{vr:.2f}x）{note}")
    ath = []
    for k in ("SPX", "NDX"):
        d = Dc.get(k)
        if d is not None:
            c, m = d.close.iloc[-1], d.close.max()
            ath.append(f"{NAME[k]} {f(c)}（{'历史新高' if c >= m else f'距新高 {pct(c, m):+.1f}%'}）")
    mac = []
    for k, lab in (("VIX", "VIX"), ("US10Y", "美10年债")):
        d = Dc.get(k)
        if d is not None:
            mac.append(f"{lab} {d.close.iloc[-1]:.2f}（{d.close.iloc[-1] - d.close.iloc[-2]:+.2f}）")
    lv = [classify(Dc[k].iloc[-1], Dc[k].close.iloc[-1]) for k in ("ES", "NQ") if k in Dc]
    w = max(lv) if lv else 0
    concl = {0: "趋势向上，均线三组同向上，少动为宜，持有让利润奔跑。", 1: "多头趋势，未见破坏迹象，最小阻力方向仍向上。",
             2: "回调中（在20均线组下方），盯住底线EMA60，守住就仍是多头回调。",
             3: "跌破底线EMA60：多头优势丢失，偏空思考，可考虑买保险。", 4: "空头占优：保护仓位/对冲优先。"}[w]
    ev_html = "".join(f"<li>{e['msg']}</li>" for e in events_today) or "<li>无</li>"
    date = Dc["ES"].index[-1] if "ES" in Dc else ""
    title = f"美股趋势 {date:%m/%d}｜" + " ".join(heads)
    html = (f"<p style='color:#566172'>纽约 {date} 交易日收盘后 · 发送于 {now.astimezone(TZ):%m/%d %H:%M} {TZ.key.split('/')[-1]}时间</p>"
            f"<p><b>结论：</b>{concl}</p>{''.join(rows)}"
            f"<p><b>量能：</b><br>{'<br>'.join(vol)}</p><p><b>指数：</b>{'；'.join(ath)}<br><b>宏观：</b>{'；'.join(mac)}</p>"
            f"<p><b>今日触发：</b></p><ul>{ev_html}</ul>"
            f"<p style='color:#888;font-size:12px'>规则：日K 20/60/120 均线组（MA+EMA）+ 抵扣价 + 1小时EMA20/60 + 破底翻 + 量能，仿 LEI 笔记。期货为连续合约（换月有缺口）。非投资建议。</p>")
    snap = {"date": str(date), "sent": now.isoformat(), "levels": {k: classify(Dc[k].iloc[-1], Dc[k].close.iloc[-1]) for k in TREND if k in Dc},
            "close": {k: float(Dc[k].close.iloc[-1]) for k in ("ES", "NQ", "SPX", "NDX", "RSP") if k in Dc}}
    return title, html, snap



# ----------------------------------------------------------------------------- 10/21 review queue
# Avalon 10/21: only the important daily-close signals; each one is reviewed and rewritten by Claude
# (scheduled task "美股提醒 AI 审核", weekdays after the close) before it reaches him.
PENDING = ST / "pending.jsonl"
SKIPPED = ST / "skipped.jsonl"
KEEP_TYPES = {"收盘状态", "20均线拐头", "站上所有均线", "跌破所有均线", "破底翻", "假突破", "历史新高", "放量止跌", "VIX",
              "半导体收破底线", "半导体收回底线", "10年期利率"}
RAW_AFTER_H = float(os.environ.get("US_ALERT_RAW_AFTER_H", "5"))   # unreviewed for this long -> send the raw text


def keep(e):
    # 10/22 Avalon (盘中暴跌没收到提醒): also push ES/NQ intraday moves across EMA60 / into-out of 空头占优
    # 10/22 later: both directions (跌破/收回底线EMA60, 进入/离开空头占优)
    if e["type"] == "盘中状态" and e["sym"] in ("ES", "NQ"):
        a, b = e["key"].rsplit(":", 1)[-1].split(">")
        return max(int(a), int(b)) >= 3
    return e["type"] in KEEP_TYPES and e["sym"] not in GOLD


def read_pending():
    return [json.loads(l) for l in PENDING.read_text().splitlines() if l.strip()] if PENDING.exists() else []


def fallback_raw(now):
    """safety net: if Claude's review hasn't happened within RAW_AFTER_H hours, push the raw alerts."""
    P = read_pending()
    old = [e for e in P if (now - datetime.fromisoformat(e["queued_at"])).total_seconds() > RAW_AFTER_H * 3600]
    if not old:
        return
    for e in old:
        send(e["title"] + "（未经审核）", e["html"], False)
        append(ST / "pending_done.jsonl", {**e, "done": "raw", "done_at": now.isoformat()})
    PENDING.write_text("".join(json.dumps(e, ensure_ascii=False, default=str) + "\n" for e in P if e not in old))


def clear_pending(now):
    P = read_pending()
    for e in P:
        append(ST / "pending_done.jsonl", {**e, "done": "reviewed", "done_at": now.isoformat()})
    PENDING.write_text("")
    return len(P)


# ----------------------------------------------------------------------------- send / state
def send(title, html, dry):
    tok = os.environ.get("US_ALERT_TOKEN") or os.environ.get("PUSHPLUS_TOKEN")
    if dry or not tok:
        print("[dry]" if dry else "[no token]", title)
        return True
    import requests
    j = requests.post("https://www.pushplus.plus/send", timeout=30,
                      json={"token": tok, "title": title, "content": html, "template": "html"}).json()
    print("pushplus:", j.get("code"), j.get("msg"), title)
    return j.get("code") == 200


def load_state():
    try:
        return json.loads(STATE.read_text())
    except Exception:  # noqa: BLE001
        return {}


def append(path, obj):
    with open(path, "a") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")


def run(args):
    now = datetime.now(timezone.utc).astimezone(NY)
    ST.mkdir(parents=True, exist_ok=True)
    bars = load_cached() if args.no_fetch else fetch()
    D, H = prep(bars)
    if "ES" not in D or "NQ" not in D:
        print("us_trend: ES/NQ missing, skip")
        return 1
    Dc = closed_frames(D, now)
    state = load_state()
    first = "close" not in state
    sent = state.setdefault("sent", {})
    evs = close_events(Dc, state.setdefault("close", {})) + live_events(D, H, state, now)
    if first:   # first run only seeds the state; don't fire on history
        for e in evs:
            sent[e["key"]] = now.isoformat()
        evs = []
    new = [e for e in evs if e["key"] not in sent]
    if not args.dry_run:      # 10/22: keys merged into another message (站上/跌破所有均线) count as sent
        for e in new:
            for a in e.get("also", []):
                sent[a] = now.isoformat()
    new = [e for e in new if e["key"] not in {a for x in new for a in x.get("also", [])}]
    queue = os.environ.get("US_ALERT_MODE", "queue") == "queue"
    for e in new:
        arrow = "↑ " if e["side"] == "bull" else "↓ "
        head = e.get("head") or (arrow + e["type"])
        title = f"【美股{'警示' if e['side'] == 'bear' else '提示'}】{NAME.get(e['sym'], e['sym'])} {head}"
        if e["sym"] == "RSP":
            # Avalon 10/23: RSP "强势多头" push on a down day read like "US stocks strong" while NQ was 空头占优.
            # RSP = breadth, not the index: always show ES/NQ state; bullish breadth while ES/NQ are in
            # 警戒/空头 gets the neutral 【美股宽度】 label instead of 【美股提示】.
            idx = {k: classify(D[k].iloc[-1], D[k].close.iloc[-1]) for k in ("ES", "NQ") if D.get(k) is not None}
            weak = any(v >= 3 for v in idx.values())
            label = "警示" if e["side"] == "bear" else ("宽度" if weak else "提示")
            tag = "，".join(f"{k} {SHORT_STATE[v]}" for k, v in idx.items())
            title = f"【美股{label}】{NAME.get('RSP', 'RSP')}（市场宽度）{head}｜指数：{tag}"
        html = (f"<p>{e['msg']}</p>" + breadth_meaning(D, e) + explain(e["msg"]) +
                f"<p style='color:#888'>{now.astimezone(TZ):%m/%d %H:%M} {TZ.key.split('/')[-1]} · 非投资建议</p>")
        if not keep(e):          # 10/21: small-timeframe / gold / volume noise is logged only, never pushed
            if not args.dry_run:
                sent[e["key"]] = now.isoformat()
                append(SKIPPED, {**e, "logged_at": now.isoformat()})
            continue
        if queue:                # 10/21: Claude reviews the queue after the close and sends one note
            if not args.dry_run:
                sent[e["key"]] = now.isoformat()
                append(PENDING, {**e, "title": title, "html": html, "queued_at": now.isoformat()})
                append(EVENTS, {**e, "status": "queued", "sent_at": now.isoformat()})
            else:
                print("[dry] queue", title)
            continue
        if send(title, html, args.dry_run) and not args.dry_run:
            sent[e["key"]] = now.isoformat()
            append(EVENTS, {**e, "sent_at": now.isoformat()})
    if queue and not args.dry_run:
        fallback_raw(now)
    # daily report once per cash session, after the close
    spx_last = D["SPX"].index[-1] if "SPX" in D else None
    day = str(spx_last)
    want_daily = os.environ.get("US_ALERT_DAILY") == "1"
    if (args.force_daily or (spx_last == now.date() and closed(spx_last, "fut", now) and state.get("daily") != day)):
        today = [e for e in evs if e.get("date") == day] + [json.loads(l) for l in (EVENTS.read_text().splitlines() if EVENTS.exists() else [])
                                                           if json.loads(l).get("date") == day and json.loads(l)["key"] not in {x["key"] for x in evs}]
        title, html, snap = report(D, H, Dc, today, now)
        PREVIEW.write_text(f"<h3>{title}</h3>{html}")
        if args.dry_run:
            pass
        elif (want_daily or args.force_daily) and send(title, html, False):
            state["daily"] = day
            append(DAILY, snap)
        elif not (want_daily or args.force_daily):   # AI daily review (scheduled task) replaces the push
            state["daily"] = day
            append(DAILY, snap)
    if not args.dry_run:
        cut = (now - timedelta(days=40)).isoformat()
        state["sent"] = {k: v for k, v in sent.items() if v >= cut}
        STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1, default=str))
    print(f"us_trend: {len(new)} new alert(s); ES {STATES[classify(D['ES'].iloc[-1], D['ES'].close.iloc[-1])]}, "
          f"NQ {STATES[classify(D['NQ'].iloc[-1], D['NQ'].close.iloc[-1])]}")
    return 0


def replay(args):
    D, _ = prep(load_cached() if args.no_fetch else fetch())
    prev, seen = {}, set()
    dates = [t for t in D["ES"].index if str(t) >= args.replay]
    warm = D["ES"].index[D["ES"].index.get_loc(dates[0]) - 1]
    close_events({k: d.loc[:warm] for k, d in D.items()}, prev)
    for t in dates:
        for e in close_events({k: d.loc[:t] for k, d in D.items()}, prev):
            if e["key"] not in seen:
                seen.add(e["key"])
                seen.update(e.get("also", []))
                print(t, "|", e["type"], "|", e["msg"].replace("<br>", " / ").replace("<b>", "").replace("</b>", ""))
    for k in [x for x in ("ES", "NQ", "RSP", "GC") if x in D]:
        r = D[k].iloc[-1]
        print(f"now {k}: {STATES[classify(r, r.close)]} | {lines_text(r, r.close)}")


def review(args):
    D, _ = prep(load_cached() if args.no_fetch else fetch())
    if not EVENTS.exists():
        print("no events logged yet")
        return
    rows = []
    for l in EVENTS.read_text().splitlines():
        e = json.loads(l)
        sym = {"SPY": "SPX", "QQQ": "NDX", "VIX": "ES", "RSP": "RSP"}.get(e["sym"], e["sym"])
        d = D.get(sym)
        if d is None or not e.get("date"):
            continue
        idx = [i for i, t in enumerate(d.index) if str(t) >= e["date"]]
        if not idx:
            continue
        i0 = idx[0]
        base = d.close.iloc[i0]
        row = {"date": e["date"], "sym": e["sym"], "type": e["type"], "side": e["side"]}
        for n in (1, 5, 10, 20):
            row[f"fwd{n}"] = round(pct(d.close.iloc[i0 + n], base), 2) if i0 + n < len(d) else None
        rows.append(row)
    df = pd.DataFrame(rows)
    print(df.to_string(index=False))
    df.to_csv(ST / "review.csv", index=False)
    for side, g in df.groupby("side"):
        x = g["fwd5"].dropna()
        if len(x):
            ok = (x > 0).mean() if side == "bull" else (x < 0).mean()
            print(f"{side}: n={len(x)} avg fwd5 {x.mean():+.2f}%  right-direction {ok:.0%}")


def facts(args):
    """compact JSON for the daily AI review (scheduled task). Uses the bars cached by the Mac's hourly run."""
    now = datetime.now(timezone.utc).astimezone(NY)
    D, H = prep(load_cached() if args.no_fetch else fetch())
    Dc = closed_frames(D, now)
    out = {"as_of_pt": f"{now.astimezone(TZ):%Y-%m-%d %H:%M}", "bars_as_of_utc": str(D["ES"].ts.iloc[-1]) if "ts" in D["ES"] else None,
           "session": str(Dc["ES"].index[-1]), "trend": {}, "volume": {}, "index": {}, "macro": {}}
    for k in TREND + GOLD:
        d = Dc.get(k)
        if d is None:
            continue
        r, pr = d.iloc[-1], d.iloc[-2]
        x = {"close": round(r.close, 2), "chg_pct": round(pct(r.close, pr.close), 2), "state": STATES[classify(r, r.close)],
             "prev_state": STATES[classify(pr, pr.close)],
             "g20": [round(min(r.ma20, r.ema20), 2), round(max(r.ma20, r.ema20), 2)], "ema60": round(r.ema60, 2),
             "g60": [round(min(r.ma60, r.ema60), 2), round(max(r.ma60, r.ema60), 2)],
             "g120": [round(min(r.ma120, r.ema120), 2), round(max(r.ma120, r.ema120), 2)],
             "ma_rising_next": {n: bool(r.close > r[f"ded{n}"]) for n in (20, 60, 120)},
             "high": round(r.high, 2), "low": round(r.low, 2), "prev_high": round(pr.high, 2), "prev_low": round(pr.low, 2),
             "low_10d_before": round(d.low.iloc[-11:-1].min(), 2), "high_10d_before": round(d.high.iloc[-11:-1].max(), 2)}
        # position vs lines (for the AI analysis: LEI reads "above/inside/below the 20 group", distance to 底线)
        g20lo, g20hi = min(r.ma20, r.ema20), max(r.ma20, r.ema20)
        x["vs_g20"] = "above" if r.close > g20hi else ("below" if r.close < g20lo else "inside")
        x["pct_vs_ema60"] = round(pct(r.close, r.ema60), 2)
        x["ma20_slope5d_pct"] = round(pct(r.ma20, d.ma20.iloc[-6]), 2)
        # weekly MAs (LEI: weekly EMA20/60 cross / "avoid the cross" rally; his levels are often weekly lines)
        w = d[["close", "high", "low"]].copy()
        w.index = pd.to_datetime(w.index)
        w = w.resample("W-FRI").agg({"close": "last", "high": "max", "low": "min"}).dropna()
        if len(w) >= 25:
            wc = w.close
            we20, we60 = wc.ewm(span=20, adjust=False).mean(), wc.ewm(span=60, adjust=False).mean()
            gap = (we20 - we60) / we60 * 100
            x["weekly"] = {"weeks": len(w), "close": round(wc.iloc[-1], 2), "ema20": round(we20.iloc[-1], 2), "ema60": round(we60.iloc[-1], 2),
                           "ma20": round(wc.rolling(20).mean().iloc[-1], 2),
                           "ema20_minus_60_pct": round(gap.iloc[-1], 2), "gap_4w_ago_pct": round(gap.iloc[-5], 2) if len(gap) > 5 else None,
                           "align": "bull" if we20.iloc[-1] > we60.iloc[-1] else "bear",
                           "pct_vs_ema20": round(pct(wc.iloc[-1], we20.iloc[-1]), 2), "pct_vs_ema60": round(pct(wc.iloc[-1], we60.iloc[-1]), 2),
                           "note": "ema60 needs ~60+ weeks of history to settle" if len(w) < 80 else ""}
        if k in H:
            hc = H[k].iloc[:-1]
            x["h1"] = {"ema20": round(hc.ema20.iloc[-1], 2), "ema60": round(hc.ema60.iloc[-1], 2), "ema120": round(hc.ema120.iloc[-1], 2),
                       "bias": "bull" if hc.ema20.iloc[-1] > hc.ema60.iloc[-1] else "bear",
                       "ema60_vs_120": "bull" if hc.ema60.iloc[-1] > hc.ema120.iloc[-1] else "bear",
                       "ema60_minus_120_pct": round(pct(hc.ema60.iloc[-1], hc.ema120.iloc[-1]), 3),
                       "last_bar_utc": str(hc.index[-1])}
        out["trend"][k] = x
    for k in ("SPY", "QQQ", "RSP"):
        d = Dc.get(k)
        if d is not None:
            r = d.iloc[-1]
            out["volume"][k] = {"chg_pct": round(pct(r.close, d.close.iloc[-2]), 2), "vol_x20d": round(r.volume / r.vol20, 2)}
    for k in ("SPX", "NDX"):
        d = Dc.get(k)
        if d is not None:
            out["index"][k] = {"close": round(d.close.iloc[-1], 2), "pct_from_ath_close": round(pct(d.close.iloc[-1], d.close.max()), 2)}
    d = Dc.get("SMH")
    if d is not None and len(d) > 70:
        out["semis"] = {"SMH": {"close": round(d.close.iloc[-1], 2), "chg_pct": round(pct(d.close.iloc[-1], d.close.iloc[-2]), 2),
                                "ema60": round(d.ema60.iloc[-1], 2), "pct_vs_ema60": round(pct(d.close.iloc[-1], d.ema60.iloc[-1]), 2)}}
    for k in ("VIX", "US10Y"):
        d = Dc.get(k)
        if d is not None:
            out["macro"][k] = {"close": round(d.close.iloc[-1], 3), "chg": round(d.close.iloc[-1] - d.close.iloc[-2], 3)}
    since = (now - timedelta(hours=40)).isoformat()
    out["alerts_sent_last_40h"] = [{k: e.get(k) for k in ("sent_at", "sym", "type", "side", "msg")}
                                   for e in (json.loads(l) for l in (EVENTS.read_text().splitlines() if EVENTS.exists() else []))
                                   if e.get("sent_at", "") >= since]
    out["rules"] = "states 0-4: " + " / ".join(STATES) + "; 0<->1 not pushed; intraday needs 0.15% beyond line; 1h EMA20/60 cross; 破底翻 = break 10-session low then first close back above; vol alerts >=1.3x & |chg|>=1%; facts-only (no push): vs_g20, pct_vs_ema60, ma20_slope5d_pct, weekly EMA20/60 (resampled W-FRI from daily), h1 EMA60 vs EMA120"
    print(json.dumps(out, ensure_ascii=False, default=str))


def send_note(args):
    """push the AI daily review: file = first line title, rest HTML. Copy kept in data/state/us_trend/notes/."""
    txt = Path(args.send_note).read_text().strip().split("\n", 1)
    title, html = txt[0].strip(), (txt[1] if len(txt) > 1 else "")
    (ST / "notes").mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).astimezone(TZ)
    (ST / "notes" / f"{stamp:%Y-%m-%d}.html").write_text(f"<h3>{title}</h3>{html}")
    if "名词解释" not in html:
        html += explain(title + html)
    ok = send(title, html, args.dry_run)
    if ok and not args.dry_run:
        append(ST / "notes.jsonl", {"sent_at": stamp.isoformat(), "title": title})
        if args.clear_pending:
            print("pending cleared:", clear_pending(stamp))
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-fetch", action="store_true", help="use the cached bars in data/state/us_trend/bars")
    ap.add_argument("--force-daily", action="store_true")
    ap.add_argument("--replay", metavar="YYYY-MM-DD")
    ap.add_argument("--review", action="store_true")
    ap.add_argument("--facts", action="store_true", help="compact JSON for the daily AI review")
    ap.add_argument("--send-note", metavar="FILE", help="push an AI daily review (line 1 = title, rest = HTML)")
    ap.add_argument("--pending", action="store_true", help="print the queued alerts (JSON lines) waiting for review")
    ap.add_argument("--clear-pending", action="store_true", help="with --send-note: archive the queue after sending")
    a = ap.parse_args()
    if a.pending:
        P = read_pending()
        print(json.dumps({"n": len(P), "alerts": [{k: e.get(k) for k in ("queued_at", "sym", "type", "side", "msg", "price", "level", "date")} for e in P]},
                         ensure_ascii=False, default=str, indent=0))
    elif a.facts:
        facts(a)
    elif a.send_note:
        sys.exit(send_note(a))
    elif a.replay:
        replay(a)
    elif a.review:
        review(a)
    else:
        sys.exit(run(a))
