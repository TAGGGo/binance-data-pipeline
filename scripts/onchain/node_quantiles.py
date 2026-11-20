"""Supply-quantile cost basis (Murphy's "供给分位数成本模型") from the node URPD -> daily csv. Added 2026-10-27.
q = cost price where cumulative supply, sorted by creation-day price, reaches q (log-interpolated in the 1% bin).
Raw UTXO method, no entity adjustment (Glassnode/Murphy's entity numbers differ by ~1k at 75-80k).
History 2017-01-01..2026-10-06: static data/onchain/cost_quantiles_hist_2017-01-01_to_2026-10-06.csv
(built once from btc-volatility/urpd/deltas.parquet). From 2026-10-07: base_urpd_end_2026-10-06.csv + node deltas.csv.
Output: data/onchain/cost_quantiles_daily.csv  d,q50,q55,q60,q65,q70,q75,supply  (complete UTC days only);
also data/onchain/urpd_daily/<day>.csv (bin,btc; 1% log bins, bin k = price [e^(0.01k), e^(0.01(k+1)))) for each node day.
KNOWN DISTORTION: 10/22 wallet reshuffle (~192k BTC moved from 62.9-64.2k bins to ~82.5k) lifts q60/q65 by
~0.5-1.5k from 10/22 on (see claude/node-urpd-pipeline.md). Runs from scripts/ops/run_update.sh after node_conc.py.
Python 3.9, stdlib only."""
import csv, math, os
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ON = os.path.join(REPO, "data", "onchain"); ND = os.path.join(ON, "node_urpd")
HIST = os.path.join(ON, "cost_quantiles_hist_2017-01-01_to_2026-10-06.csv")
OUT = os.path.join(ON, "cost_quantiles_daily.csv")
SNAP = os.path.join(ON, "urpd_daily")  # one bin,btc file per day; 2026-07-01..10-06 backfilled from btc-volatility deltas.parquet
QS = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75]
def log(*a): print("node_quantiles:", *a, flush=True)
def quantiles(u):
    ks = sorted(k for k, v in u.items() if v > 0); tot = sum(u[k] for k in ks); res = []
    for q in QS:
        tgt = tot * q; c = 0.0
        for k in ks:
            v = u[k]
            if c + v >= tgt:
                res.append(round(math.exp((k + (tgt - c) / v) * 0.01))); break
            c += v
    return res, tot
base = {}
for r in csv.DictReader(open(os.path.join(ND, "base_urpd_end_2026-10-06.csv"))):
    base[int(r["bin"])] = float(r["btc"])
seen = {}
for r in csv.DictReader(open(os.path.join(ND, "deltas.csv"))):
    if r["day"] < "2026-10-07": continue
    seen[(r["h_end"], r["day"], int(r["bin"]))] = float(r["delta"])
byday = {}
for (_, d, b), v in seen.items():
    byday.setdefault(d, {}); byday[d][b] = byday[d].get(b, 0.0) + v
rows = list(csv.DictReader(open(HIST)))
u = dict(base)
for d in sorted(byday):
    for b, v in byday[d].items(): u[b] = u.get(b, 0.0) + v
    qv, tot = quantiles(u)
    sp = os.path.join(SNAP, d + ".csv")  # daily URPD snapshot for the app's 筹码分布 card (written once per day)
    if not os.path.exists(sp):
        with open(sp, "w") as f:
            f.write("bin,btc\n"); f.writelines("%d,%.4f\n" % (b, v) for b, v in sorted(u.items()) if v > 1e-9)
    rows.append(dict(zip(["d"] + ["q%d" % int(q * 100) for q in QS] + ["supply"], [d] + qv + [round(tot, 2)])))
with open(OUT, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
log("last %s q60 %s q65 %s q75 %s (%d rows)" % (rows[-1]["d"], rows[-1]["q60"], rows[-1]["q65"], rows[-1]["q75"], len(rows)))
