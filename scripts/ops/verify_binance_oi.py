"""One-off check (2026-11-06): do TradingView's BINANCE:<t>USD.P_OI (coin-margined) and <t>USDC.P_OI values match
Binance's own API? tv_deriv_extra.py stores BINANCE_CM as contracts x face ($100 BTC / $10 others) and BINANCE_USDC as
coins; the cloud and the device VM cannot reach Binance (geo-blocked), so this runs on the Mac via the job queue.
Uses the shared VPN helper (~/Projects/vpn_session.py, same as run_update.sh) only if the direct call is blocked.
Writes data/outputs/binance_oi_check.json and prints a table. Run: queue data/state/jobq/verify_binance_oi.job
"""
import json, os, subprocess, sys, time
from pathlib import Path
import requests, duckdb

REPO = Path(__file__).resolve().parents[2]
VPNS = REPO.parent.parent / "vpn_session.py"
OUT = REPO / "data/outputs/binance_oi_check.json"
COINS = ["BTC", "ETH", "XRP", "SOL"]

def get(url, **p):
    r = requests.get(url, params=p, timeout=20)
    return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text[:200])

def fetch():
    res = {}
    code, info = get("https://dapi.binance.com/dapi/v1/exchangeInfo")
    if code != 200: return None, f"dapi exchangeInfo {code}: {str(info)[:120]}"
    size = {s["symbol"]: s.get("contractSize") for s in info.get("symbols", [])}
    for c in COINS:
        sym = f"{c}USD_PERP"
        code, j = get("https://dapi.binance.com/dapi/v1/openInterest", symbol=sym)
        cm = float(j["openInterest"]) if code == 200 else None
        code2, j2 = get("https://fapi.binance.com/fapi/v1/openInterest", symbol=f"{c}USDC")
        usdc = float(j2["openInterest"]) if code2 == 200 else None
        code3, j3 = get("https://api.bybit.com/v5/market/open-interest", category="inverse", symbol=f"{c}USD", intervalTime="5min", limit=1)
        byinv = float(j3["result"]["list"][0]["openInterest"]) if code3 == 200 and isinstance(j3, dict) and j3.get("result", {}).get("list") else None
        code4, j4 = get("https://api.bybit.com/v5/market/open-interest", category="linear", symbol=f"{c}PERP", intervalTime="5min", limit=1)
        byusdc = float(j4["result"]["list"][0]["openInterest"]) if code4 == 200 and isinstance(j4, dict) and j4.get("result", {}).get("list") else None
        res[c] = {"bybit_inv": byinv, "bybit_usdc": byusdc, "cm_contracts": cm, "cm_contract_size_usd": size.get(sym), "usdc_coins": usdc, "ts": int(time.time())}
    return res, None

def main():
    env = dict(os.environ)
    envf = REPO / ".env"
    if envf.exists():
        for line in envf.read_text().splitlines():
            if line.startswith("MDH_VPN_LOCATION="): env["MDH_VPN_LOCATION"] = line.split("=", 1)[1].strip().strip('"')
    res, err = fetch()
    used_vpn = False
    if res is None and VPNS.exists():
        loc = env.get("MDH_VPN_LOCATION", "smart")
        subprocess.run(["/usr/bin/python3", str(VPNS), "enter", "verify_oi", str(os.getpid()), loc], timeout=90)
        used_vpn = True
        try:
            res, err = fetch()
        finally:
            subprocess.run(["/usr/bin/python3", str(VPNS), "leave", "verify_oi", str(os.getpid())], timeout=60)
    if res is None:
        print("FAILED:", err); OUT.write_text(json.dumps({"error": err})); return
    con = duckdb.connect(str(REPO / "data/market.duckdb"), read_only=True)
    rows = []
    for c, v in res.items():
        tv = dict(con.execute("""select venue, arg_max(oi_raw, ts) from tv_oi where symbol=? and interval='1h'
                                 and venue in ('BINANCE_CM','BINANCE_USDC','BYBIT_INV','BYBIT_USDC') group by 1""", [c]).fetchall())
        rows.append({"coin": c, **v, "tv_cm": tv.get("BINANCE_CM"), "tv_usdc": tv.get("BINANCE_USDC"), "tv_bybit_inv": tv.get("BYBIT_INV"), "tv_bybit_usdc": tv.get("BYBIT_USDC"),
                     "cm_ratio_tv_over_api": (tv.get("BINANCE_CM") / v["cm_contracts"]) if tv.get("BINANCE_CM") and v["cm_contracts"] else None,
                     "usdc_ratio_tv_over_api": (tv.get("BINANCE_USDC") / v["usdc_coins"]) if tv.get("BINANCE_USDC") and v["usdc_coins"] else None})
    OUT.write_text(json.dumps({"used_vpn": used_vpn, "rows": rows}, indent=1))
    for r in rows: print(r)

if __name__ == "__main__":
    main()
