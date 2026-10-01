"""
Push the daily market report (spot ETF flows + market, funding, derivatives, macro, CME, XRP watch, rebound
signals) to WeChat through PushPlus (https://www.pushplus.plus).

Runs on the Mac from scripts/ops/run_update.sh after `mdh export`; reads data/dashboard/data.json.
Two versions, one message per new US ETF session (data/state/pushplus_last.txt):
  * AI version: the Claude scheduled task "微信日报 AI 点评" reads `--facts`, writes a short commentary to a file and
    runs `--ai-note <file>`; the commentary replaces the rule-based "今日要点" card.
  * Rule-based fallback: the hourly run sends the plain version only if the AI version hasn't gone out by
    PUSHPLUS_FALLBACK_UTC_HOUR (default 3 = 11:00 Beijing) the day after the session.
Flags: --facts (JSON for the AI), --status, --ai-note FILE, --dry-run [--ai-note FILE], --force.

.env
  PUSHPLUS_TOKEN   PushPlus user token (account must be real-name verified; error 905 otherwise)
  PUSHPLUS_TO      optional friend token(s), comma separated, to send to instead of yourself
  PUSHPLUS_READER_TOKEN  optional: the reader's OWN user token (dad's). Messages are sent with it, so they land in the
                   reader's WeChat; his account must be real-name verified (error 905 otherwise). Beijing time.
  PUSHPLUS_ASSETS  optional ETF assets, default "ETH,BTC,SOL,XRP,ZEC,NEAR,HYPE"

One HTML page (PushPlus "html" template, <= 20,000 characters), light colors fixed on every block so WeChat's
dark mode stays readable; no images, so nothing has to be hosted.

  python3 scripts/ops/push_etf_report.py            normal (scheduled) run
  python3 scripts/ops/push_etf_report.py --force    send now regardless of time / last sent
  python3 scripts/ops/push_etf_report.py --dry-run  write data/state/push_preview.html, send nothing
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "data" / "dashboard" / "data.json"
# one "last sent" record per recipient set: a new PUSHPLUS_TO (e.g. dad's friend token) starts fresh and gets the latest session
_READER = os.environ.get("PUSHPLUS_READER_TOKEN", "").strip()   # the reader's own user token (dad): send with it
_TO = os.environ.get("PUSHPLUS_TO", "").strip() or _READER
# --to-me: TEST send of exactly what dad would get (same Beijing clock), but with Avalon's own PUSHPLUS_TOKEN,
# never to dad / friends, always "forced", and the last-sent state is left untouched
_ME = "--to-me" in sys.argv
STATE = REPO / "data" / "state" / ("pushplus_last.txt" if not _TO else
                                   "pushplus_last_" + hashlib.sha1(_TO.encode()).hexdigest()[:8] + ".txt")
PREVIEW = REPO / "data" / "state" / "push_preview.html"
XRP_RANGE = (1.25, 1.70)            # Avalon's range levels
XRP_BTC_LEVELS = (1696, 1795)       # sats: invalidate below, confirm above

CSS = """<style>
.r{font-family:-apple-system,Helvetica,Arial,sans-serif;color:#0e1621;background:#eef1f5;padding:8px;border-radius:12px;max-width:640px;font-size:13px}
.h{background:#0f1f33;color:#fff;border-radius:10px;padding:12px 14px}.h .e{font-size:11px;letter-spacing:1px;color:#b9a6ff}.h .t{font-size:20px;font-weight:700}.h .s{font-size:12px;color:#c9d3e0}
.c{background:#fff;color:#0e1621;border:1px solid #e1e5eb;border-radius:10px;padding:10px 12px;margin:10px 0}
.c h3{margin:0 0 6px;font-size:15px;color:#0e1621}.m{color:#566172;font-size:12px}.sum{background:#fffbea;border-color:#f3e3a3;line-height:1.7;font-size:14px}
table{width:100%;border-collapse:collapse}td{padding:4px 3px;font-size:13px;color:#0e1621;border-bottom:1px solid #eef1f5}td.n{text-align:right;white-space:nowrap}
.u{color:#1a8f5a}.d{color:#d03b3b}.z{color:#566172}.b{font-weight:700}.bar{height:9px;border-radius:3px}.bg{background:#1a8f5a}.br{background:#d03b3b}
.k td{border:0;vertical-align:top;width:33%}.k .v{font-size:17px;font-weight:700}.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:3px}
.p{line-height:1.6;margin:6px 0}.l{line-height:1.65;margin:8px 0;padding-bottom:6px;border-bottom:1px solid #eef1f5}.nw{white-space:nowrap}.f{font-size:11px;color:#566172;line-height:1.5;margin:6px 2px}
</style>"""


def ok(x):
    return x is not None and not (isinstance(x, float) and (math.isnan(x) or math.isinf(x)))


def num(x, d=0):
    return "–" if not ok(x) else f"{abs(x):,.{d}f}"


def sn(x, d=0):
    return "–" if not ok(x) else ("+" if x > 0 else "−" if x < 0 else "") + num(x, d)


def cl(x):
    return "u" if ok(x) and x > 0 else "d" if ok(x) and x < 0 else "z"


def cn(x, d=0, sign=True):
    """Chinese units for big coin counts: 1.35亿 / 31.4万; small numbers as is."""
    if not ok(x):
        return "–"
    sg = ("+" if x > 0 else "−" if x < 0 else "") if sign else ""
    a = abs(x)
    if a >= 1e8:
        return f"{sg}{a / 1e8:,.2f}亿"
    if a >= 1e5:
        return f"{sg}{a / 1e4:,.1f}万"
    return f"{sg}{a:,.{d}f}"


def cspan(x, d=0):
    return f'<span class="{cl(x)}">{cn(x, d)}</span>'


def span(x, d=0, suf=""):
    return f'<span class="{cl(x)}">{sn(x, d)}{suf}</span>'


def px(x):
    return "–" if not ok(x) else f"${x:,.0f}" if x >= 1000 else f"${x:,.2f}" if x >= 10 else f"${x:,.3f}" if x >= 1 else f"${x:,.4f}"


def usd(x):
    if not ok(x):
        return "–"
    s = "+" if x > 0 else "−" if x < 0 else ""
    return f"{s}${abs(x) / 1e9:,.2f}B" if abs(x) >= 1e9 else f"{s}${abs(x) / 1e6:,.1f}M"


def lastv(dates, vals, ago=0):
    """(date, value) of the last valid point, and the valid value `ago` observations earlier."""
    idx = [i for i, v in enumerate(vals) if ok(v)]
    if not idx:
        return None, None, None
    i = idx[-1]
    j = idx[-1 - ago] if ago and len(idx) > ago else None
    return dates[i], vals[i], (vals[j] if j is not None else None)


def nd(a):
    return 2 if a in ("BTC", "ZEC") else 0


def row(*cells):
    return "<tr>" + "".join(f'<td class="n">{c}</td>' if i else f"<td>{c}</td>" for i, c in enumerate(cells)) + "</tr>"


# ---------------------------------------------------------------------------- sections
def s_market(D):
    rows = []
    for a in ("BTC", "ETH", "SOL", "XRP", "ZEC", "HYPE"):
        c = (D.get("coins") or {}).get(a)
        if not c:
            continue
        d1, dv = c["tf"]["1d"], c.get("deriv") or {}
        rows.append(row(f'<b>{a}</b>', px(d1["close"]), span(d1["chg_pct"], 1, "%"), span(dv.get("price_chg7_pct"), 1, "%"),
                        span(dv.get("price_chg30_pct"), 1, "%")))
    m = D["macro"]
    _, fg, fg7 = lastv(m["date"], m["fg"], 7)
    _, bd, bd7 = lastv(m["date"], m["btc_d_ex"], 7)
    _, t3, t37 = lastv(m["date"], m["total3_b"], 7)
    fgl = "极度恐慌" if fg <= 24 else "恐慌" if fg <= 44 else "中性" if fg <= 55 else "贪婪" if fg <= 74 else "极度贪婪"
    return (f'<div class="c"><h3>市场快照</h3><table><tr><td class="m">币种</td><td class="n m">日收盘</td><td class="n m">1日</td><td class="n m">7日</td><td class="n m">30日</td></tr>{"".join(rows)}</table>'
            f'<div class="p">恐慌贪婪 <b>{fg:.0f}</b>（{fgl}，一周前 {num(fg7)}）· BTC 市占率（扣除稳定币）<b>{bd:.2f}%</b>（7日 {span(bd - bd7 if ok(bd7) else None, 2, " pt")}）'
            f' · TOTAL3 <b>${t3:,.0f}B</b>（7日 {span((t3 / t37 - 1) * 100 if ok(t37) else None, 1, "%")}）</div></div>'), fg, fgl


def s_flows(D):
    m = D["macro"]
    _, st, st7 = lastv(m["date"], m["stables_b"], 7)
    _, _, st30 = lastv(m["date"], m["stables_b"], 30)
    lines = [f'稳定币总供应 <b>${st:,.1f}B</b>，7日 {span(st - st7 if ok(st7) else None, 1, "B")}，30日 {span(st - st30 if ok(st30) else None, 1, "B")}']
    for a in ("BTC", "ETH"):
        x = (D.get("xc") or {}).get(a) or {}
        if x.get("cbp_1d"):
            dt, v, _ = lastv(x["cbp_1d"]["date"], x["cbp_1d"]["bp"])
            vals = [b for b in x["cbp_1d"]["bp"][-7:] if ok(b)]
            avg = sum(vals) / len(vals) if vals else None
            lines.append(f'{a} Coinbase 溢价 {span(v, 1, " bp")}（{dt[5:]}），7日均 {span(avg, 1, " bp")}')
    x = (D.get("xc") or {}).get("BTC") or {}
    if x.get("kp_1d"):
        dt, v, _ = lastv(x["kp_1d"]["date"], x["kp_1d"]["pct"])
        lines.append(f'BTC 泡菜溢价 {span(v, 2, "%")}（{dt[5:]}）')
    return ('<div class="c"><h3>资金面</h3>' + "".join(f'<div class="p">{l}</div>' for l in lines)
            + '<div class="m">Coinbase 溢价为正 = 美国买家愿意多付钱；泡菜溢价为正 = 韩国散户追涨。</div></div>'), st, st7


def s_derivs(D):
    rows = []
    for a in ("BTC", "ETH", "XRP", "SOL"):
        c = (D.get("coins") or {}).get(a)
        if not c or not c.get("deriv"):
            continue
        dv = c["deriv"]; f = dv.get("funding") or {}
        ls = None
        dd = (D.get("deriv") or {}).get(f"{a}USDT", {}).get("daily")
        if dd:
            _, ls, _ = lastv(dd["date"], dd["ls_accounts"])
        rows.append(row(f"<b>{a}</b>", span(f.get("avg7_bp8"), 2, " bp") if f else span(dv.get("funding_apr_7d"), 1, "%"),
                        f'{usd(dv.get("oi_usd")).lstrip("+")}', span(dv.get("oi_chg7_pct"), 1, "%"), num(ls, 2)))
    tot = ""
    x = (D.get("xc") or {}).get("BTC") or {}
    if x.get("oi"):
        o = x["oi"]
        def total(i):
            return sum(o[k][i] for k in ("binance", "okx", "bybit", "hyperliquid") if k in o and ok(o[k][i]))
        n = len(o["date"])
        if n > 8:
            t0, t7 = total(n - 1), total(n - 8)
            tot = f'<div class="p">BTC 四所合计持仓 <b>${t0:,.1f}B</b>，7日 {span((t0 / t7 - 1) * 100 if t7 else None, 1, "%")}</div>'
    return ('<div class="c"><h3>合约情绪</h3><table><tr><td class="m">币种</td><td class="n m">费率 7日均/8h</td><td class="n m">币安持仓</td><td class="n m">持仓7日</td><td class="n m">多空比</td></tr>'
            + "".join(rows) + f'</table>{tot}<div class="m">币安默认费率为 +1 bp/8h：高于它说明多头拥挤，负数说明空头拥挤。多空比为账户数之比。</div></div>')


def s_macro(D):
    m = D["macro"]
    items = [("美债10年", "us10y", 2, "%", "bp"), ("美元指数", "dxy", 2, "", "%"), ("VIX", "vix", 1, "", "%"),
             ("标普500", "spx", 0, "", "%"), ("美联储净流动性", "netliq_t", 2, "T", "B")]
    rows = []
    for name, k, d, suf, mode in items:
        dt, v, v5 = lastv(m["date"], m[k], 5)
        if not ok(v):
            continue
        ch = ((v - v5) * 100 if mode == "bp" else (v - v5) * 1000 if mode == "B" else (v / v5 - 1) * 100) if ok(v5) else None
        unit = " bp" if mode == "bp" else "B" if mode == "B" else "%"
        rows.append(f'<div class="l"><b>{name}</b>　{"$" if k == "netliq_t" else ""}{v:,.{d}f}{suf}　<span class="m nw">5天 {sn(ch, 0 if mode != "%" else 2)}{unit} · {md(dt)}</span></div>')
    return ('<div class="c"><h3>宏观 <span class="m">（日期为美东）</span></h3>'
            + "".join(rows) + '<div class="m">美债收益率上行、美元走强，通常对加密资产不利；VIX 升高代表市场避险情绪上升；美联储净流动性增加一般利好风险资产。</div></div>')


R_DATE = [""]
# the reader's clock: dad (PUSHPLUS_TO set) reads Beijing time, Avalon (self) reads California time; PUSHPLUS_TZ overrides
_TZ = os.environ.get("PUSHPLUS_TZ", "").strip() or ("Asia/Shanghai" if _TO else "America/Los_Angeles")
BJ = ZoneInfo(_TZ)
LOC = {"Asia/Shanghai": "北京", "America/Los_Angeles": "加州", "America/New_York": "美东"}.get(_TZ, _TZ)
ET = ZoneInfo("America/New_York")


def et_local(d, h, m=0) -> str:
    """US Eastern wall time on date d, shown on the reader's clock, e.g. '北京 20:30' / '北京次日 2:00' / '加州 5:30'."""
    e = datetime(d.year, d.month, d.day, h, m, tzinfo=ET).astimezone(BJ)
    nd_ = (e.date() - d).days
    return f"{LOC}{'次日' if nd_ == 1 else '前一日' if nd_ == -1 else ''} {e.hour}:{e.minute:02d}"
PX_ASOF = [""]      # "9/29 08:00": Beijing time of the last closed Binance daily candle (UTC 00:00 = Beijing 08:00)


def md(d: str) -> str:
    """'2026-09-28' -> '9/28'"""
    return f"{int(d[5:7])}/{int(d[8:10])}"


LIVE: dict = {}     # symbol -> {"px", "c24", "c7"}: rolling 24h / 7d changes from the latest Binance 1h spot candle
LIVE_ASOF = [""]


def load_live(symbols):
    """Latest price and rolling 24h/7d % changes, so the report matches what an exchange app shows right now."""
    try:
        import duckdb
        con = duckdb.connect(str(S.DB_PATH), read_only=True)
        rows = con.execute("""
            WITH x AS (SELECT symbol, ts, close FROM bn_kline_1h WHERE market = 'spot' AND list_contains(?, symbol)),
                 l AS (SELECT symbol, max(ts) ts FROM x GROUP BY symbol)
            SELECT l.symbol, l.ts, a.close, b.close, c.close FROM l
            JOIN x a ON a.symbol = l.symbol AND a.ts = l.ts
            LEFT JOIN x b ON b.symbol = l.symbol AND b.ts = l.ts - INTERVAL 24 HOUR
            LEFT JOIN x c ON c.symbol = l.symbol AND c.ts = l.ts - INTERVAL 7 DAY""", [sorted(symbols)]).fetchall()
        con.close()
    except Exception as e:      # DB busy/missing: fall back to the last closed daily candle
        print("push_etf_report: live prices unavailable:", e, file=sys.stderr)
        return
    last = None
    for sym, ts, p, p24, p7 in rows:
        LIVE[sym] = {"px": p, "c24": (p / p24 - 1) * 100 if p24 else None, "c7": (p / p7 - 1) * 100 if p7 else None}
        last = ts if last is None or ts > last else last
    if last is not None:
        e = min(last.replace(tzinfo=timezone.utc) + timedelta(hours=1), datetime.now(timezone.utc)).astimezone(BJ)
        LIVE_ASOF[0] = f"{e.month}/{e.day} {e:%H:%M}"


def live_syms():
    return set(S.PUSH_HOLDINGS + ["BTC", "DASH", "ZEN"] + [x for v in S.PUSH_SECTORS.values() for x in v])


def live(C, a, key):
    """Live value: the export's rolling numbers (coins.live_changes; handles HYPE via Hyperliquid), else our own query, else daily."""
    c = C.get(a) or {}
    lv = c.get("live") or {}
    v = lv.get({"px": "price", "c24": "chg24_pct", "c7": "chg7_pct"}[key])
    if ok(v):
        return v
    v = (LIVE.get(a) or {}).get(key)
    if ok(v):
        return v
    return {"px": c.get("tf", {}).get("1d", {}).get("close"), "c24": c.get("tf", {}).get("1d", {}).get("chg_pct"),
            "c7": (c.get("deriv") or {}).get("price_chg7_pct")}[key]


def px_asof() -> str:
    F = load_1d("BTC")
    if not F or not F.get("t"):
        return ""
    t = F["t"][-1]
    t = t / 1000 if t > 1e11 else t
    e = datetime.fromtimestamp(t + 86400, tz=timezone.utc).astimezone(BJ)
    return f"{e.month}/{e.day} {e:%H:%M}"


def s_etf(R, assets):
    rows, detail = [], []
    for a in assets:
        r = R.get(a)
        if not r or r.get("error"):
            continue
        w, d = r["win"], nd(a)
        dd = 1 if a == "BTC" else 0
        own = f' <span class="m">纽约 {md(r["date"])}</span>' if r["date"] != R_DATE[0] else ""
        stk = f'，{r["streak"]}天连{"增" if r["streak_dir"] > 0 else "减"}' if r["streak"] >= 2 else ""
        rows.append(f'<div class="l"><b>{a}</b>{own}　当日 <b>{cspan(w["day"]["coins"], dd if abs(w["day"]["coins"] or 0) < 100 else 0)}</b> 枚 <span class="m nw">（{usd(w["day"]["usd"])}{stk}）</span><br>'
                    f'<span class="m">近7天 {cn(w["d7"]["coins"], 0)} · 本月 {cn(w["mtd"]["coins"], 0)} · 总持仓 {cn(r["hold"], 0, False)}</span></div>')
        if a in ("ETH", "BTC"):
            s = r["series"]; cs = s["coins"][-7:]; mx = max([abs(c or 0) for c in cs] + [1e-9])
            bars = "".join(f'<tr><td class="m" style="width:40px">{dt[5:]}</td><td><div class="bar {"bg" if (c or 0) >= 0 else "br"}" style="width:{max(2, abs(c or 0) / mx * 100):.0f}%"></div></td>'
                           f'<td class="n {cl(c)}" style="width:86px">{sn(c, d)}</td></tr>' for dt, c in zip(s["date"][-7:], cs))
            funds = r.get("funds") or []
            adds = sorted([f for f in funds if (f.get("coins") or 0) > 0], key=lambda f: -f["coins"])[:3]
            cuts = sorted([f for f in funds if (f.get("coins") or 0) < 0], key=lambda f: f["coins"])[:2]
            mv = ("<div class=\"p\">分基金：" + "　".join(f'<span class="{cl(f["coins"])}">{f["zh"]} {sn(f["coins"], d)}</span>' for f in adds + cuts) + "</div>") if adds or cuts else ""
            detail.append(f'<div class="c"><h3>{a} 现货 ETF · {r["date"]}</h3><div class="p">{r["text"]["zh"]}</div><table>{bars}</table>{mv}</div>')
    head = (f'<div class="c"><h3>美国现货 ETF<br><span class="m">纽约 {md(R_DATE[0])} 交易日 · 单位：枚</span></h3>'
            + "".join(rows) + '<div class="m">绿 = 净流入（买入），红 = 净流出。日期不同的行标注了自己的日期（ZEC 来自灰度官方文件、NEAR、HYPE 来自发行商官网，通常晚一个交易日；NEAR ETF 于 9/29 上市，HYPE 合计从 9/30 起有 Bitwise 数据）。万 = 10⁴，亿 = 10⁸。</div></div>')
    return head + "".join(detail)


def s_cme(D):
    x = ((D.get("xc") or {}).get("BTC") or {}).get("cme")
    if not x or len(x["date"]) < 2:
        return ""
    i = len(x["date"]) - 1
    ch = lambda k: x[k][i] - x[k][i - 1] if ok(x[k][i]) and ok(x[k][i - 1]) else None
    return (f'<div class="c"><h3>CME 比特币期货持仓（周度，{x["date"][i]}）</h3><table>'
            + row("总持仓", f'{num(x["oi"][i])} BTC', span(ch("oi")))
            + row("杠杆基金净头寸", f'{sn(x["lev_net"][i])} BTC', span(ch("lev_net")))
            + row("资管机构净头寸", f'{sn(x["am_net"][i])} BTC', span(ch("am_net")))
            + '</table><div class="m">右列为较上周变化。杠杆基金（对冲基金）常做"买现货 ETF、空 CME 期货"的基差套利，所以它的净空头往往跟着 ETF 流入一起变大。</div></div>')


def s_xrp(D):
    c = (D.get("coins") or {}).get("XRP")
    if not c:
        return "", None
    p = c["tf"]["1d"]["close"]; rel = c.get("rel") or {}
    lo, hi = XRP_RANGE
    pos = (p - lo) / (hi - lo) * 100
    ratio = (rel.get("ratio") or 0) * 1e8
    a, b = XRP_BTC_LEVELS
    rstate = "已站上确认位" if ratio >= b else "跌破失效位" if ratio < a else "在两者之间"
    since = ((D.get("since") or {}).get("coins") or {}).get("XRP")
    sline = ""
    if since:
        allc = D["since"]["coins"]
        rk = sorted(allc, key=lambda k: -(allc[k]["pct"][-1] or -1e9)).index("XRP") + 1
        sline = f'<div class="p">9/18 买入以来：<b>{sn(since["pct"][-1], 1)}%</b>（{px(since["base"])} → {px(since["last"])}），{len(allc)} 个币中排第 {rk}</div>'
    txt = (f'<div class="c"><h3>持仓关注：XRP</h3>'
           f'<div class="p">价格 <b>{px(p)}</b>，在 1.25–1.70 区间的 <b>{pos:.0f}%</b> 位置（0% = 区间底，100% = 区间顶）</div>'
           f'<div class="p">XRP/BTC <b>{ratio:,.0f} sats</b>：确认位 {b:,}、失效位 {a:,}，目前<b>{rstate}</b></div>'
           f'<div class="p">60日 beta {num(rel.get("beta60"), 2)}，BTC 解释 {num((rel.get("r2_60") or 0) * 100)}% 的日波动；距上次一年新高 {rel.get("days_since", "–")} 天（{sn(rel.get("dd_pct"), 0)}%）</div>{sline}</div>')
    return txt, (p, pos, ratio, rstate)


SIG = [("flush", "杠杆出清"), ("shorts", "空头拥挤"), ("us", "美国买盘"), ("spot", "现货买盘"), ("etf", "ETF 流入"), ("reclaim", "站回周均价")]
SIGC = {2: "#1a8f5a", 1: "#e0a100", 0: "#d03b3b", None: "#b7bec9"}


def s_signals(D, coins=("XRP", "ZEC", "ETH", "BTC")):
    rows = []
    for a in coins:
        g = ((D.get("coins") or {}).get(a) or {}).get("sig")
        if not g:
            continue
        stage = {"setup": "信号在形成", "confirmed": "已确认"}.get(g["stage"], "未出现")
        dots = " ".join(f'<span style="white-space:nowrap"><i class="dot" style="background:{SIGC[g["status"].get(k)]}"></i>{n}</span>' for k, n in SIG)
        rows.append(f'<div class="p"><b>{a}</b> {g["greens"]}/{g["available"]} 绿 · {stage}<br><span class="m">{dots}</span></div>')
    return ('<div class="c"><h3>反弹信号灯</h3>' + "".join(rows)
            + '<div class="m">绿 = 满足，黄 = 接近，红 = 不满足，灰 = 暂无数据。"信号在形成" = 资金面先转好、价格还没涨回来。仅为观察清单，未经回测。</div></div>')


# ---------------------------------------------------------------------------- dad's version (holdings-focused)
sys.path.insert(0, str(REPO))
from mdh import settings as S   # noqa: E402

COINS_DIR = REPO / "data" / "dashboard" / "coins"
NY_TZ = "America/New_York"
KEY_RELEASES = {10: "美国 CPI（通胀）", 50: "美国非农就业", 53: "美国 GDP", 54: "美国 PCE（美联储最看重的通胀）",
                46: "美国 PPI", 9: "美国零售销售"}


def load_1d(a):
    f = COINS_DIR / f"{a}.json"
    if not f.exists():
        return None
    F = json.loads(f.read_text())["tf"]["1d"]
    return {k: v for k, v in F.items() if isinstance(v, list)}   # live: includes today's forming candle


def lev_read(p7, oi7, fund):
    if not ok(p7) or not ok(oi7):
        return ""
    if oi7 > 5 and p7 > 0:
        r = "价格涨、杠杆也在加：追多的人多了"
    elif oi7 > 5:
        r = "价格跌、杠杆在加：多半是新开的空单"
    elif oi7 < -5 and p7 > 0:
        r = "价格涨、杠杆在减：空头在平仓"
    elif oi7 < -5:
        r = "价格跌、杠杆在减：多头在离场"
    else:
        r = "杠杆变化不大"
    if ok(fund):
        r += "；费率" + ("偏高，多头拥挤" if fund > 2 else "为负，空头拥挤" if fund < 0 else "正常")
    return r


def ma_table(F, lp):
    """One line: how far the live price is above (green) / below (red) each SMA; wraps cleanly at large font."""
    mas = [(n, (F.get(f"sma{n}") or [None])[-1]) for n in (20, 30, 50, 100)]
    mas = [(n, v) for n, v in mas if ok(v)]
    if not mas or not ok(lp):
        return ""
    return ('<br><span class="m">现价比均线：</span>' + " ".join(f'<span class="nw">{n}日 {span((lp / v - 1) * 100, 1, "%")}</span>' for n, v in mas))


def checkup(D, a):
    c = (D.get("coins") or {}).get(a)
    F = load_1d(a)
    if not c or not F:
        return None, []
    d1, dv = c["tf"]["1d"], c.get("deriv") or {}
    f = dv.get("funding") or {}
    fund = f.get("avg7_bp8")
    cl_ = F["c"]; last = cl_[-1]
    hi30 = max(x for x in F["h"][-30:] if ok(x)); lo30 = min(x for x in F["l"][-30:] if ok(x))
    above = [k for k, v in d1["above"].items() if v]; n_ma = len([v for v in d1["above"].values() if v is not None])
    trend = "多头排列（站上全部均线）" if len(above) == n_ma else "空头排列（跌破全部均线）" if not above else f"站上 {len(above)}/{n_ma} 条均线"
    rsi = d1.get("rsi")
    rs = "超买" if ok(rsi) and rsi >= 70 else "超卖" if ok(rsi) and rsi <= 30 else "正常"
    rel = c.get("rel") or {}
    vs = (f'30日比 BTC {"强" if (rel.get("chg30_pct") or 0) > 0 else "弱"} {abs(rel.get("chg30_pct") or 0):.0f}%') if rel else ""
    C = D.get("coins") or {}
    lp, l24, l7 = live(C, a, "px"), live(C, a, "c24"), live(C, a, "c7")
    line = (f'<div class="l"><b>{a}</b> <b>{px(lp)}</b><br>24小时 {span(l24, 1, "%")}　7天 {span(l7, 1, "%")}'
            + ma_table(F, lp) + f'<br><span class="m">趋势：{trend} · RSI {num(rsi)}（{rs}）· {vs} · 距30日高点 {sn((lp / max(hi30, lp) - 1) * 100, 0)}%'
            + (f' · 杠杆：{lev_read(l7, dv.get("oi_chg7_pct"), fund)}' if dv else "") + "</span></div>")
    # alerts (last closed day)
    al = []
    ch = live(D.get("coins") or {}, a, "c24")
    if ok(ch) and abs(ch) >= 8:
        al.append(f'{a} 24小时{"大涨" if ch > 0 else "大跌"} {sn(ch, 1)}%')
    v = [x for x in F["v"][-21:-1] if ok(x)]
    if v and ok(F["v"][-1]) and F["v"][-1] >= 3 * (sum(v) / len(v)):      # today's volume so far already 3x a normal full day
        al.append(f'{a} 今天成交量已是 20 日均量的 {F["v"][-1] / (sum(v) / len(v)):.1f} 倍')
    if ok(fund) and (fund > 3 or fund < -1):
        al.append(f'{a} 资金费率{"很高" if fund > 3 else "为负"}（7日均 {sn(fund, 2)} bp/8h），{"多头过度拥挤" if fund > 3 else "空头拥挤，容易被轧空"}')
    oi = F.get("oi") or []
    if len(oi) >= 2 and ok(oi[-1]) and ok(oi[-2]) and oi[-2] > 0 and abs(oi[-1] / oi[-2] - 1) >= 0.15:
        al.append(f'{a} 合约持仓单日{"暴增" if oi[-1] > oi[-2] else "骤降"} {sn((oi[-1] / oi[-2] - 1) * 100, 0)}%')
    for m in ("sma50", "sma100"):
        s_ = F.get(m) or []
        if len(s_) >= 2 and all(ok(x) for x in (s_[-1], s_[-2], cl_[-2])):
            if cl_[-2] < s_[-2] and last >= s_[-1]:
                al.append(f'{a} 重新站上 {m[3:]} 日均线')
            elif cl_[-2] >= s_[-2] and last < s_[-1]:
                al.append(f'{a} 跌破 {m[3:]} 日均线')
    p7 = live(D.get("coins") or {}, a, "c7")
    if ok(p7) and p7 <= -15:
        al.append(f'{a} 一周跌了 {sn(p7, 0)}%')
    if last >= max(x for x in cl_[-30:] if ok(x)):
        al.append(f'{a} 创 30 日新高')
    elif last <= min(x for x in cl_[-30:] if ok(x)):
        al.append(f'{a} 创 30 日新低')
    return line, al


def s_holdings(D):
    lines, alerts = [], []
    for a in S.PUSH_HOLDINGS:
        l, al = checkup(D, a)
        if l:
            lines.append(l); alerts += al
    body = (f'<div class="c"><h3>持仓体检（{" · ".join(S.PUSH_HOLDINGS)}）<span class="m"> 截至{LOC} {LIVE_ASOF[0] or PX_ASOF[0]}</span></h3>' + "".join(lines)
            + f'<div class="m">全部为截至{LOC} {LIVE_ASOF[0] or PX_ASOF[0]} 的实时数据：涨跌为滚动 24 小时 / 7 天，RSI 和均线包含今天还没收盘的日K。均线用 20/30/50/100 日简单均线，"现价比均线"为正（绿）表示价格在均线上方；RSI 高于 70 为超买、低于 30 为超卖；"比 BTC 强/弱"看的是相对 BTC 的涨跌。</div></div>')
    al = (f'<div class="c"><h3>异动提醒<span class="m"> 截至{LOC} {LIVE_ASOF[0] or PX_ASOF[0]}</span></h3>' + ("".join(f'<div class="p">⚠️ {x}</div>' for x in alerts) if alerts else '<div class="p">今天持仓没有异动。</div>')
          + '<div class="m">触发条件：24 小时涨跌超 8%、成交量超 20 日均量 3 倍、费率极端、合约持仓单日变化超 15%、站上/跌破 50 或 100 日均线、一周跌超 15%、创 30 日新高/新低。</div></div>')
    return body, al, alerts


def s_sectors(D):
    C = D.get("coins") or {}
    def r1(a):
        return live(C, a, "c24")
    def r7(a):
        return live(C, a, "c7")
    rows = []
    for name, coins in S.PUSH_SECTORS.items():
        have = [a for a in coins if a in C]
        if not have:
            continue
        a1 = [r1(a) for a in have if ok(r1(a))]; a7 = [r7(a) for a in have if ok(r7(a))]
        best = max(have, key=lambda a: r7(a) if ok(r7(a)) else -1e9)
        m7 = sum(a7) / len(a7) if a7 else None; m1 = sum(a1) / len(a1) if a1 else None
        rows.append((m7, f'<div class="l"><b>{name}</b>　7天 {span(m7, 1, "%")}　<span class="m">24小时 {sn(m1, 1)}%</span><br>'
                         f'<span class="m">{" ".join(have)} · 领涨 {best} {sn(r7(best), 0)}%</span></div>'))
    rows.sort(key=lambda x: -(x[0] if ok(x[0]) else -1e9))
    b1, b7 = r1("BTC"), r7("BTC")
    return (f'<div class="c"><h3>板块轮动（按 7 天排序）<br><span class="m">截至{LOC} {LIVE_ASOF[0] or PX_ASOF[0]}</span></h3>'
            + "".join(r for _, r in rows) + f'<div class="l"><b>BTC 对照</b>　7天 {span(b7, 1, "%")}　<span class="m">24小时 {sn(b1, 1)}%</span></div>'
            + '<div class="m">板块涨跌为等权平均；小字是板块里的币和 7 天涨得最多的那个。</div></div>')


def s_zec(D, R):
    r = R.get("ZEC")
    if not r or r.get("error"):
        return ""
    w = r["win"]; s = r["series"]
    zeros = 0
    for c in reversed(s["coins"]):
        if abs(c or 0) > 1e-9:
            break
        zeros += 1
    C = D.get("coins") or {}
    priv = " · ".join(f'{a} {span(live(C, a, "c7"), 1, "%")}' for a in ("ZEC", "DASH", "ZEN") if a in C)
    sig = ((C.get("ZEC") or {}).get("sig") or {}).get("values") or {}
    prem = sig.get("cb_prem_24h_bp") if ok(sig.get("cb_prem_24h_bp")) else sig.get("cb_prem_bp")
    return ('<div class="c"><h3>ZEC 专项</h3>'
            f'<div class="p">灰度 ZCSH（纽约 {md(r["date"])} 交易日）：当日 {cspan(w["day"]["coins"], 0)} 枚，近 7 个交易日 {cspan(w["d7"]["coins"], 0)} 枚，总持仓 {cn(r["hold"], 0, False)} 枚'
            + (f'，<b>已连续 {zeros} 个交易日零流入</b>' if zeros >= 2 else "") + '</div>'
            f'<div class="p">隐私板块 7 天：{priv}</div>'
            f'<div class="p">美国买盘（Coinbase 溢价，近 24 小时）：{span(prem, 1, " bp") if ok(prem) else "数据积累中"}</div>'
            '<div class="m">9 月 ZEC 上涨的三个发动机：ETF 新钱、美国现货买盘、隐私板块联动。三者重新转强，才更可能再创新高。</div></div>')


def s_funds_lite(D):
    m = D["macro"]
    sd, st, st7 = lastv(m["date"], m["stables_b"], 7)
    fd, fg, fg7 = lastv(m["date"], m["fg"], 7)
    fgl = "极度恐慌" if fg <= 24 else "恐慌" if fg <= 44 else "中性" if fg <= 55 else "贪婪" if fg <= 74 else "极度贪婪"
    return ('<div class="c"><h3>资金面</h3>'
            f'<div class="p">稳定币总量 <b>${st:,.1f}B</b>（{md(sd)}），一周 {span(st - st7 if ok(st7) else None, 1, "B")}：稳定币是场内"待用的子弹"，增加说明有新钱进场。</div>'
            f'<div class="p">恐慌贪婪指数 <b>{fg:.0f}</b>（{md(fd)}，{fgl}，一周前 {num(fg7)}）：过度贪婪时容易回调，极度恐慌时往往是低位。</div></div>'), fg, fgl


def calendar(D, days=7):
    now = datetime.now(timezone.utc).date()
    ev = []
    for d in S.FOMC_DECISIONS:
        dd = datetime.strptime(d, "%Y-%m-%d").date()
        if 0 <= (dd - now).days <= days:
            ev.append((dd, f"美联储议息决议（美东 14:00，{et_local(dd, 14)}）"))
    nxt = next((d for d in S.FOMC_DECISIONS if datetime.strptime(d, "%Y-%m-%d").date() >= now), None)
    key = os.environ.get("FRED_API_KEY", "")
    if key:
        try:
            j = requests.get("https://api.stlouisfed.org/fred/releases/dates", timeout=20, params={
                "api_key": key, "file_type": "json", "realtime_start": now.isoformat(),
                "realtime_end": (now + timedelta(days=days)).isoformat(), "include_release_dates_with_no_data": "true", "limit": 1000}).json()
            seen = set()
            for x in j.get("release_dates", []):
                rid = int(x["release_id"])
                if rid in KEY_RELEASES and (rid, x["date"]) not in seen:
                    seen.add((rid, x["date"]))
                    ev.append((datetime.strptime(x["date"], "%Y-%m-%d").date(), KEY_RELEASES[rid] + f"（通常美东 8:30，{et_local(datetime.strptime(x['date'], '%Y-%m-%d').date(), 8, 30)}）"))
        except Exception:
            pass
    ev.sort()
    wk = "一二三四五六日"
    rows = "".join(f'<div class="p"><b>{d.month}/{d.day}（周{wk[d.weekday()]}）</b> {t}</div>' for d, t in ev)
    return ('<div class="c"><h3>未来 7 天大事</h3>' + (rows or '<div class="p">未来 7 天没有重要的美国数据或议息会议。</div>')
            + (f'<div class="m">下一次美联储议息：{nxt}。代币解锁日历暂未接入。</div>' if nxt else "") + '</div>'), ev


def s_signals_dad(D):
    rows = []
    for a in S.PUSH_HOLDINGS:
        c = (D.get("coins") or {}).get(a) or {}
        g = c.get("sig"); p7 = live(D.get("coins") or {}, a, "c7")
        if not g or not ((ok(p7) and p7 < 0) or g["stage"] != "none"):
            continue
        stage = {"setup": "信号在形成", "confirmed": "已确认"}.get(g["stage"], "未出现")
        dots = " ".join(f'<span style="white-space:nowrap"><i class="dot" style="background:{SIGC[g["status"].get(k)]}"></i>{n}</span>' for k, n in SIG)
        rows.append(f'<div class="p"><b>{a}</b>（7天 {sn(p7, 1)}%）{g["greens"]}/{g["available"]} 绿 · {stage}<br><span class="m">{dots}</span></div>')
    if not rows:
        return ""
    return ('<div class="c"><h3>反弹信号灯（近 7 天下跌的持仓）</h3>' + "".join(rows)
            + '<div class="m">跌的时候看：资金面先转好（绿灯变多）、价格还没涨回来，就是"信号在形成"。绿 = 满足，黄 = 接近，红 = 不满足，灰 = 暂无数据。仅为观察清单。</div></div>')


def summary(D, R, fg, fgl, alerts, events):
    parts = []
    e, b = R.get("ETH"), R.get("BTC")
    if e and b:
        def one(r):
            return f'{r["asset"]} {sn(r["win"]["day"]["coins"], nd(r["asset"]))} 枚'
        streak = min(e["streak"], b["streak"]) if e["streak_dir"] == b["streak_dir"] else 0
        parts.append(f'美国现货 ETF：{one(b)}、{one(e)}' + (f'，已连续 {streak} 个交易日双双净{"流入" if e["streak_dir"] > 0 else "流出"}' if streak >= 2 else "") + "。")
    C = D.get("coins") or {}
    hs = [(a, live(C, a, "c24"), live(C, a, "c7")) for a in S.PUSH_HOLDINGS if a in C]
    if hs:
        best = max(hs, key=lambda x: x[2] if ok(x[2]) else -1e9); worst = min(hs, key=lambda x: x[2] if ok(x[2]) else 1e9)
        parts.append(f'持仓近 7 天：{best[0]} 最强（{sn(best[2], 1)}%），{worst[0]} 最弱（{sn(worst[2], 1)}%）；恐慌贪婪 {fg:.0f}（{fgl}）。')
    parts.append(f'异动：{len(alerts)} 条，见下方。' if alerts else "持仓今天没有异动。")
    if events:
        d, t = events[0]
        parts.append(f'近期大事：{d.month}/{d.day} {t.split("（")[0]}。')
    return '<div class="c sum"><h3>今日要点</h3>' + "".join(f"<div>{p}</div>" for p in parts) + "</div>"


def facts(D) -> dict:
    """Compact numbers for the AI commentary (read by the Claude scheduled task via --facts)."""
    R = D.get("etf_report") or {}
    C = D.get("coins") or {}
    now = datetime.now(BJ)
    load_live(live_syms())
    out = {"generated_at": D["generated_at"], "reader_timezone": f"{_TZ} ({LOC})", "report_date_local": now.strftime("%Y-%m-%d %H:%M"),
           "live_prices_as_of_local": LIVE_ASOF[0],
           "price_note": "everything is live as of live_prices_as_of_local: live_* = rolling 24h/7d; RSI/SMA/alerts/rebound signals include today's forming daily candle. chg_since_utc0 = change since today's UTC 00:00 candle open", "timing_note":
           "Report is dated in the reader's local time (reader_timezone). ETF dates are New York trading days; macro dates are US dates. "
           "Say 'ETF 纽约 9/28 交易日' style, avoid ambiguous words like 昨天/今天 for data dates.", "etf": {}, "holdings": {}, "alerts": [], "sectors_7d": {}, "macro": {}, "events": []}
    for a in ("BTC", "ETH", "SOL", "XRP", "ZEC", "NEAR", "HYPE"):
        r = R.get(a)
        if r and not r.get("error"):
            w = r["win"]
            out["etf"][a] = {"date": r["date"], "day_coins": w["day"]["coins"], "day_usd": w["day"]["usd"], "d7_coins": w["d7"]["coins"],
                             "d7_pct_of_holdings": w["d7"]["pct"], "mtd_coins": w["mtd"]["coins"], "streak": r["streak"] * r["streak_dir"],
                             "window_effect": r["window_effect"], "day_vs_prev_pct": r["day_vs_prev_pct"], "holdings": r["hold"]}
    for a in S.PUSH_HOLDINGS + ["BTC"]:
        c = C.get(a)
        if not c:
            continue
        d1, dv = c["tf"]["1d"], c.get("deriv") or {}
        rel, g = c.get("rel") or {}, c.get("sig") or {}
        out["holdings"][a] = {"live_price": live(C, a, "px"), "live_chg_24h": live(C, a, "c24"), "live_chg_7d": live(C, a, "c7"),
                              "chg_since_utc0": d1["chg_pct"], "chg_30d": dv.get("price_chg30_pct"),
                              "rsi": d1.get("rsi"), "above_smas": d1.get("above"), "macd": d1.get("macd_side"),
                              "vs_btc_30d_pct": rel.get("chg30_pct"), "oi_chg_7d": dv.get("oi_chg7_pct"),
                              "funding_7d_bp8": (dv.get("funding") or {}).get("avg7_bp8"),
                              "rebound_signals": {"greens": g.get("greens"), "stage": g.get("stage"), "status": g.get("status")}}
    for a in S.PUSH_HOLDINGS:
        _, al = checkup(D, a)
        out["alerts"] += al
    for name, coins in S.PUSH_SECTORS.items():
        v = [live(C, x, "c7") for x in coins]
        v = [x for x in v if ok(x)]
        out["sectors_7d"][name] = round(sum(v) / len(v), 1) if v else None
    m = D["macro"]
    for k in ("us10y", "dxy", "vix", "spx", "netliq_t", "fg", "stables_b"):
        dt, v, v5 = lastv(m["date"], m[k], 5)
        out["macro"][k] = {"date": dt, "last": v, "5_obs_ago": v5}
    _, ev = calendar(D)
    out["events"] = [f"{d.isoformat()} {t}" for d, t in ev]
    return out


def s_ai(note: str) -> str:
    paras = [p.strip() for p in note.strip().split("\n") if p.strip()]
    return ('<div class="c sum"><h3>今日点评 <span class="m">（Claude 根据当日数据撰写，仅供参考）</span></h3>'
            + "".join(f'<div class="p">{p}</div>' for p in paras) + "</div>")


def s_x(xnote: str) -> str:
    """博主怎么看: one blogger per line, 'name｜when｜what they said' (written by the Claude task after reading X)."""
    out = []
    for ln in xnote.strip().split("\n"):
        parts = [x.strip() for x in ln.split("｜")]
        if len(parts) >= 3 and parts[2]:
            # optional 4th field: Claude's own analysis / fact-check, shown in a tinted box under the view
            ana = f'<div style="background:#f3f0ff;border-radius:6px;padding:4px 7px;margin-top:3px;font-size:13px;color:#3b2f73">Claude：{"｜".join(parts[3:])}</div>' if len(parts) >= 4 and parts[3] else ""
            out.append(f'<div class="l"><b>{parts[0]}</b> <span class="m">{parts[1]}</span><br>{parts[2]}{ana}</div>')
        elif ln.strip():
            out.append(f'<div class="p">{ln.strip()}</div>')
    if not out:
        return ""
    return ('<div class="c"><h3>博主怎么看 <span class="m">（X 上的原话摘要）</span></h3>' + "".join(out)
            + '<div class="m">博主观点由 Claude 从 X 摘要，只代表博主本人；紫色框是 Claude 用数据核对后的补充。时间为北京时间。</div></div>')


def build(D, assets, note=None, xnote=None):
    R = D.get("etf_report") or {}
    main = next((R[a] for a in ("ETH", "BTC") if a in R and not R[a].get("error")), None)
    date = main["date"] if main else D["generated_at"][:10]
    R_DATE[0] = date
    PX_ASOF[0] = px_asof()
    load_live(live_syms())
    now = datetime.now(BJ)
    hold, alert, alerts = s_holdings(D)
    fundsec, fg, fgl = s_funds_lite(D)
    cal, events = calendar(D)
    etf = s_etf(R, assets).split('<div class="c"><h3>ETH 现货')[0].split('<div class="c"><h3>BTC 现货')[0]   # table only
    top = (s_ai(note) if note else summary(D, R, fg, fgl, alerts, events)) + (s_x(xnote) if xnote else "")
    body = (top + alert + hold + etf + s_zec(D, R) + s_sectors(D) + s_signals_dad(D)
            + fundsec + s_macro(D) + cal)
    title = f"市场日报 {now.month}/{now.day}｜ETF(纽约{md(date)}) " + " ".join(f'{a} {sn(R[a]["win"]["day"]["coins"], nd(a))}' for a in ("BTC", "ETH") if a in R and not R[a].get("error"))
    html = (CSS + f'<div class="r"><div class="h"><div class="e">MARKET DATA HUB</div><div class="t">每日市场日报 · {now.month}月{now.day}日</div>'
            f'<div class="s">{LOC}时间 {now:%Y-%m-%d %H:%M} 发送<br>币价：截至{LOC} {LIVE_ASOF[0] or PX_ASOF[0]} · 美国 ETF：纽约 {md(date)} 交易日</div></div>{body}'
            f'<div class="f">币价为币安现货实时价格（每小时更新）；日K 在 UTC 0 点（{LOC}时间 {datetime.now(timezone.utc).replace(hour=0, minute=0).astimezone(BJ):%H:%M}）换新一根。宏观数据日期为美东日期。ETF 按交易日记账，枚数 = 美元净流入 ÷ 当日纽约 16:00 价格。数据：SoSoValue、Grayscale、Binance、Coinbase、FRED、DefiLlama、alternative.me。仅供参考，不构成投资建议。</div></div>')
    return title, html, date


def arg(name):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv and sys.argv.index(name) + 1 < len(sys.argv) else None


def main():
    force, dry = "--force" in sys.argv, "--dry-run" in sys.argv
    D = json.loads(DATA.read_text())
    if "--facts" in sys.argv:
        print(json.dumps(facts(D), ensure_ascii=False, default=str))
        return 0
    last = STATE.read_text().strip() if STATE.exists() else ""
    note_path = arg("--ai-note")
    note = Path(note_path).read_text() if note_path else None
    xp = arg("--x-note")
    xnote = Path(xp).read_text() if xp and Path(xp).exists() else None
    assets = [a.strip() for a in os.environ.get("PUSHPLUS_ASSETS", "ETH,BTC,SOL,XRP,ZEC,NEAR,HYPE").split(",") if a.strip()]
    title, html, date = build(D, assets, note, xnote)
    while len(html) > 19500 and len(assets) > 2:     # PushPlus body limit is 20,000 characters
        assets = assets[:-1]
        title, html, date = build(D, assets, note, xnote)
    if "--status" in sys.argv:
        print(json.dumps({"latest_session": date, "last_sent": last, "new": date > last}))
        return 0
    if dry:
        PREVIEW.parent.mkdir(parents=True, exist_ok=True)
        PREVIEW.write_text('<meta charset="utf-8"><meta name="viewport" content="width=device-width">' + html)
        print(len(html), "chars |", title)
        return 0
    token = os.environ.get("PUSHPLUS_TOKEN", "").strip() if _ME else (_READER or os.environ.get("PUSHPLUS_TOKEN", "").strip())
    if _ME:
        force = True
        title = "【测试】" + title
    if not token:
        print("push_etf_report: PUSHPLUS_TOKEN not set; skipping")
        return 0
    now = datetime.now(timezone.utc)
    if not force:
        if date <= last:
            print(f"push_etf_report: nothing new (latest {date}, last sent {last or 'never'})")
            return 0
        if not note:
            # plain (rule-based) version is the fallback: the Claude scheduled task sends the AI version first;
            # if it hasn't by FALLBACK hour UTC the day after the session (default 03:00 UTC = 11:00 Beijing), send this
            fb = int(os.environ.get("PUSHPLUS_FALLBACK_UTC_HOUR", "3"))
            earliest = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1, hours=fb)
            if now < earliest:
                print(f"push_etf_report: {date} ready; waiting for the AI version until {earliest:%H:%M} UTC")
                return 0
    body = {"token": token, "title": (title + ("" if note else "")) [:100], "content": html, "template": "html"}
    to = "" if _ME else os.environ.get("PUSHPLUS_TO", "").strip()
    if to:
        body["to"] = to
    resp = requests.post("https://www.pushplus.plus/send", json=body, timeout=30)
    try:
        j = resp.json()
    except ValueError:
        j = {"code": resp.status_code}
    if j.get("code") == 200 and _ME:
        print(f"push_etf_report: TEST sent {date} ({'AI' if note else 'rule-based'}, {len(html)} chars) -> Avalon (own token); state untouched")
        return 0
    if j.get("code") == 200:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(date)
        print(f"push_etf_report: sent {date} ({'AI' if note else 'rule-based'}, {len(html)} chars) -> {'friends' if to else 'reader' if _READER else 'self'}")
        return 0
    print(f"push_etf_report: failed {j}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
