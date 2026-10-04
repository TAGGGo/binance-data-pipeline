"""Compact order-flow + options readout for chat (keeps Claude's token use small).

  python3 scripts/flow_brief.py [BTC|ETH] [hours=24] [step_hours=1]

Rows are in Pacific time. Deltas = taker buy minus taker sell, $M:
  bn_spot / bn_perp from binance_1h (spot_/futures_taker_buy_quote_volume), cb from cb_taker_15m.
Then one options block from deribit_opt_summary / deribit_opt_expiry (now vs ~24h ago).
"""
import sys
from pathlib import Path

import duckdb
import pandas as pd

sym = (sys.argv[1] if len(sys.argv) > 1 else "BTC").upper()
hours = int(sys.argv[2]) if len(sys.argv) > 2 else 24
step = int(sys.argv[3]) if len(sys.argv) > 3 else 1
DB = Path(__file__).resolve().parents[1] / "data" / "market.duckdb"
con = duckdb.connect(str(DB), read_only=True)
tz = "America/Los_Angeles"


def has(t):
    return con.execute("select count(*) from information_schema.tables where table_name=?", [t]).fetchone()[0] > 0


bn = con.execute(f"""
  select timestamp ts, spot_close px,
         (2*spot_taker_buy_quote_volume - spot_quote_volume)/1e6 bn_spot,
         (2*futures_taker_buy_quote_volume - futures_quote_volume)/1e6 bn_perp
  from binance_1h where symbol = ? and timestamp >= now()::timestamp - interval {hours + 1} hour
  order by ts""", [f"{sym}USDT"]).df().set_index("ts")
if has("cb_taker_15m"):
    cb = con.execute(f"""select date_trunc('hour', ts) ts, sum(delta_usd)/1e6 cb from cb_taker_15m
      where symbol = ? and ts >= now()::timestamp - interval {hours + 1} hour group by 1""", [sym]).df().set_index("ts")
    bn = bn.join(cb, how="outer")
df = bn.sort_index().tail(hours)
if step > 1:
    df = df.resample(f"{step}h").agg({"px": "last", **{c: "sum" for c in df.columns if c != "px"}})
df.index = df.index.tz_localize("UTC").tz_convert(tz).strftime("%m/%d %H:%M")
flows = [c for c in df.columns if c != "px"]
tot = df[flows].sum()
print(f"{sym} taker delta $M, PT, {step}h rows (perp row blank = Binance futures not fetched yet)")
print(df.round({"px": 0, **{c: 1 for c in flows}}).to_string(na_rep="-"))
print("sum", "  ".join(f"{c} {tot[c]:+.0f}" for c in flows))

if has("deribit_opt_summary"):
    s = con.execute("select * from deribit_opt_summary where currency=? order by ts", [sym]).df()
    if len(s):
        now = s.iloc[-1]
        ago = s[s.ts <= now.ts - pd.Timedelta(hours=23)]
        prev = ago.iloc[-1] if len(ago) else None

        def chg(col, fmt="{:+.1f}"):
            return "" if prev is None or pd.isna(prev[col]) else " (" + fmt.format(now[col] - prev[col]) + " vs 24h)"

        t = pd.Timestamp(now.ts).tz_localize("UTC").tz_convert(tz).strftime("%m/%d %H:%M PT")
        print(f"\nDeribit {sym} options @ {t}: DVOL {now.dvol:.1f}{chg('dvol')} | 30d ATM IV {now.atm_iv_30d:.1f} "
              f"| 30d 25d RR {now.rr25_30d:+.1f}{chg('rr25_30d')} | P/C OI {now.pc_oi:.2f}, P/C vol24 {now.pc_vol24:.2f} "
              f"| OI ${now.oi_usd/1e9:.1f}B{chg('oi_usd', '{:+.2e}')}")
        m = con.execute("""select * from deribit_opt_expiry where currency=? and ts=? and expiry=?""",
                        [sym, now.ts, now.main_expiry]).df().iloc[0]
        print(f"main expiry {pd.Timestamp(m.expiry):%m/%d} ({m.days:.0f}d, ${m.oi_usd/1e9:.1f}B): max pain {m.max_pain:,.0f}, "
              f"biggest call {m.top_call_strike:,.0f} ({m.top_call_oi:,.0f}), biggest put {m.top_put_strike:,.0f} ({m.top_put_oi:,.0f}); "
              f"front {pd.Timestamp(now.front_expiry):%m/%d} max pain {now.front_max_pain:,.0f}")
