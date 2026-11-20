"""Bitcoin Core node status -> data/state/node_status.json (stdlib only; runs natively on macOS from run_update.sh).
Read-only: calls getblockchaininfo over local RPC with the cookie file. Never changes the node.
Why: Claude's device shell (Linux VM) cannot reach macOS localhost or ~/Library, so this file is how it sees sync progress."""
import base64, datetime as dt, json, os, urllib.request
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "state", "node_status.json")
DATADIR = os.environ.get("BTC_DATADIR", os.path.expanduser("~/Library/Application Support/Bitcoin"))
st = {"checked_utc": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")}
try:
    user, pw = open(os.path.join(DATADIR, ".cookie")).read().strip().split(":", 1)
    req = urllib.request.Request("http://127.0.0.1:8332/", data=json.dumps({"jsonrpc": "1.0", "id": "s", "method": "getblockchaininfo", "params": []}).encode(),
                                 headers={"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode(), "Content-Type": "application/json"})
    r = json.load(urllib.request.urlopen(req, timeout=10))["result"]
    keep = ("chain", "blocks", "headers", "verificationprogress", "initialblockdownload", "pruned", "pruneheight", "prune_target_size", "size_on_disk", "mediantime")
    st.update({k: r.get(k) for k in keep}); st["ok"] = True
    st["tip_time_utc"] = dt.datetime.utcfromtimestamp(r.get("mediantime", 0)).strftime("%Y-%m-%d %H:%M")
except FileNotFoundError:
    st.update(ok=False, error="no .cookie (Bitcoin Core not running, or server=1 missing)")
except Exception as e:
    st.update(ok=False, error=f"{type(e).__name__}: {e}"[:300])
json.dump(st, open(OUT, "w"), indent=1)
print("node:", "ok" if st.get("ok") else "FAIL", st.get("blocks"), "/", st.get("headers"), "progress", st.get("verificationprogress"), st.get("error", ""))
