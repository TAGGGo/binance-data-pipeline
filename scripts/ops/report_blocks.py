"""
Structured numbers for 币聊 Crypto Chat's 今日日报 (the blocks of the old WeChat report, as JSON instead of HTML).

Reuses push_etf_report.py's calculations (持仓体检, 异动提醒, ZEC 专项, 反弹信号灯, 板块轮动, 资金面, 宏观, 未来 7 天大事)
and writes them to the crypto-chat exchange folder (data/state/crypto-chat/report-blocks.json). Run hourly from
run_update.sh after `mdh export`. Times are Beijing time (dad reads the report).
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ["PUSHPLUS_TZ"] = "Asia/Shanghai"
sys.path.insert(0, str(Path(__file__).resolve().parent))
import push_etf_report as P   # noqa: E402

OUT = P.REPO / "data" / "state" / "crypto-chat" / "report-blocks.json"


def r(x, d=1):
    return round(x, d) if P.ok(x) else None


def checkup(D, a):
    c = (D.get("coins") or {}).get(a)
    F = P.load_1d(a)
    if not c or not F:
        return None
    C = D["coins"]
    d1, dv = c["tf"]["1d"], c.get("deriv") or {}
    fund = (dv.get("funding") or {}).get("avg7_bp8")
    lp = P.live(C, a, "px")
    hi30 = max(x for x in F["h"][-30:] if P.ok(x))
    above = [k for k, v in d1["above"].items() if v]
    n_ma = len([v for v in d1["above"].values() if v is not None])
    trend = "站上全部均线" if len(above) == n_ma else "跌破全部均线" if not above else f"站上 {len(above)}/{n_ma} 条均线"
    rsi = d1.get("rsi")
    rel = (c.get("rel") or {}).get("chg30_pct")
    ma = {}
    for n in (20, 30, 50, 100):
        v = (F.get(f"sma{n}") or [None])[-1]
        if P.ok(v) and P.ok(lp):
            ma[str(n)] = r((lp / v - 1) * 100)
    return {"sym": a, "px": lp, "chg24": r(P.live(C, a, "c24")), "chg7": r(P.live(C, a, "c7")), "ma": ma, "trend": trend, "rsi": r(rsi, 0),
            "rsi_note": "超买" if P.ok(rsi) and rsi >= 70 else "超卖" if P.ok(rsi) and rsi <= 30 else "正常",
            "vs_btc30": r(rel, 0), "from_hi30": r((lp / max(hi30, lp) - 1) * 100, 0) if P.ok(lp) else None,
            "lev": P.lev_read(P.live(C, a, "c7"), dv.get("oi_chg7_pct"), fund) if dv else ""}


# companies whose earnings move US tech / crypto risk appetite: the Magnificent 7, the two big AI chip names, crypto stocks
WATCH = {"AAPL": "苹果", "MSFT": "微软", "GOOGL": "谷歌", "GOOG": "谷歌", "AMZN": "亚马逊", "META": "Meta", "NVDA": "英伟达",
         "TSLA": "特斯拉", "AVGO": "博通", "TSM": "台积电", "MSTR": "Strategy", "COIN": "Coinbase", "HOOD": "Robinhood"}


def earnings(days=7):
    """Upcoming earnings of WATCH companies (Nasdaq calendar). Returns events with an absolute UTC time."""
    import requests
    out = []
    today = datetime.now(P.ET).date()
    for i in range(days + 1):
        d = today + timedelta(days=i)
        if d.weekday() >= 5:
            continue
        try:
            rows = requests.get("https://api.nasdaq.com/api/calendar/earnings", params={"date": d.isoformat()},
                                headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}, timeout=20).json()["data"]["rows"] or []
        except Exception as e:
            print("report_blocks: earnings", d, e, file=sys.stderr)
            continue
        groups = {}
        for r_ in rows:
            name = WATCH.get(r_.get("symbol", ""))
            if not name:
                continue
            when = {"time-after-hours": "after", "time-pre-market": "pre"}.get(r_.get("time"), "na")
            if name not in groups.setdefault(when, []):
                groups[when].append(name)
        for when, names in groups.items():
            h, m = {"after": (16, 0), "pre": (7, 0)}.get(when, (12, 0))
            t = datetime(d.year, d.month, d.day, h, m, tzinfo=P.ET)
            if t > datetime.now(timezone.utc):
                out.append({"ts": t.astimezone(timezone.utc).isoformat(), "what": "财报：" + "、".join(names),
                            "approx": when != "na", "kind": "earnings"})
    return out


def _nasdaq_day(d):
    import requests
    try:
        return requests.get("https://api.nasdaq.com/api/calendar/earnings", params={"date": d.isoformat()},
                            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}, timeout=20).json()["data"]["rows"] or []
    except Exception as e:
        print("report_blocks: earnings", d, e, file=sys.stderr)
        return []


def _move_since_close(sym, release):
    """% change from the last regular close before `release` to the latest price (pre/after-hours included), Yahoo 5m."""
    import requests
    try:
        j = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}", params={"interval": "5m", "range": "5d", "includePrePost": "true"},
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=20).json()["chart"]["result"][0]
    except Exception as e:
        print("report_blocks: yahoo", sym, e, file=sys.stderr)
        return None, None
    ts, cl = j.get("timestamp") or [], j["indicators"]["quote"][0].get("close") or []
    rel = release.astimezone(P.ET)
    anchor = rel.replace(hour=16, minute=0, second=0, microsecond=0)
    if anchor > rel:
        anchor -= timedelta(days=1)
        while anchor.weekday() >= 5:
            anchor -= timedelta(days=1)
    a = anchor.timestamp()
    before = [c for t, c in zip(ts, cl) if c and t < a]
    after = [(t, c) for t, c in zip(ts, cl) if c and t >= release.timestamp()]
    if not before or not after:
        return None, None
    return round((after[-1][1] / before[-1] - 1) * 100, 1), datetime.fromtimestamp(after[-1][0], timezone.utc).isoformat()


def recent_earnings(hours=30):
    """WATCH companies that reported in the last `hours`: when, consensus EPS, stock move since the last close."""
    out = []
    now = datetime.now(timezone.utc)
    today = datetime.now(P.ET).date()
    for i in range(0, 4):
        d = today - timedelta(days=i)
        if d.weekday() >= 5:
            continue
        for r_ in _nasdaq_day(d):
            sym = r_.get("symbol", "")
            name = WATCH.get(sym)
            if not name or sym == "GOOG" or any(x["name"] == name for x in out):
                continue
            when = {"time-after-hours": "after", "time-pre-market": "pre"}.get(r_.get("time"), "na")
            h, m = {"after": (16, 0), "pre": (7, 0)}.get(when, (12, 0))
            rel = datetime(d.year, d.month, d.day, h, m, tzinfo=P.ET)
            if not (now - timedelta(hours=hours) <= rel <= now):
                continue
            move, move_at = _move_since_close(sym, rel)
            out.append({"sym": sym, "name": name, "ts": rel.astimezone(timezone.utc).isoformat(), "when": when,
                        "eps_forecast": (r_.get("epsForecast") or "").replace("$", "") or None, "move": move, "move_at": move_at})
    return out


def macro_events(D):
    """Fed decisions and key US data from push_etf_report's calendar, with an absolute UTC time."""
    _, ev = P.calendar(D)
    out = []
    for d, t in ev:
        m = re.search(r"美东 (\d{1,2}):(\d{2})", t)
        h, mi = (int(m.group(1)), int(m.group(2))) if m else (8, 30)
        ts = datetime(d.year, d.month, d.day, h, mi, tzinfo=P.ET)
        if ts > datetime.now(timezone.utc):
            out.append({"ts": ts.astimezone(timezone.utc).isoformat(), "what": re.sub(r"（.*", "", t), "kind": "macro"})
    return out


