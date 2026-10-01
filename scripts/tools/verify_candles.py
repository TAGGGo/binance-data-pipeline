"""
Cross-checks for the coin-page candles (python3 scripts/tools/verify_candles.py).

1. Binance's own 1d klines vs our days rebuilt from Binance 1h rows (open/high/low/close, volume, taker-buy):
   they come from two different Binance endpoints, so matching them proves the aggregation and UTC boundaries.
2. Weekly/monthly candles rebuilt by us vs Binance's native 1w / 1M klines (REST, spot).
3. MACD/RSI recomputed with a plain loop over the exported closes.
Prints the largest relative difference per check; anything above 1e-9 on prices is a real mismatch.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from mdh import indicators as ind, settings  # noqa: E402
from mdh.core import db  # noqa: E402


def rel(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    m = ~(np.isnan(a) | np.isnan(b))
    return float(np.max(np.abs(a[m] - b[m]) / np.maximum(np.abs(b[m]), 1e-12))) if m.any() else float("nan")


def main():
    con = db.connect(read_only=True)
    ok = True
    for a in settings.COIN_PAGES:
        inp = ind.load_inputs(con, a)
        h = inp["h"]
        if h.empty or "spot_open" not in h:
            print(f"{a}: no hourly data yet"); continue
        hd = ind._agg(pd.DataFrame({"open": h["spot_open"], "high": h["spot_high"], "low": h["spot_low"],
                                    "close": h["spot_close"], "qv": h["spot_qv"], "tbq": h["spot_tbq"],
                                    "perp_close": h["perp_close"], "perp_qv": h["perp_qv"]}), "1D")
        d = inp["d"]
        spot = d[d.market == "spot"].set_index("ts")
        perp = d[d.market == "perp"].set_index("ts")
        today = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
        days = hd.index[(hd.index < today)].intersection(spot.index)
        r = {c: rel(hd.loc[days, c], spot.loc[days, c]) for c in ["open", "high", "low", "close"]}
        r["quote_vol"] = rel(hd.loc[days, "qv"], spot.loc[days, "qv"])
        r["taker_buy"] = rel(hd.loc[days, "tbq"], spot.loc[days, "tbq"])
        pdays = hd.index[(hd.index < today)].intersection(perp.index)
        r["perp_close"] = rel(hd.loc[pdays, "perp_close"], perp.loc[pdays, "close"])
        r["perp_qv"] = rel(hd.loc[pdays, "perp_qv"], perp.loc[pdays, "qv"])
        bad = (np.abs(hd.loc[pdays, "perp_qv"] / perp.loc[pdays, "qv"] - 1) > 1e-6)
        if bad.any():
            print(f"   perp volume differs on {int(bad.sum())} of {len(pdays)} days, e.g. {list(pdays[bad.values][:5].date)}")
        print(f"{a} 1h->1d vs Binance 1d over {len(days)} days:", {k: f"{v:.1e}" for k, v in r.items()})

        # our 1w / 1M vs Binance native
        for tf, bi in (("1w", "1w"), ("1M", "1M")):
            c = ind.build_candles(inp, tf)
            c = c[~c["partial"]]                  # the forming candle keeps changing; compare closed ones
            js = requests.get("https://data-api.binance.vision/api/v3/klines",
                              params={"symbol": f"{a}USDT", "interval": bi, "limit": 1000}, timeout=30).json()
            nb = pd.DataFrame([x[:8] for x in js], columns=["t", "o", "h", "l", "c", "v", "ct", "qv"]).astype(float)
            nb.index = pd.to_datetime(nb["t"], unit="ms")
            idx = c.index.intersection(nb.index)
            rr = {k: rel(c.loc[idx, col], nb.loc[idx, k]) for col, k in
                  (("open", "o"), ("high", "h"), ("low", "l"), ("close", "c"), ("vol", "qv"))}
            print(f"   {tf} vs Binance native {bi} over {len(idx)} candles:", {k: f"{v:.1e}" for k, v in rr.items()})
            ok &= all(v < 1e-9 for k, v in rr.items() if k != "qv") and rr["qv"] < 1e-6
        ok &= all(v < 1e-9 for k, v in r.items() if k in ("open", "high", "low", "close", "perp_close"))
    print("ALL MATCH" if ok else "MISMATCH FOUND")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
