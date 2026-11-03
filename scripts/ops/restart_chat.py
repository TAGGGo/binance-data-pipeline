"""Restart the crypto-chat server (launchd KeepAlive job) so src/*.js changes load. Via jobq."""
import subprocess, os
r = subprocess.run(["bash", "-c", 'launchctl kickstart -k "gui/$(id -u)/com.crypto-chat" && sleep 4 && launchctl print "gui/$(id -u)/com.crypto-chat" | grep -E "state|pid" | head -3'], capture_output=True, text=True)
print(r.returncode, r.stdout, r.stderr, flush=True)
