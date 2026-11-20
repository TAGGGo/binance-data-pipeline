"""Arkham API (30-day trial, key in .env ARKHAM_API_KEY; key capped at 10,000 data points by Avalon 11-02).
1. check entity ids  2. per entity: big BTC transfers per month window (usd >= 100k, limit 1650) -> addresses that Arkham
attributes to that entity. Output data/research/arkham/btc_entity_addrs.csv (address, entity, entity_name, type, label).
Resumable (done windows in progress.json). Stops when remaining data points < ARK_RESERVE (default 3000)."""
import os, json, time, csv, datetime as dt, urllib.request, urllib.parse
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")); OUT = f"{REPO}/data/research/arkham"; os.makedirs(OUT, exist_ok=True)
KEY = [l.split("=", 1)[1].strip() for l in open(f"{REPO}/.env") if l.startswith("ARKHAM_API_KEY=")][-1]
RESERVE = int(os.environ.get("ARK_RESERVE", 3000)); MONTHS = int(os.environ.get("ARK_MONTHS", 24))
def log(*x): print("arkham:", *x, flush=True)
REM = [None]
def get(path, **q):
    url = "https://api.arkm.com" + path + ("?" + urllib.parse.urlencode(q) if q else "")
    for k in range(4):
        try:
            r = urllib.request.urlopen(urllib.request.Request(url, headers={"API-Key": KEY}), timeout=60)
            REM[0] = int(r.headers.get("x-intel-datapoints-remaining") or 0); return json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = e.read()[:200]
            if e.code == 429: time.sleep(5 * (k + 1)); continue
            log("HTTP", e.code, path, body); return None
        except Exception as e:
            log("err", e); time.sleep(3)
    return None
ENTS = ["coinbase", "kraken", "gemini", "upbit", "bithumb", "korbit", "coinone", "binance", "okx", "bitfinex", "robinhood", "bybit",
        "crypto-com", "bitstamp", "bitget", "kucoin", "htx", "gate-io", "mexc", "deribit", "bitflyer", "coincheck",
        "blackrock", "fidelity", "grayscale", "ark-invest", "bitwise", "vaneck", "franklin-templeton", "invesco", "wisdomtree", "valkyrie",
        "microstrategy", "tether", "us-government"]
PF = f"{OUT}/progress.json"; prog = json.load(open(PF)) if os.path.exists(PF) else {"ents": {}, "done": []}
for e in ENTS:
    if e in prog["ents"]: continue
    d = get(f"/intelligence/entity/{e}")
    prog["ents"][e] = {"ok": bool(d and d.get("id")), "name": (d or {}).get("name"), "type": (d or {}).get("type")}
    log("entity", e, prog["ents"][e], "remaining", REM[0]); json.dump(prog, open(PF, "w"))
AF = f"{OUT}/btc_entity_addrs.csv"; seen = set()
if os.path.exists(AF):
    for r in csv.DictReader(open(AF)): seen.add((r["address"], r["entity"]))
else:
    open(AF, "w").write("address,entity,entity_name,type,label\n")
today = dt.date.today().replace(day=1)
wins = [((today - dt.timedelta(days=31 * i)).replace(day=1), (today - dt.timedelta(days=31 * (i - 1))).replace(day=1) if i else dt.date.today() + dt.timedelta(days=1)) for i in range(MONTHS)]
for e, info in prog["ents"].items():
    if not info["ok"]: continue
    for a, b in wins:
        tag = f"{e}|{a}"
        if tag in prog["done"]: continue
        if REM[0] is not None and REM[0] < RESERVE: log("reserve reached", REM[0]); raise SystemExit
        d = get("/transfers", base=e, chains="bitcoin", usdGte=100000, timeGte=a.isoformat(), timeLte=b.isoformat(), limit=1650)
        if d is None: continue
        new = []
        for t in d.get("transfers", []):
            for side in ("fromAddress", "toAddress"):
                x = t.get(side) or {}; ent = (x.get("arkhamEntity") or {}); lab = (x.get("arkhamLabel") or {})
                if ent.get("id") and x.get("address") and (x["address"], ent["id"]) not in seen:
                    seen.add((x["address"], ent["id"])); new.append([x["address"], ent["id"], ent.get("name", ""), ent.get("type", ""), lab.get("name", "")])
            for side in ("fromAddresses", "toAddresses"):
                for y in t.get(side) or []:
                    x = y.get("address") or {}; ent = (x.get("arkhamEntity") or {}); lab = (x.get("arkhamLabel") or {})
                    if ent.get("id") and x.get("address") and (x["address"], ent["id"]) not in seen:
                        seen.add((x["address"], ent["id"])); new.append([x["address"], ent["id"], ent.get("name", ""), ent.get("type", ""), lab.get("name", "")])
        with open(AF, "a", newline="") as f: csv.writer(f).writerows(new)
        prog["done"].append(tag); json.dump(prog, open(PF, "w"))
        log(e, a, "transfers", len(d.get("transfers", [])), "new addrs", len(new), "remaining", REM[0])
log("done; addresses", len(seen), "remaining", REM[0])
