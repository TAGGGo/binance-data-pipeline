"""CBD heatmap data (貝格先生's 「CBD 籌碼分布熱力圖」, Glassnode Cost Basis Distribution over time). Added 2026-11-01.
Same URPD as self_urpd_metrics.py (btc-volatility deltas.parquet to 2026-10-06, node deltas.csv after). For each day the
cumulative supply per 1% log bin is spread evenly over its price range into $1,000 bins (20K..130K).
Output data/onchain/self/cbd_weekly.json: weekly columns (each Sunday + the newest day) from 2023-10-01:
  {"dates": [...], "lo": 20000, "step": 1000, "grid": [[BTC per bin, ...], ...], "price": [close...]}
Skips when the newest URPD day is already in the file (once a day is enough)."""
import csv, json, os, collections, datetime as dt
import duckdb, numpy as np
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ON = os.path.join(REPO, "data", "onchain"); OUT = os.path.join(ON, "self", "cbd_weekly.json")
HIST = os.environ.get("URPD_HIST", os.path.expanduser("~/Projects/btc-volatility/urpd/deltas.parquet"))
ND = os.path.join(ON, "node_urpd", "deltas.csv"); SW = "2026-10-07"; START = "2023-10-01"; LO, HI, STEP = 20000, 130000, 1000
seen = {}
for r in csv.DictReader(open(ND)):
    if r["day"] >= SW:
        seen[(r["h_end"], r["day"], int(r["bin"]))] = float(r["delta"])
newest = max(d for _, d, _ in seen) if seen else SW
try:
    if json.load(open(OUT))["dates"][-1] >= newest and not os.environ.get("CBD_FORCE"):
        print(f"cbd: up to date ({newest})"); raise SystemExit(0)
except (FileNotFoundError, KeyError, ValueError):
    pass
c = duckdb.connect()
rows = c.execute(f"select d::varchar, bin, sum(delta)::double from read_parquet('{HIST}') where d<'{SW}' group by 1,2").fetchall()
for (_, d, b), v in seen.items():
    rows.append((d, b, v))
byday = collections.defaultdict(list)
for d, b, v in rows:
    byday[d].append((b, v))
days = sorted(byday); bmin = min(b for _, b, _ in rows); bmax = max(b for _, b, _ in rows)
cur = np.zeros(bmax - bmin + 1); le = np.exp(np.arange(bmin, bmax + 2) * 0.01); edges = np.arange(LO, HI + STEP, STEP, dtype=float)
m = duckdb.connect(os.path.join(REPO, "data", "market.duckdb"), read_only=True)
px = {str(t)[:10]: float(p) for t, p in m.execute("select ts, close from bn_kline_1d where symbol='BTC' and market='spot'").fetchall()}
m.close()
out_d, grid, price = [], [], []
for i, d in enumerate(days):
    for b, v in byday[d]:
        cur[b - bmin] += v
    if d < START:
        continue
    if dt.date.fromisoformat(d).weekday() == 6 or i == len(days) - 1:
        cdf = np.concatenate([[0], np.cumsum(np.maximum(cur, 0))])
        out_d.append(d); grid.append([int(round(x)) for x in np.diff(np.interp(edges, le, cdf))]); price.append(round(px.get(d, 0)))
json.dump({"dates": out_d, "lo": LO, "step": STEP, "grid": grid, "price": price}, open(OUT + ".tmp", "w"), separators=(",", ":"))
os.replace(OUT + ".tmp", OUT)
print(f"cbd: {len(out_d)} weeks {out_d[0]}..{out_d[-1]}, {len(grid[0])} bins")
