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
VNC="127.0.0.1::5900"

vnc() { vncdotool --delay=80 -s "$VNC" "$@"; }

echo "==> waiting for Windows desktop on VNC :5900"
# Heuristic: when the desktop is up, the VNC frame jumps past ~150 KB
# (Windows desktop background renders far more than the install splash).
for _ in $(seq 1 90); do
    if vnc capture /tmp/vnc-bringup.png >/dev/null 2>&1; then
        sz=$(stat -c %s /tmp/vnc-bringup.png 2>/dev/null || echo 0)
        if [ "$sz" -gt 150000 ]; then
            echo "    desktop visible ($sz bytes)"
            break
        fi
    fi
    sleep 10
done

echo "==> opening Start menu and searching for powershell"
# Win+R is fragile when no app has focus (it can autocomplete to other exes).
# The Start-menu search is far more deterministic: tap Win, type, Enter.
vnc key super
sleep 1
vnc type "powershell"
sleep 2          # let the search index resolve
vnc key enter
sleep 5          # PowerShell takes a beat to materialise

echo "==> launching run_all.ps1 from the share"
# Single-line PowerShell launcher. ExecutionPolicy Bypass for the child only.
PS_CMD='powershell -ExecutionPolicy Bypass -File \\host.lan\Data\Windows-MCP\tests\manual\vm_e2e\run_all.ps1'
vnc type "$PS_CMD"
sleep 1
vnc key enter

echo "==> waiting for $RESULTS to appear"
rm -f "$RESULTS"
# 60 iterations * 30 s = 30 min wait. Windows install of Python+uv inside
# the VM takes ~10 min by itself under TCG.
for _ in $(seq 1 60); do
    if [ -f "$RESULTS" ]; then
        echo "    results.json received"
        cat "$RESULTS"
        exit 0
    fi
    sleep 30
done

echo "ERROR: results.json never appeared after 30 minutes" >&2
echo "Check $REPO_ROOT/tests/manual/vm_e2e/run_all.log on the share for details" >&2
exit 1
