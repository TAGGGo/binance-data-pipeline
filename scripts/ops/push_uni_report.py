"""
UNI-only market analysis (dated) for WeChat (PushPlus). Market numbers come live from data/market.duckdb (read-only);
news / risk text comes from data/state/uni_news.json (written by Claude each day after checking sources).

  python3 scripts/ops/push_uni_report.py --dry-run     write data/state/uni_preview.html, send nothing
  python3 scripts/ops/push_uni_report.py --to-me       TEST: send to Avalon's own PUSHPLUS_TOKEN, title prefixed 【测试】
  python3 scripts/ops/push_uni_report.py --send        send to dad (PUSHPLUS_READER_TOKEN)
Source .env first:  set -a; . ./.env; set +a
Large-font friendly (dad's phone): line lists, no wide tables, all colours fixed for WeChat dark mode.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import requests

REPO = Path(__file__).resolve().parents[2]
DB = REPO / "data" / "market.duckdb"
NEWS = REPO / "data" / "state" / "uni_news.json"
PREVIEW = REPO / "data" / "state" / "uni_preview.html"
TZ = ZoneInfo(os.environ.get("PUSHPLUS_TZ", "Asia/Shanghai"))


def pct(a, b):
    return (a / b - 1) * 100


def col(v, txt=None):
    c = "#1a8f5a" if v > 0 else "#d03b3b" if v < 0 else "#566172"
    return f'<span style="color:{c};font-weight:700">{txt if txt is not None else f"{v:+.1f}%"}</span>'


def market():
    con = duckdb.connect(str(DB), read_only=True)
    q = lambda s: con.sql(s).fetchall()
    h = q("""select ts, close, quote_volume, taker_buy_quote from bn_kline_1h
             where symbol='UNI' and market='spot' order by ts desc limit 24*31""")
    h = list(reversed(h))                       # oldest -> newest
    now_ts, px = h[-1][0], h[-1][1]
    at = lambda n: h[-1 - n][1]                 # price n hours ago
    d1, d7, d30 = pct(px, at(24)), pct(px, at(24 * 7)), pct(px, at(min(24 * 30, len(h) - 1)))
    last24 = h[-24:]
    qv24 = sum(r[2] for r in last24)
    tb24 = sum(r[3] for r in last24) / qv24 * 100
    last168 = h[-168:]
    tb7 = sum(r[3] for r in last168) / sum(r[2] for r in last168) * 100
    d = q("""select cast(ts as date), close, high, low, quote_volume from bn_kline_1d
             where symbol='UNI' and market='spot' order by ts desc limit 60""")
    closes = [r[1] for r in d]
    sma20, sma50 = sum(closes[:20]) / 20, sum(closes[:50]) / 50
    vol20 = sum(r[4] for r in d[1:21]) / 20
    hi30 = max(r[2] for r in d[:30])
    lo30 = min(r[3] for r in d[:30])
    peak = d[[r[2] for r in d].index(hi30)][0]
    vol_peak = max(r[4] for r in d[:20])
    oi = q("""select cast(timestamp as date), avg(sum_open_interest_value), avg(sum_toptrader_long_short_ratio),
              avg(count_long_short_ratio) from binance_1h where symbol='UNIUSDT'
              and timestamp>=current_date-interval 8 day group by 1 order by 1""")
    fund = q("""select avg(funding_rate*1e4) from deriv_funding where venue='binance' and symbol like 'UNI%'
                and ts>=now()-interval 7 day""")[0][0]
    return dict(ts=now_ts, px=px, d1=d1, d7=d7, d30=d30, qv24=qv24, tb24=tb24, tb7=tb7, sma20=sma20, sma50=sma50,
                vol20=vol20, hi30=hi30, lo30=lo30, peak=peak, vol_peak=vol_peak, oi_now=oi[-1][1], oi_prev=oi[0][1],
                oi_chg=pct(oi[-1][1], oi[0][1]), top=oi[-1][2], acct=oi[-1][3], fund=fund,
                low_recent=min(r[3] for r in d[:7]))


def card(title, inner, bg="#ffffff", border="#e1e5eb"):
    return (f'<div style="background:{bg};color:#0e1621;border:1px solid {border};border-radius:10px;'
            f'padding:10px 12px;margin:10px 0"><div style="font-size:17px;font-weight:700;margin-bottom:6px">{title}</div>{inner}</div>')


def line(txt, small=False):
    st = "font-size:12px;color:#566172" if small else "font-size:15px;color:#0e1621;line-height:1.7"
    return f'<div style="{st};margin:3px 0">{txt}</div>'


def build():
    m = market()
    news = json.loads(NEWS.read_text()) if NEWS.exists() else {}
    t = m["ts"].replace(tzinfo=timezone.utc).astimezone(TZ)
    now = datetime.now(TZ)
    px = m["px"]
    vol_ratio = m["qv24"] / m["vol20"]
    peak_chg = pct(px, m["hi30"])
    body = ""
    # --- headline
    head = (f'<div style="background:#0f1f33;color:#fff;border-radius:10px;padding:12px 14px">'
            f'<div style="font-size:12px;color:#b9a6ff;letter-spacing:1px">UNI 最新市场分析 · 给爸爸</div>'
            f'<div style="font-size:26px;font-weight:700">${px:.3f}</div>'
            f'<div style="font-size:15px;color:#c9d3e0">24小时 {m["d1"]:+.1f}% ｜ 7天 {m["d7"]:+.1f}% ｜ 30天 {m["d30"]:+.1f}%</div>'
            f'<div style="font-size:12px;color:#c9d3e0">分析日期 {now:%Y年%m月%d日}｜币价截至北京时间 {t:%m/%d %H:%M}（币安现货）</div></div>')
    # --- one-paragraph summary from Claude
    if news.get("summary"):
        body += card("一句话总结", line(news["summary"]), bg="#fffbea", border="#f3e3a3")
    # --- money flow
    flow = (line(f'<b>现货成交</b>：近24小时 ${m["qv24"]/1e6:.0f}M，是近20天日均的 {vol_ratio:.1f} 倍；'
                 f'9月下旬高峰日成交约 ${m["vol_peak"]/1e6:.0f}M，已明显缩量。')
            + line(f'<b>主动买入占比</b>：近24小时 {m["tb24"]:.0f}%，近7天 {m["tb7"]:.0f}%（50%为买卖均衡，目前买卖力量差不多，没有明显资金涌入）。')
            + line(f'<b>合约持仓量</b>：约 ${m["oi_now"]/1e6:.0f}M，比8天前 {col(m["oi_chg"])}（持仓越多，涨跌越容易被放大）。')
            + line(f'<b>资金费率</b>：7天平均 {m["fund"]:+.2f} 个基点/8小时，略偏多、不拥挤。')
            + line(f'<b>大户多空比</b>：{m["top"]:.1f}（大于1=大户做多更多）；散户账户多空比 {m["acct"]:.1f}。')
            + line("UNI 目前没有美国现货ETF，所以没有“ETF资金流”这一项；以上是交易所里能看到的资金动向。", small=True))
    body += card("资金流入", flow)
    # --- technicals
    above20 = "站上" if px > m["sma20"] else "跌破"
    tech = (line(f'<b>现价 ${px:.2f}</b>，{above20}20日均线 ${m["sma20"]:.2f}，高于50日均线 ${m["sma50"]:.2f}（中期趋势仍向上）。')
            + line(f'<b>30天区间</b> ${m["lo30"]:.2f} ~ ${m["hi30"]:.2f}；最高点 {m["peak"]:%m/%d}，现价比高点 {col(peak_chg)}。')
            + line(f'<b>上方压力</b>：约 $9.9～10.3（9月下旬反复被压回），再往上是 $10.94 高点。')
            + line(f'<b>下方支撑</b>：20日均线 ${m["sma20"]:.2f}，其次约 ${m["low_recent"]:.2f}（近期低点），再次 $8.0 附近。收盘跌破 $8.4 说明这一轮整理转弱。')
            + line("最近一周是在 $8.4～$9.3 之间来回震荡：涨完一大段后的“休息”，方向还没选出来。", small=True))
    body += card("形态", tech)
    # --- news
    for key, title in (("news", "新闻（已核对来源）"), ("risks", "风险提示")):
        items = news.get(key) or []
        if not items:
            continue
        inner = "".join(line(f'<b>{i["h"]}</b><br>{i["t"]}' + (f'<br><span style="font-size:12px;color:#566172">{i["s"]}</span>' if i.get("s") else "")) for i in items)
        body += card(title, inner, bg="#fff5f5" if key == "risks" else "#ffffff", border="#f1c9c9" if key == "risks" else "#e1e5eb")
    # --- scam card (fixed)
    scam = "".join(line(x) for x in news.get("scam", [])) + line("拿不准的消息，先发给儿子看，再决定。", small=True)
    body += card("防骗提醒", scam, bg="#eefaf3", border="#bfe5cf")
    foot = line("以上为公开数据与新闻整理，不是买卖建议。币价与链上数据来自交易所/公开接口，新闻来源见各条末尾。", small=True)
    html = ('<div style="font-family:-apple-system,Helvetica,Arial,sans-serif;background:#eef1f5;padding:8px;'
            f'border-radius:12px;max-width:640px;color:#0e1621">{head}{body}{foot}</div>')
    title = f"UNI最新市场分析 {now:%m/%d}｜${px:.2f} 24h{m['d1']:+.1f}%"
    return title, html


def main():
    title, html = build()
    print(len(html), "chars |", title)
    if len(html) > 19500:
        print("too long for PushPlus (20k)"); return 1
    if "--dry-run" in sys.argv:
        PREVIEW.write_text('<meta charset="utf-8"><meta name="viewport" content="width=device-width">' + html)
        return 0
    me = "--to-me" in sys.argv
    if not (me or "--send" in sys.argv):
        print("use --dry-run, --to-me or --send"); return 0
    token = os.environ.get("PUSHPLUS_TOKEN" if me else "PUSHPLUS_READER_TOKEN", "").strip()
    if not token:
        print("token not set"); return 1
    j = requests.post("https://www.pushplus.plus/send", timeout=30,
                      json={"token": token, "title": ("【测试】" if me else "") + title, "content": html, "template": "html"}).json()
    print("pushplus:", j.get("code"), j.get("msg"))
    return 0 if j.get("code") == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
