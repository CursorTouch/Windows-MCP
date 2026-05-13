# Windows-MCP — VM end-to-end test harness

Tests the secure-desktop host service against a real UAC prompt inside a
Windows VM. Two driver paths are supported:

## Path A — in-VM driver (default)

The MCP client runs *inside* the Windows VM and talks to the MCP server
over the stdio transport — the same shape Claude Desktop uses. Results
are written to a JSON file in the bind-mounted share so the Linux side
can read them without any port mapping.

  Linux                       Windows VM
  ─────                       ──────────
  /home/.../tests/  ◄──SMB──► \\host.lan\Data\…\tests\
  └─ vm_e2e/                  └─ run_all.ps1
       └─ results.json ◄──── writes ◄──── mcp_client.py ──stdio──► windows-mcp serve

## Path B — Linux-side driver

The MCP server inside the VM is served over streamable-http. Container
port 8000 is forwarded to the Linux host (needs container restart with
`-p 8000:8000`). The Python MCP client runs on Linux. Same assertions
but exercises HTTP transport too.

## Bring-up sequence

Run from Linux:

    bash tests/manual/vm_e2e/bringup.sh

That installs Python+uv+windows-mcp inside the VM, registers the service
with `--allow-user-binary-path` (since the VM is disposable), and runs
`run_all.ps1`. After completion, `results.json` will exist at
`tests/manual/vm_e2e/results.json` on the Linux side.

## Tests covered

1. Service install succeeds, service is RUNNING.
2. Service auto-starts after a Windows reboot (no manual start needed).
3. MCP `WaitForUACPrompt` blocks, returns a dialog after we trigger UAC.
4. Policy=`block`  → service refuses auto-click on Winlogon.
5. Policy=`allow_all` → service performs the auto-click.
6. Service uninstall removes the registry policy and the service entry.
