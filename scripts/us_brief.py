#!/usr/bin/env python3
"""US-stock sentiment + price brief from data/dashboard/data.json (stdlib only; runs in the device_bash VM).

    python3 scripts/us_brief.py            # summary
    python3 scripts/us_brief.py --path X   # other data.json

Sources: CNN Fear & Greed (us.cnn_fg, us.cnn_components; source cnn_feargreed) and api.nasdaq.com closes
(us.equity; source nasdaq). NOTE: macro.fg in data.json is the alternative.me CRYPTO index, not CNN.
Episode table = every time CNN's score closed < 20 after >= 10 trading days at >= 25, with S&P 500 forward
returns over 5/20/60 trading days and the max drawdown within 60 days. Descriptive, small sample.
"""
import json
import os
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT = os.path.join(HERE, "..", "data", "dashboard", "data.json")
TICKERS = ["SPY", "QQQ", "SMH", "DIA", "IWM", "GOOG", "ORCL", "CRCL", "COIN", "IBIT", "ETHA", "NVDA"]
NAMES = {"market_momentum_sp500": "动量 (S&P vs 125日线)", "stock_price_strength": "强度 (52周新高-新低)",
         "stock_price_breadth": "广度 (McClellan 成交量)", "put_call_options": "Put/Call 5日均",
         "market_volatility_vix": "VIX vs 50日均", "junk_bond_demand": "垃圾债利差",
         "safe_haven_demand": "避险 (股-债 20日)"}


def pct(a, b):
    return None if a is None or b in (None, 0) else (a / b - 1) * 100


def f(v, nd=1, sign=True):
    if v is None:
        return "  n/a"
    return f"{v:+.{nd}f}" if sign else f"{v:.{nd}f}"


def main():
    path = DEFAULT
    if "--path" in sys.argv:
        path = sys.argv[sys.argv.index("--path") + 1]
    d = json.load(open(path))
    us = d.get("us") or {}
    print(f"data.json generated {d.get('generated_at')}")

    fg = us.get("cnn_fg")
    comps = us.get("cnn_components")
    if comps and comps.get("component"):
        rows = dict(zip(comps["component"], range(len(comps["component"]))))
        i = rows.get("fear_and_greed")
        if i is not None:
            print(f"\nCNN Fear & Greed (US stocks) close {comps['date'][i]}: {comps['score'][i]} {comps['rating'][i]}"
                  f" | prev close {comps['prev_close'][i]} | 1w {comps['prev_1w'][i]} | 1m {comps['prev_1m'][i]}"
                  f" | 1y {comps['prev_1y'][i]}")
        for k, name in NAMES.items():
            j = rows.get(k)
            if j is not None:
                print(f"  {name:22} {comps['score'][j]:>5}  {comps['rating'][j]}")
    if fg and fg.get("date"):
        n = len(fg["date"])
        print("\nlast 7 sessions: date score | SPX | VIX | P/C5d | net new highs % | junk spread")
        for i in range(max(0, n - 7), n):
            print(f"  {fg['date'][i]} {round(fg['score'][i], 1):>5} | {fg['spx'][i]} | {fg['vix'][i]} | {fg['pc5d'][i]}"
                  f" | {fg['nnh_pct'][i]} | {fg['junk'][i]}")

        # episodes: close < 20 after >= 10 sessions at >= 25
        sc, spx, dt = fg["score"], fg["spx"], fg["date"]
        eps, calm = [], 0
        for i in range(n):
            if sc[i] is None:
                continue
            if sc[i] < 20 and calm >= 10:
                eps.append(i)
            calm = calm + 1 if sc[i] >= 25 else 0
        if eps:
            print("\nCNN < 20 episodes since 2021 (S&P fwd % over 5/20/60 sessions, max DD % within 60, "
                  "SPX off 52w high at signal):")
            for i in eps:
                hi = max(x for x in spx[max(0, i - 252):i + 1] if x)
                fw = [pct(spx[i + h], spx[i]) if i + h < n else None for h in (5, 20, 60)]
                win = [x for x in spx[i + 1:i + 61] if x]
                dd = pct(min(win), spx[i]) if win else None
                print(f"  {dt[i]} score {round(sc[i], 1):>4} | off high {f(pct(spx[i], hi))} | "
                      f"5d {f(fw[0])} 20d {f(fw[1])} 60d {f(fw[2])} | maxDD60 {f(dd)}")

    eq = us.get("equity")
    if eq and eq.get("date"):
        by = {}
        for dte, t, c in zip(eq["date"], eq["ticker"], eq["close"]):
            if c is not None:
                by.setdefault(t, []).append((dte, c))
        print("\nclose | 1w % | 1m % | off 52w high % (api.nasdaq.com daily closes)")
        for t in TICKERS:
            s = by.get(t)
            if not s:
                continue
            last_d, last = s[-1]
            yr = [c for dte, c in s if date.fromisoformat(dte) >= date.fromisoformat(last_d).replace(
                year=date.fromisoformat(last_d).year - 1)]
            # api.nasdaq.com closes are NOT split-adjusted (e.g. ETHA ~1:3 reverse split late Sep 2026):
            # blank any window that contains a one-day jump of >1.8x or <0.55x
            def clean(k):
                if len(s) <= k:
                    return None
                seg = [c for _, c in s[-k - 1:]]
                return None if any(b / a > 1.8 or b / a < 0.55 for a, b in zip(seg, seg[1:])) else s[-k - 1][1]
            w, m = clean(5), clean(21)
            jumps = [i for i in range(1, len(s)) if s[i][1] / s[i - 1][1] > 1.8 or s[i][1] / s[i - 1][1] < 0.55]
            if jumps:
                yr = [c for dte, c in s[jumps[-1]:] if dte in {x for x, _ in s[jumps[-1]:]}] or yr
            print(f"  {t:5} {last_d} {last:>9.2f} | {f(pct(last, w)):>6} | {f(pct(last, m)):>6} | "
                  f"{f(pct(last, max(yr))):>6}")


if __name__ == "__main__":
    main()
