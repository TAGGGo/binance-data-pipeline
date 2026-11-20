"""Minute-level job queue for the Mac (added 2026-11-01, Avalon: "最大限度提高速度"). The hourly run_update.sh was the
only way to start Mac-side jobs, so every new job waited up to an hour. book_snapshot.py (launchd, every 60 s) calls
run_queue(): each data/state/jobq/<name>.job file is started detached and renamed to <name>.started-<UTC>.
Job file: first line = script path relative to the repo (must be under scripts/), optional KEY=VALUE env lines after it.
Log: data/logs/<name>.log. The scripts themselves hold locks, so a duplicate start just exits.
<name>.kill (empty file) stops a running scripts/chain/<name>.py (pkill -f), then is renamed <name>.killed-<UTC>."""
import os, subprocess, sys, datetime as dt
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
Q = os.path.join(REPO, "data", "state", "jobq")
def run_queue():
    if not os.path.isdir(Q): return
    for f in sorted(os.listdir(Q)):
        if f.endswith(".kill"):
            name = f[:-5]
            if name.replace("_", "").isalnum():
                subprocess.run(["pkill", "-f", f"scripts/chain/{name}.py"])
            os.replace(os.path.join(Q, f), os.path.join(Q, f"{name}.killed-{dt.datetime.utcnow():%Y%m%dT%H%M}"))
            continue
        if not f.endswith(".job"): continue
        p = os.path.join(Q, f); name = f[:-4]
        try:
            lines = [x.strip() for x in open(p) if x.strip()]
            script = os.path.normpath(os.path.join(REPO, lines[0]))
            if not script.startswith(os.path.join(REPO, "scripts") + os.sep) or not os.path.exists(script):
                os.replace(p, p + ".bad"); continue
            env = dict(os.environ)
            for x in lines[1:]:
                if "=" in x: k, v = x.split("=", 1); env[k.strip()] = v.strip()
            subprocess.Popen([sys.executable, script], cwd=REPO, env=env, stdin=subprocess.DEVNULL,
                             stdout=open(os.path.join(REPO, "data", "logs", name + ".log"), "a"), stderr=subprocess.STDOUT,
                             start_new_session=True)
            os.replace(p, os.path.join(Q, f"{name}.started-{dt.datetime.utcnow():%Y%m%dT%H%M}"))
        except Exception as e:
            print("jobq:", name, e, flush=True)
if __name__ == "__main__":
    run_queue()
