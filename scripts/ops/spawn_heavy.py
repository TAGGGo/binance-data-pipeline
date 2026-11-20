"""Run scripts/ops/heavy_jobs.sh synchronously in /bin/bash's session (so it keeps /bin/bash's Full Disk Access to
/Volumes/chain and never triggers macOS sandboxd TCC deadlocks after run_update.sh exits). Log: data/logs/heavy.log."""
import os, subprocess, sys
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
lock = os.path.join(REPO, "data", "state", "heavy.pid"); logf = os.path.join(REPO, "data", "logs", "heavy.log")
try:
    pid = int(open(lock).read().strip()); os.kill(pid, 0)
    print(f"heavy: previous run still going (pid {pid}); not starting another"); sys.exit(0)
except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
    pass
with open(logf, "a") as lf:
    p = subprocess.Popen(["/bin/bash", os.path.join(REPO, "scripts", "ops", "heavy_jobs.sh")], cwd=REPO,
                         stdout=lf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    open(lock, "w").write(str(p.pid))
    rc = p.wait()
print(f"heavy: finished pid {p.pid} (rc={rc}) -> data/logs/heavy.log")
