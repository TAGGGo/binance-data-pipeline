"""STANDARD Q6 v1.0 (2026-10-20): 资金面读数 (ETF flows, Binance spot CVD, Coinbase taker CVD, Coinbase premium) and HOW UNUSUAL each reading is.
Spec: Project doc claude/std/Q6-flow-readings.md. Change rules only by bumping VERSION here and in the spec (with the reason).
Why it exists: on 10/20 Claude improvised percentiles in chat and stated two false superlatives
("ETH 被机构卖得比以往都重", "BTC 现货买盘是近几年最强的一档"). This script fixes the definitions and
prints the ONLY wording Claude may use about how unusual a reading is.

Definitions (v1.0):
  windows   : 1 / 5 / 20. ETF = US trading sessions present in v_etf_flows. Crypto = closed UTC days (today's open candle dropped).
  ETF       : net_inflow_usd summed over the window, $M. Called "ETF 资金" (never "机构").
  Binance   : spot taker delta = 2*taker_buy_quote - quote_volume (bn_kline_1d, market='spot'), $M and % of quote volume.
              Binance only; called "Binance 现货". Median alt = median over Binance spot alts (ex BTC + stables) of the % figure.
  Coinbase  : cb_taker_15m delta_usd (BTC, ETH; history since 2026-10-01).
  Premium   : v_coinbase_premium_1d premium_bp, averaged over the window.
  Ranking   : PRIMARY = among the rolling values of the last 365 days (overlapping windows; ETF: last 252 sessions).
              SECONDARY = among all history. Ranks are printed only when >= 90 comparable values exist.
  Wording   : from the 365-day rank only: rank 1 -> "365天内最高/最低"; top/bottom 5% -> "365天内前5%高/低";
              top/bottom 20% -> "偏高/偏低"; else "正常范围". "历史最高/最低" only when all-history rank = 1.
              Any other superlative is not allowed.
Usage: python3 scripts/std/q6_flow.py [COIN ...] [--no-log]   (default coins: BTC ETH)
Appends one line to data/outputs/std_answers.csv unless --no-log."""
import duckdb, os, sys, datetime as dt
import pandas as pd

VERSION = "Q6 v1.0"; WINS = (1, 5, 20); MIN_N = 90
HERE = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
args = [a for a in sys.argv[1:] if not a.startswith("--")]; COINS = [a.upper() for a in args] or ["BTC", "ETH"]
c = duckdb.connect(os.path.join(REPO, "data/market.duckdb"), read_only=True)
STABLE = {"USDC", "FDUSD", "TUSD", "USDP", "BUSD", "DAI", "USD1", "EUR", "USDE", "PYUSD"}
today = dt.datetime.utcnow().date()
lines, log = [], []

def D(x): return str(x)[:10]

def word(r365, n365, hi_is_top, rall):
    if r365 is None: return "历史不足,不排名"
    pct = r365 / n365  # r365 = 1 means most extreme in the chosen direction
    side = "高" if hi_is_top else "低"
    if r365 == 1: w = f"365天内最{side}"
    elif pct <= 0.05: w = f"365天内前5%{side}"
    elif pct <= 0.20: w = f"偏{side}"
    else: return "正常范围"
    if rall == 1: w += f"(也是历史最{side})"
    return w

def rank(series, recent_n):
    """series: rolling values indexed by date, last = current. Returns text, wording, raw."""
    s = series.dropna(); cur = s.iloc[-1]
    rec = s.iloc[-recent_n:]
    if len(rec) < MIN_N: return f"历史仅{len(s)}个值", "历史不足,不排名", None
    hi_top = cur >= rec.median()
    r365 = int((rec > cur).sum() + 1) if hi_top else int((rec < cur).sum() + 1)
    rall = int((s > cur).sum() + 1) if hi_top else int((s < cur).sum() + 1)
    side = "高" if hi_top else "低"
    mx, mn = rec.idxmax(), rec.idxmin()
    txt = (f"近365天第{r365}{side}/{len(rec)} | 全史第{rall}{side}/{len(s)} | "
           f"365天区间 {rec.min():+,.1f}({D(mn)}) ~ {rec.max():+,.1f}({D(mx)})")
    return txt, word(r365, len(rec), hi_top, rall), (r365, len(rec), rall, len(s))

