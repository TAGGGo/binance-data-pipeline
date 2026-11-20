#!/usr/bin/env python3
"""Daily snapshot of public data on mrluanluan.com (水博乱乱's site).

Pages are Next.js server-rendered; chart data sits in the RSC payload as
{"key": ..., "points": [[ms, v, ...], ...]} (history downsampled to ~2000 pts,
recent days daily). ONLY data we don't have ourselves (Avalon 10/17: our own sources stay
primary; URPD, SOPR, cost lines, ETF flows, Deribit come from our pipeline):
  - IBIT options OI by strike/expiry + IBIT GEX map (raw RSC snapshots, 3x per US trading day on his side)
  - exchange stablecoin reserves (merged CSV)
  - his USDT "ammo" score series (API JSON)
Polite: runs at most once per UTC day (state file), 4 requests, 2 s apart.
Usage: python3 scripts/ops/fetch_mrluanluan.py [--force]
"""
import csv, datetime as dt, json, os, re, sys, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data/onchain/mrluanluan"
STATE = ROOT / "data/state/mrluanluan_state.json"
BASE = "https://mrluanluan.com"
UA = {"User-Agent": "Mozilla/5.0 (personal research; daily)"}
CHART_PAGES = {"funds/stablecoins": {"reserve_all"}}
RAW_PAGES = {"options/ibit-oi": "ibit_oi", "options/ibit-map": "ibit_gex"}


def get(path):
    req = urllib.request.Request(BASE + path, headers=UA)
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read().decode("utf-8")


def rsc(html):
    parts = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', html, re.S)
    return "".join(json.loads('"' + p + '"') for p in parts)


def series(raw):
    out = []
    for m in re.finditer(r'"key":"([^"]+)"', raw):
        seg = raw[m.start():m.start() + 4_000_000]
        p = seg.find('"points":[[')
        if p < 0 or p > 3000:
            continue
        depth = 0
        for i in range(p + 9, len(seg)):
            c = seg[i]
            if c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    break
        try:
            out.append((m.group(1), json.loads(seg[p + 9:i + 1])))
        except ValueError:
            pass
    return out


def merge_csv(path, pts):
    rows = {}
    if path.exists():
        with open(path) as f:
            for r in csv.reader(f):
                if r and r[0] != "d":
                    rows[r[0]] = r[1:]
    for p in pts:
        d = dt.datetime.utcfromtimestamp(p[0] / 1000).strftime("%Y-%m-%d")
        rows[d] = ["" if v is None else v for v in p[1:]]
    width = max((len(v) for v in rows.values()), default=1)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["d"] + ["v%d" % i if i else "value" for i in range(width)])
        for d in sorted(rows):
            w.writerow([d] + list(rows[d]))
    return len(rows)


def main():
    force = "--force" in sys.argv
    today = dt.datetime.utcnow().strftime("%Y-%m-%d")
    st = json.loads(STATE.read_text()) if STATE.exists() else {}
    if st.get("last_ok") == today and not force:
        print("mrluanluan: already fetched today")
        return
    OUT.mkdir(parents=True, exist_ok=True)
    snap = OUT / "snapshots" / today
    snap.mkdir(parents=True, exist_ok=True)
    errs, n = [], 0
    for page, keep in CHART_PAGES.items():
        try:
            for key, pts in series(rsc(get("/" + page))):
                if pts and key in keep:
                    merge_csv(OUT / ("%s__%s.csv" % (page.replace("/", "_"), key)), pts)
                    n += 1
        except Exception as e:  # keep going; report at end
            errs.append("%s: %s" % (page, e))
        time.sleep(2)
    for page, name in RAW_PAGES.items():
        try:
            (snap / (name + ".rsc.txt")).write_text(rsc(get("/" + page)))
        except Exception as e:
            errs.append("%s: %s" % (page, e))
        time.sleep(2)
    try:
        (snap / "usdt_ammo_1d.json").write_text(get("/api/funds/usdt-ammo/series?tf=1d&days=3000"))
    except Exception as e:
        errs.append("usdt-ammo: %s" % e)
    st.update(last_run=today, series=n, errors=errs)
    if n and not errs:
        st["last_ok"] = today
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, indent=1))
    print("mrluanluan: %d series merged, %d errors %s" % (n, len(errs), errs[:3]))


if __name__ == "__main__":
    main()
