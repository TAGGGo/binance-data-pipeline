"""Compact order-flow + options readout for chat (keeps Claude's token use small).

  python3 scripts/flow_brief.py [BTC|ETH] [hours=24] [step_hours=1]

Rows are in Pacific time. Deltas = taker buy minus taker sell, $M:
  bn_spot / bn_perp from binance_1h (spot_/futures_taker_buy_quote_volume), cb from cb_taker_15m.
  oi_bn / oi_okx / oi_byb = change in open interest during the row, in COINS (from oi_5m, 2026-10-08+);
  rising OI on a drop = new shorts, falling = longs closing / liquidated. 15m detail: scripts/oi5m_brief.py.
Then one options block from deribit_opt_summary / deribit_opt_expiry (now vs ~24h ago), a GEX line,
and a Hyperliquid big-account line (hl_whale_pos / hl_whale_orders; detail: scripts/hl_whales_brief.py).
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
if has("oi_5m"):
    oi = con.execute(f"""select ts, venue, contract, oi_coin c from oi_5m
          where symbol = ? and ts >= now()::timestamp - interval {hours + 2} hour""", [sym]).df()
    if len(oi):
        last_full = oi.groupby(["venue", "contract"]).ts.max().min()
        oi = oi.pivot_table(index="ts", columns=["venue", "contract"], values="c").sort_index().ffill()
        oi = oi[oi.index <= last_full].T.groupby(level=0).sum(min_count=1).T
        # OI change during each hour row = last 5m value in the hour minus last value of the previous hour
        oi = oi.resample("1h").last().diff()
        oi = oi.rename(columns={"binance": "oi_bn", "okx": "oi_okx", "bybit": "oi_byb"})
        bn = bn.join(oi[[c for c in ("oi_bn", "oi_okx", "oi_byb") if c in oi]], how="outer")
df = bn.sort_index().tail(hours)
if step > 1:
    df = df.resample(f"{step}h").agg({"px": "last", **{c: "sum" for c in df.columns if c != "px"}})
df.index = df.index.tz_localize("UTC").tz_convert(tz).strftime("%m/%d %H:%M")
flows = [c for c in df.columns if c != "px" and not c.startswith("oi_")]
tot = df[flows].sum()
print(f"{sym} taker delta $M, PT, {step}h rows (perp row blank = Binance futures not fetched yet)")
print(df.round({"px": 0, **{c: 1 for c in flows}, **{c: 0 for c in df.columns if c.startswith("oi_")}}).to_string(na_rep="-"))
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
        if "net_gex_usd" in now and pd.notna(now.get("net_gex_usd")):
            print(f"GEX (dealers long calls / short puts): net ${now.net_gex_usd/1e6:+,.0f}M per 1% move"
                  f"{chg('net_gex_usd', '{:+.2e}')}, <=7d ${now.gex_near_usd/1e6:+,.0f}M | gamma flip "
                  f"{now.gamma_flip:,.0f} | call wall {now.call_wall:,.0f} | put wall {now.put_wall:,.0f}"
                  if pd.notna(now.gamma_flip) else
                  f"GEX: net ${now.net_gex_usd/1e6:+,.0f}M per 1% | no flip within +-20% | call wall {now.call_wall} | put wall {now.put_wall}")

try:   # Coinbase premium (USDT-adjusted): nowcasts same-day US ETF flow (backtest 2024-08..2026-10: US-session
       # avg vs flow corr 0.58, sign right 74%; pre-market 11-14 UTC 65%); weak for price (fwd 24h corr 0.03)
    cp = con.execute("""select ts, premium_bp p from v_coinbase_premium_1h where symbol=? and ts >= now()::timestamp - interval 31 day
                        order by ts""", [sym]).df().set_index("ts").p.clip(-60, 60)
    if len(cp) > 100:
        z = (cp.iloc[-1] - cp.mean()) / cp.std()
        us = cp[cp.index >= cp.index[-1].normalize()].between_time("13:00", "20:59")
        t = cp.index[-1].tz_localize("UTC").tz_convert(tz).strftime("%m/%d %H:%M PT")
        print(f"Coinbase premium @ {t}: {cp.iloc[-1]:+.1f} bp (z {z:+.1f} vs 30d), last 6h avg {cp.tail(6).mean():+.1f}"
              + (f", today US-session avg {us.mean():+.1f} bp (sign ~= today's ETF flow sign 3 times in 4)" if len(us) else ""))
except Exception:  # noqa: BLE001 - view missing on a fresh DB
    pass

if has("hl_whale_pos"):
    h = con.execute("""select max(ts) t, sum(notional_usd) filter (where size>0)/1e6 l, sum(notional_usd) filter (where size<0)/1e6 s,
        count(*) n from hl_whale_pos where coin=? and ts=(select max(ts) from hl_whale_pos)""", [sym]).fetchone()
    if h and h[0] is not None:
        print(f"Hyperliquid watch-list ({h[3]} big positions): long ${h[1] or 0:,.0f}M / short ${h[2] or 0:,.0f}M -> scripts/hl_whales_brief.py {sym}")
