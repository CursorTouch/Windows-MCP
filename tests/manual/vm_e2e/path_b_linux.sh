#!/usr/bin/env bash
# Path B — Linux-side MCP client driver.
#
# Runs the same mcp_client.py against the running MCP server inside the
# Windows VM, but over streamable-http transport so the protocol travels
# Linux → host:8000 → container → VM. This validates that the MCP server
# is reachable over a real network transport (the shape Claude Desktop
# uses when the server is remote).
#
# Prerequisites (one-time host setup):
#
#   1. Container must be running with port 8000 forwarded:
#        docker run … -p 8000:8000 …
#      To re-add to a running container without losing the disk image,
#      docker stop winvm; docker rm winvm (keeps /tmp/winvm-storage);
#      then `docker run` again with the same -v /tmp/winvm-storage:/storage
#      plus -p 8000:8000.
#
#   2. Inside the container, forward container:8000 to the Windows VM's
#      IP (dockur typically assigns 20.20.20.21):
#        docker exec -d winvm sh -c 'apt-get -qq install -y socat 2>/dev/null;
#                                   socat TCP-LISTEN:8000,fork,reuseaddr TCP:20.20.20.21:8000'
#
#   3. Inside the Windows VM, the MCP server must be running:
#        windows-mcp serve --transport streamable-http --host 0.0.0.0 \
#                          --port 8000 --allow-insecure-remote
#      (run_all.ps1's path-B mode does this automatically.)

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
RESULTS="$REPO_ROOT/tests/manual/vm_e2e/results-path-b.json"
URL="${WINDOWS_MCP_URL:-http://localhost:8000/mcp/}"

echo "==> ensuring local mcp client SDK is installed"
pip install --quiet --break-system-packages mcp >/dev/null

echo "==> probing $URL"
if ! curl -sf --max-time 5 -o /dev/null "$URL" 2>/dev/null; then
    echo "    (probe returned non-200, but the MCP server might require a session; continuing)"
fi

echo "==> running mcp_client.py against $URL"
python3 "$REPO_ROOT/tests/manual/vm_e2e/mcp_client.py" \
        --results "$RESULTS" \
        --http "$URL"

echo
echo "==> path B results:"
cat "$RESULTS"
