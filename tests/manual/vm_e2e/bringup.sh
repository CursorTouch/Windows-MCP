#!/usr/bin/env bash
# Kick off the in-VM test suite.
#
#   bash tests/manual/vm_e2e/bringup.sh
#
# Requires the dockur/windows container to be running and an OOBE-complete
# Windows VM accessible on VNC port 5900 with samba share at \\host.lan\Data.
#
# After the script returns, read tests/manual/vm_e2e/results.json on the host
# side to see what passed/failed.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
RESULTS="$REPO_ROOT/tests/manual/vm_e2e/results.json"
LOG_DIR="$REPO_ROOT/tests/manual/vm_e2e"

vnc_send_keys() {
    local text="$1"
    # vncdotool's typetext expects spaces to be literal; doublequotes inside
    # the text need escaping. We're sending a single PowerShell one-liner so
    # let's just pass it through carefully.
    vncdotool -s 127.0.0.1::5900 typewrite "$text"
}

echo "==> waiting for Windows desktop to be available on VNC :5900"
# Heuristic: when the desktop is up, the VNC frame size jumps past ~50 KB
# (Windows desktop has more visual content than the install/OOBE pages).
for _ in $(seq 1 90); do
    out="/tmp/vnc-bringup.png"
    if vncdotool -s 127.0.0.1::5900 capture "$out" >/dev/null 2>&1; then
        sz=$(stat -c %s "$out" 2>/dev/null || echo 0)
        if [ "$sz" -gt 60000 ]; then
            echo "    desktop visible ($sz bytes)"
            break
        fi
    fi
    sleep 10
done

echo "==> opening PowerShell via Win+R"
vncdotool -s 127.0.0.1::5900 key win-r
sleep 1
vncdotool -s 127.0.0.1::5900 typewrite "powershell"
vncdotool -s 127.0.0.1::5900 key enter
sleep 3

echo "==> launching run_all.ps1 from the share"
PS_CMD='powershell -ExecutionPolicy Bypass -File \\host.lan\Data\Windows-MCP\tests\manual\vm_e2e\run_all.ps1'
vncdotool -s 127.0.0.1::5900 typewrite "$PS_CMD"
vncdotool -s 127.0.0.1::5900 key enter

echo "==> waiting for $RESULTS to appear"
rm -f "$RESULTS"
for _ in $(seq 1 60); do
    if [ -f "$RESULTS" ]; then
        echo "    results.json received"
        cat "$RESULTS"
        exit 0
    fi
    sleep 30
done

echo "ERROR: results.json never appeared after 30 minutes" >&2
echo "Check tests/manual/vm_e2e/run_all.log on the share for details" >&2
exit 1
