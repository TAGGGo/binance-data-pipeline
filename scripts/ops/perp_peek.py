"""On-demand Binance USDT-M perp peek (2026-11-06). Cloud/VM are geo-blocked from fapi, so this runs on the Mac
through the job queue (data/state/jobq/<name>.job; env lines SYMS=ZECUSDT,BTCUSDT  INTERVAL=15m  LIMIT=24).
Writes data/outputs/perp_peek_<SYM>.csv: 15m (or INTERVAL) klines with taker delta + 5m open interest (coins, USD).
Uses the shared VPN helper (~/Projects/vpn_session.py) only if the direct call is blocked."""
import os, subprocess, time
from pathlib import Path
import pandas as pd, requests
REPO = Path(__file__).resolve().parents[2]; VPNS = REPO.parent.parent / "vpn_session.py"
SYMS = os.environ.get("SYMS", "ZECUSDT").split(","); IV = os.environ.get("INTERVAL", "15m"); LIM = int(os.environ.get("LIMIT", "24"))
LOC = os.environ.get("MDH_VPN_LOCATION", "")
if not LOC and (REPO / ".env").exists():
    for line in (REPO / ".env").read_text().splitlines():
        if line.startswith("MDH_VPN_LOCATION="): LOC = line.split("=", 1)[1].strip().strip('"')
LOC = LOC or "smart"
def pull():
    out = {}
    for s in SYMS:
        r = requests.get("https://www.binance.com/fapi/v1/klines", params=dict(symbol=s, interval=IV, limit=LIM), timeout=20)
        if r.status_code != 200:
            print("fapi", s, r.status_code, r.text[:120]); return None
        k = pd.DataFrame(r.json()).iloc[:, [0, 1, 2, 3, 4, 7, 10]]; k.columns = ["t", "o", "h", "l", "c", "qv", "tbq"]
        for col in k.columns[1:]: k[col] = k[col].astype(float)
        k["delta"] = 2 * k.tbq - k.qv; k["t"] = pd.to_datetime(k.t, unit="ms", utc=True)
        o = requests.get("https://www.binance.com/futures/data/openInterestHist", params=dict(symbol=s, period="5m", limit=min(500, LIM * 3 + 3)), timeout=20)
        if o.status_code == 200 and isinstance(o.json(), list):
            oi = pd.DataFrame(o.json()); oi["t"] = pd.to_datetime(oi.timestamp, unit="ms", utc=True)
            oi = oi.set_index("t")[["sumOpenInterest", "sumOpenInterestValue"]].astype(float)
            k = k.set_index("t").join(oi.rename(columns={"sumOpenInterest": "oi_coin", "sumOpenInterestValue": "oi_usd"}), how="left").reset_index()
        out[s] = k
    return out
res = pull()
if res is None and VPNS.exists():
    subprocess.run(["/usr/bin/python3", str(VPNS), "enter", "perp_peek", str(os.getpid()), LOC], timeout=90)
    try: res = pull()
    finally: subprocess.run(["/usr/bin/python3", str(VPNS), "leave", "perp_peek", str(os.getpid())], timeout=60)
if res is None: print("FAILED: fapi blocked"); raise SystemExit
for s, k in res.items():
    f = REPO / f"data/outputs/perp_peek_{s}.csv"; k.to_csv(f, index=False); print(s, "->", f, len(k), "rows, last", k.t.iloc[-1])