def emit(label, series, recent_n, unit, logkey=None):
    s = series.dropna()
    if s.empty: return
    cur = s.iloc[-1]; txt, w, _ = rank(series, recent_n)
    lines.append(f"  {label}: {cur:+,.1f}{unit}  [{w}]  {txt}")
    if logkey: log.append(f"{logkey} {cur:+,.0f}{unit.strip()} {w}")

# ---- ETF
etf = c.sql("select date, asset, sum(net_inflow_usd)/1e6 m from v_etf_flows group by 1,2").df()
etf_last = etf.date.max()
lines.append(f"{VERSION}  run {dt.datetime.utcnow():%Y-%m-%d %H:%M} UTC\n\n[1] ETF 资金 ($M, 按美国交易日; 最新交易日 {D(etf_last)})")
for a in ["BTC", "ETH", "SOL", "XRP", "SUI", "ZEC", "NEAR", "HYPE"]:
    s = etf[etf.asset == a].set_index("date").m.sort_index()
    if s.empty: continue
    lines.append(f" {a} (最新 {D(s.index[-1])}, {len(s)} 个交易日)")
    for w in WINS:
        emit(f"{w}日合计", s.rolling(w, min_periods=w).sum(), 252, "M", f"{a}etf{w}" if a in ("BTC", "ETH") and w in (1, 20) else None)

# ---- Binance spot CVD
k = c.sql(f"""select symbol s, ts::date d, quote_volume qv, 2*taker_buy_quote-quote_volume dl
             from bn_kline_1d where market='spot' and ts::date < date '{today}'""").df()
qv = k.pivot_table(index="d", columns="s", values="qv"); dl = k.pivot_table(index="d", columns="s", values="dl")
lines.append(f"\n[2] Binance 现货主动买卖差 (收盘的 UTC 日; 最新 {D(dl.index.max())})")
alts = [x for x in dl.columns if x != "BTC" and x not in STABLE]
for w in WINS:
    sd = dl.rolling(w, min_periods=w).sum(); sq = qv.rolling(w, min_periods=w).sum(); pc = sd / sq * 100
    for coin in COINS:
        if coin not in dl.columns: continue
        emit(f"{coin} {w}日 $", sd[coin] / 1e6, 365, "M", f"{coin}bn{w}$" if w == 5 else None)
        emit(f"{coin} {w}日 占成交", pc[coin], 365, "%", f"{coin}bn{w}%" if w == 5 else None)
    emit(f"中位山寨 {w}日 占成交", pc[alts].median(axis=1), 365, "%", "altbn5%" if w == 5 else None)

# ---- Coinbase taker
cb = c.sql(f"select symbol, ts::date d, sum(delta_usd)/1e6 m from cb_taker_15m where ts::date < date '{today}' group by 1,2").df()
if not cb.empty:
    lines.append(f"\n[3] Coinbase 现货主动买卖差 ($M, 收盘的 UTC 日; 数据从 {D(cb.d.min())} 起)")
    for coin in [x for x in COINS if x in set(cb.symbol)]:
        s = cb[cb.symbol == coin].set_index("d").m.sort_index()
        for w in WINS: emit(f"{coin} {w}日", s.rolling(w, min_periods=w).sum(), 365, "M")

# ---- Coinbase premium
pr = c.sql(f"select symbol, date d, premium_bp from v_coinbase_premium_1d where date < date '{today}'").df()
lines.append(f"\n[4] Coinbase 溢价 (bp, 日收盘价算; 窗口平均)")
for coin in [x for x in COINS if x in set(pr.symbol)]:
    s = pr[pr.symbol == coin].set_index("d").premium_bp.sort_index()
    for w in WINS: emit(f"{coin} {w}日均", s.rolling(w, min_periods=w).mean(), 365, "bp", f"{coin}prem5" if w == 5 else None)

lines.append("\n措辞规则: 只能用方括号里的词描述'多不多/罕不罕见'。365天内=重叠滚动窗口。ETF 只说'ETF 资金', 不说'机构'; Binance 只说'Binance 现货'。")
print("\n".join(lines))
if "--no-log" not in sys.argv:
    with open(os.path.join(REPO, "data/outputs/std_answers.csv"), "a") as f:
        f.write(f'{dt.datetime.utcnow():%Y-%m-%d %H:%M},{VERSION},{D(etf_last)},-,"{"; ".join(log)}"\n')