def zec_orderflow():
    """Analyze 24h/12h Binance ZECUSDT 5m spot orderflow, passive iceberg absorption bars, and HL perp basis."""
    import requests
    try:
        resp = requests.get(
            "https://data-api.binance.vision/api/v3/klines",
            params={"symbol": "ZECUSDT", "interval": "5m", "limit": 288},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        if resp.status_code != 200:
            print(f"report_blocks: zec_orderflow HTTP {resp.status_code}", file=sys.stderr)
            return None
        raw = resp.json()
        if not raw or len(raw) < 24:
            return None
        bars = []
        for k in raw:
            o, c, qv, tb = float(k[1]), float(k[4]), float(k[7]), float(k[10])
            net = 2.0 * tb - qv
            buy_pct = (tb / qv * 100.0) if qv > 0 else 50.0
            chg_pct = ((c / o - 1.0) * 100.0) if o > 0 else 0.0
            bars.append({"o": o, "c": c, "qv": qv, "tb": tb, "net": net, "buy_pct": buy_pct, "chg_pct": chg_pct})

        def _agg(sub):
            tot_qv = sum(b["qv"] for b in sub)
            tot_tb = sum(b["tb"] for b in sub)
            net_usd = 2.0 * tot_tb - tot_qv
            buy_p = (tot_tb / tot_qv * 100.0) if tot_qv > 0 else 50.0
            chg_p = ((sub[-1]["c"] / sub[0]["o"] - 1.0) * 100.0) if sub[0]["o"] > 0 else 0.0
            ask_cnt = sum(1 for b in sub if b["net"] >= 150_000 and b["buy_pct"] >= 60.0 and b["chg_pct"] <= 0.10)
            bid_cnt = sum(1 for b in sub if b["net"] <= -150_000 and b["buy_pct"] <= 40.0 and b["chg_pct"] >= -0.10)
            return round(net_usd / 1e6, 2), round(buy_p, 1), round(chg_p, 1), ask_cnt, bid_cnt

        net24_m, buy24_pct, chg24_pct, ask24, bid24 = _agg(bars)
        net12_m, buy12_pct, chg12_pct, ask12, bid12 = _agg(bars[-144:] if len(bars) >= 144 else bars)
        px = round(bars[-1]["c"], 2)

        hl_bn_pct = None
        try:
            hl = requests.post(
                "https://api.hyperliquid.xyz/info",
                json={"type": "l2Book", "coin": "ZEC"},
                timeout=10,
            ).json()
            lv = hl.get("levels") or []
            if len(lv) >= 2 and lv[0] and lv[1] and px > 0:
                hl_mid = (float(lv[0][0]["px"]) + float(lv[1][0]["px"])) / 2.0
                hl_bn_pct = round((hl_mid / px - 1.0) * 100.0, 3)
        except Exception as e:
            print("report_blocks: zec_orderflow hl", e, file=sys.stderr)

        if (
            ask12 >= 3
            or (ask24 >= 5 and ask24 >= bid24 * 2)
            or (buy12_pct >= 54.0 and chg12_pct <= -1.0 and net12_m >= 3.0)
            or (buy12_pct >= 53.0 and chg12_pct <= 0.0 and hl_bn_pct is not None and hl_bn_pct >= 0.04)
        ):
            status, label = "ask_iceberg", "疑似卖方冰山压盘"
        elif (
            bid12 >= 3
            or (bid24 >= 5 and bid24 >= ask24 * 2)
            or (buy12_pct <= 46.0 and chg12_pct >= 1.0 and net12_m <= -3.0)
        ):
            status, label = "bid_iceberg", "疑似买方冰山托底"
        else:
            status, label = "balanced", "无明显冰山压制"

        return {
            "status": status, "label": label, "px": px,
            "buy24_pct": buy24_pct, "net24_m": net24_m, "chg24_pct": chg24_pct,
            "buy12_pct": buy12_pct, "net12_m": net12_m, "chg12_pct": chg12_pct,
            "ask24": ask24, "bid24": bid24, "ask12": ask12, "bid12": bid12,
            "hl_bn_pct": hl_bn_pct,
        }
    except Exception as e:
        print("report_blocks: zec_orderflow", e, file=sys.stderr)
        return None


def zec_cex_inflow(px=None):
    """Fetch external >=5,000 ZEC deposits into Binance hot wallet in last 24h (excluding hot/cold internal transfers)."""
    import requests
    BN_HOT = "t1SzbuFRuPa5upV6c9ZgKb9V7fxn2b8NAKg"
    BN_COLD = "t1gsBrGZGMyDGZw2icGnMpVBuEGVWip5kH8"
    INTERNAL = {BN_HOT, BN_COLD}
    try:
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        r1 = requests.get(
            "https://api.blockchair.com/zcash/outputs",
            params={"q": f"recipient({BN_HOT}),time({since}..),value(500000000000..)", "s": "time(desc)", "limit": 50},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=12,
        )
        if r1.status_code != 200:
            print(f"report_blocks: zec_cex_inflow outputs HTTP {r1.status_code}", file=sys.stderr)
            return None
        rows = (r1.json() or {}).get("data") or []
        if not rows:
            return {"count": 0, "total_zec": 0, "max_zec": 0, "total_usd_m": 0.0, "events": []}
        tx_map = {}
        for x in rows:
            h = x.get("transaction_hash")
            if not h:
                continue
            cur = tx_map.setdefault(h, {"time": x.get("time", ""), "zec": 0.0})
            cur["zec"] += float(x.get("value") or 0) / 1e8
            if x.get("time", "") > cur["time"]:
                cur["time"] = x.get("time", "")
        hashes = list(tx_map.keys())[:20]
        external = []
        for i in range(0, len(hashes), 10):
            chunk = hashes[i:i + 10]
            r2 = requests.get(
                f"https://api.blockchair.com/zcash/dashboards/transactions/{','.join(chunk)}",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=12,
            )
            if r2.status_code != 200:
                print(f"report_blocks: zec_cex_inflow txs HTTP {r2.status_code}", file=sys.stderr)
                return None
            data = (r2.json() or {}).get("data") or {}
            for h in chunk:
                info = data.get(h) or {}
                inputs = info.get("inputs") or []
                in_addrs = {inp.get("recipient") for inp in inputs if inp.get("recipient")}
                if in_addrs & INTERNAL:
                    continue
                external.append({"time": tx_map[h]["time"], "zec": round(tx_map[h]["zec"]), "shielded": len(inputs) == 0})
        external.sort(key=lambda x: -x["zec"])
        tot = sum(x["zec"] for x in external)
        return {
            "count": len(external),
            "total_zec": tot,
            "max_zec": max((x["zec"] for x in external), default=0),
            "total_usd_m": round(tot * px / 1e6, 2) if P.ok(px) else None,
            "events": external[:5],
        }
    except Exception as e:
        print("report_blocks: zec_cex_inflow", e, file=sys.stderr)
        return None


def main():
    D = json.loads(P.DATA.read_text())
    P.load_live(P.live_syms())
    C = D.get("coins") or {}
    R = D.get("etf_report") or {}
    hold = list(P.S.PUSH_HOLDINGS)
    out = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "asof": P.LIVE_ASOF[0], "holdings": hold,
           "checkup": {}, "alerts": [], "signals": [], "sectors": [], "zec": {}, "funds": {}, "macro": [], "events": []}
    for a in hold:
        k = checkup(D, a)
        if k:
            out["checkup"][a] = k
        _, al = P.checkup(D, a)
        # a moving-average cross only counts when the price is clearly past the line (>= 1%);
        # ZEC 10/28 was flagged "跌破 50 日均线" while 0.5% under it after a +5% day
        for x in al:
            m = re.match(rf"{a} (跌破|重新站上) (\d+) 日均线", x)
            d = ((k or {}).get("ma") or {}).get(m.group(2)) if m else None
            if m and (d is None or abs(d) < 1):
                continue
            if m:
                x = f"{x}（{'低' if d < 0 else '高'}出 {abs(d):.1f}%）"
            out["alerts"].append(x)
    # 大额 ETF 资金流 (10/31): an altcoin ETF day in the top 15% of that fund's non-zero days and >= 0.5% of its holdings
    for a in ("SOL", "XRP", "ZEC", "SUI", "HYPE", "NEAR"):
        e = R.get(a) or {}
        try:
            day = (e.get("win") or {}).get("day") or {}
            usd, pct = day.get("usd") or 0, day.get("pct") or 0
            nz = sorted(abs(x) for x in (e.get("series") or {}).get("usd") or [] if x)
            if not usd or len(nz) < 8 or abs(usd) < nz[int(len(nz) * 0.85)] or abs(pct) < 0.5:
                continue
            if (datetime.now(timezone.utc).date() - datetime.fromisoformat(e["date"]).date()).days > 3:
                continue
            amt = f"{abs(usd) / 1e8:.2f} 亿" if abs(usd) >= 1e8 else f"{abs(usd) / 1e4:,.0f} 万"
            out["alerts"].append(f"{a} ETF {int(e['date'][5:7])}/{int(e['date'][8:])} 大额{'流入' if usd > 0 else '流出'} {amt}美元（占规模 {abs(pct):.1f}%）")
        except Exception:
            pass
    for a in hold:
        g = (C.get(a) or {}).get("sig")
        p7 = P.live(C, a, "c7")
        if not g or not ((P.ok(p7) and p7 < 0) or g["stage"] != "none"):
            continue
        out["signals"].append({"sym": a, "chg7": r(p7), "greens": g["greens"], "available": g["available"],
                               "stage": {"setup": "信号在形成", "confirmed": "已确认"}.get(g["stage"], "未出现"),
                               "lights": [[n, g["status"].get(k)] for k, n in P.SIG]})
    rows = []
    for name, coins in P.S.PUSH_SECTORS.items():
        have = [a for a in coins if a in C]
        if not have:
            continue
        a1 = [P.live(C, a, "c24") for a in have]; a7 = [P.live(C, a, "c7") for a in have]
        a1 = [x for x in a1 if P.ok(x)]; a7 = [x for x in a7 if P.ok(x)]
        best = max(have, key=lambda a: P.live(C, a, "c7") if P.ok(P.live(C, a, "c7")) else -1e9)
        rows.append({"name": name, "coins": have, "chg7": r(sum(a7) / len(a7)) if a7 else None,
                     "chg24": r(sum(a1) / len(a1)) if a1 else None, "best": best, "best7": r(P.live(C, best, "c7"), 0)})
    rows.sort(key=lambda x: -(x["chg7"] if x["chg7"] is not None else -1e9))
    rows.append({"name": "BTC", "coins": ["BTC"], "chg7": r(P.live(C, "BTC", "c7")), "chg24": r(P.live(C, "BTC", "c24"))})
    out["sectors"] = rows
    z = R.get("ZEC")
    if z and not z.get("error"):
        zeros = 0
        for c in reversed(z["series"]["coins"]):
            if abs(c or 0) > 1e-9:
                break
            zeros += 1
        out["zec"]["etf"] = {"date": z["date"], "day": r(z["win"]["day"]["coins"], 0), "d7": r(z["win"]["d7"]["coins"], 0),
                             "hold": r(z["hold"], 0), "zero_days": zeros}
    out["zec"]["privacy7"] = [[a, r(P.live(C, a, "c7"))] for a in ("ZEC", "DASH", "ZEN") if a in C]
    sv = ((C.get("ZEC") or {}).get("sig") or {}).get("values") or {}
    prem = sv.get("cb_prem_24h_bp") if P.ok(sv.get("cb_prem_24h_bp")) else sv.get("cb_prem_bp")
    out["zec"]["cb_prem_bp"] = r(prem)
    of = zec_orderflow()
    if of:
        out["zec"]["orderflow"] = of
    zpx = (of or {}).get("px") or P.live(C, "ZEC", "px")
    ci = zec_cex_inflow(zpx)
    if ci is not None:
        out["zec"]["cex_inflow"] = ci
    m = D["macro"]
    sd, st, st7 = P.lastv(m["date"], m["stables_b"], 7)
    fd, fg, fg7 = P.lastv(m["date"], m["fg"], 7)
    out["funds"] = {"stables_b": r(st), "stables_chg7_b": r(st - st7) if P.ok(st) and P.ok(st7) else None, "stables_date": sd,
                    "fg": r(fg, 0), "fg7": r(fg7, 0), "fg_date": fd,
                    "fg_label": ("极度恐慌" if fg <= 24 else "恐慌" if fg <= 44 else "中性" if fg <= 55 else "贪婪" if fg <= 74 else "极度贪婪") if P.ok(fg) else ""}
    for name, k, d, suf, mode in [("美债10年", "us10y", 2, "%", "bp"), ("美元指数", "dxy", 2, "", "%"), ("VIX", "vix", 1, "", "%"),
                                   ("标普500", "spx", 0, "", "%"), ("联储净流动性", "netliq_t", 2, "T", "B")]:
        dt, v, v5 = P.lastv(m["date"], m[k], 5)
        if not P.ok(v):
            continue
        ch = ((v - v5) * 100 if mode == "bp" else (v - v5) * 1000 if mode == "B" else (v / v5 - 1) * 100) if P.ok(v5) else None
        out["macro"].append({"name": name, "key": k, "value": round(v, d), "dec": d, "pre": "$" if k == "netliq_t" else "", "suf": suf,
                             "chg5": r(ch, 0 if mode != "%" else 2), "unit": " bp" if mode == "bp" else "B" if mode == "B" else "%", "date": dt})
    out["events"] = sorted(macro_events(D) + earnings(), key=lambda e: e["ts"])
    out["earnings_recent"] = recent_earnings()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    tmp.replace(OUT)
    print(f"report_blocks: {len(out['checkup'])} coins, {len(out['alerts'])} alerts, {len(out['signals'])} signals")


if __name__ == "__main__":
    main()
