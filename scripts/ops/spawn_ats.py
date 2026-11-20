"""Run scripts/chain/ats_v1.py synchronously in /bin/bash's session (keeping Full Disk Access to /Volumes/chain).
ats_v1.py takes a lock and exits in seconds when already caught up. Called from run_update.sh."""
import os, subprocess, sys
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
logf = os.path.join(REPO, "data", "logs", "ats_v1.log")
env = dict(os.environ, ATS_V1_BUDGET=os.environ.get("ATS_SPAWN_BUDGET", "86000"))
with open(logf, "a") as lf:
    p = subprocess.Popen([sys.executable, os.path.join(REPO, "scripts", "chain", "ats_v1.py")], cwd=REPO, env=env,
                         stdout=lf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    rc = p.wait()
print(f"ats_v1: finished pid {p.pid} (rc={rc}) -> data/logs/ats_v1.log")
