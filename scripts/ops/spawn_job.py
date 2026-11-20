"""Run scripts/chain/<name>.py synchronously in /bin/bash's session (keeping Full Disk Access to /Volumes/chain);
log data/logs/<name>.log. The script takes a lock and exits at once when up to date. Usage: spawn_job.py <name> <BUDGET_ENV>."""
import os, subprocess, sys
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
name, benv = sys.argv[1], sys.argv[2]
env = dict(os.environ, **{benv: os.environ.get("ATS_SPAWN_BUDGET", "86000")})
with open(os.path.join(REPO, "data", "logs", name + ".log"), "a") as lf:
    p = subprocess.Popen([sys.executable, os.path.join(REPO, "scripts", "chain", name + ".py")], cwd=REPO, env=env,
                         stdout=lf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    rc = p.wait()
print(f"{name}: finished pid {p.pid} (rc={rc}) -> data/logs/{name}.log")
