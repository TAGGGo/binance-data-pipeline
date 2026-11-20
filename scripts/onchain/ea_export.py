"""Board export for the entity-adjusted cards (11-02): latest + ~30-day-earlier snapshots of ea_urpd (unmerged, Glassnode
rules) and the exchange entity balances -> data/onchain/self/ea_cards.json. Run after ea_urpd."""
import os, glob, json, datetime as dt, numpy as np, pandas as pd
CH = os.environ.get("MDH_CHAIN_DIR", "/Volumes/chain/datahub/chain"); W = f"{CH}/ea_urpd"
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
snaps = sorted(f for f in glob.glob(f"{W}/snap_n_2*.npz"))        # unmerged run _n (11-02: the board pipeline)
day = lambda f: os.path.basename(f)[7:17]
last = snaps[-1]; want = (dt.date.fromisoformat(day(last)) - dt.timedelta(days=30)).isoformat()
prev = max([f for f in snaps if day(f) <= want] or [snaps[0]], key=day)
z, z0 = np.load(last), np.load(prev)
out = {"asof": day(last), "prev": day(prev), "lo": z["edges"][:-1].tolist()}
for k in ("ex", "ex_cb", "sth_ex", "sth_excb"):
    out[k] = np.round(z[k], 1).tolist(); out[k + "_prev"] = np.round(z0[k], 1).tolist()
xf = next((f for f in (f"{REPO}/data/onchain/self/ea_exch_daily_n.csv", f"{REPO}/data/onchain/self/ea_exch_daily_m.csv", f"{REPO}/data/onchain/self/ea_exch_daily.csv") if os.path.exists(f)), None)
if xf:
    x = pd.read_csv(xf, index_col=0)
    grp = [c for c in x.columns if c != "coinbase_pool"]
    x = x[x.index >= "2023-01-01"]
    out["exch"] = {"dates": x.index.tolist(), "total": x[grp].sum(axis=1).round(0).tolist(), "cb_pool": x["coinbase_pool"].round(0).tolist(),
                   "last": {g: round(float(x[g].iloc[-1])) for g in grp}, "src": os.path.basename(xf)}
json.dump(out, open(f"{REPO}/data/onchain/self/ea_cards.json", "w"))
print("ea_export:", out["asof"], "prev", out["prev"], "exch" in out)
