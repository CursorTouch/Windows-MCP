# Windows-MCP — VM end-to-end test harness

Tests the secure-desktop story (UAC handling) against a real UAC prompt
fired on a clean Windows VM, by verifying that **windows-mcp comes up on
its own after reboot** and the MCP server is reachable.

## Architecture

The harness is split into setup and test phases so the test never restarts
windows-mcp itself — windows-mcp must self-start after reboot using its
own Windows service + scheduled-task mechanisms.

```
+- setup.ps1 -------------+   one-time, elevated
| install python+uv       |
| uv sync                 |
| set UAC reg values      |
| install host service    |   SERVICE_AUTO_START → SCM brings it up
| windows-mcp install     |   ONLOGON task        → MCP server brings itself up
| register test task      |   ONLOGON, non-elev   → test.ps1 fires per boot
| shutdown /r             |
+------------+------------+
             |
             v   (reboot)
+- after reboot ----------+
| SCM auto-starts         |
|   WindowsMCPHost        |
| TaskSched fires         |
|   windows-mcp-server    |   listens on 127.0.0.1:8000
|   windows-mcp-test      |   medium-integrity, runs test.ps1
+------------+------------+
             |
             v
+- test.ps1 (every boot) -+
| verify service running  |
| wait for MCP HTTP up    |
| mcp_client.py --http    |   real MCP protocol over streamable-http
|   triggers UAC          |
|   asserts WaitForUACPrompt
|   asserts Click(Yes)    |
| results.json            |
+-------------------------+
```

## Files

| File | Run when | Privilege |
|------|----------|-----------|
| `setup.ps1` | Once per VM | Elevated (admin) |
| `test.ps1`  | Every reboot | Non-elevated (medium integrity) |
| `run_all.ps1` | Convenience dispatcher | Whichever |
| `mcp_client.py` | Called by test.ps1 | Medium integrity |
| `bin/`      | Pre-staged binaries (gitignored) | n/a |

## First-time bring-up

1. Stage host-side binaries (uv, Python installer) into `bin/` — one-time:
   ```
   curl -sL -o /tmp/u.zip https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip
   unzip -o /tmp/u.zip -d tests/manual/vm_e2e/bin/
   curl -sL -o tests/manual/vm_e2e/bin/python-install.exe https://www.python.org/ftp/python/3.13.0/python-3.13.0-amd64.exe
   ```
2. From an **elevated** PowerShell inside the VM, run:
   ```
   powershell -ExecutionPolicy Bypass -File \\host.lan\Data\Windows-MCP\tests\manual\vm_e2e\setup.ps1
   ```
3. Setup reboots. After the reboot, both windows-mcp components come up on
   their own. The `windows-mcp-test` task also fires automatically and
   runs the assertions. Read `results.json` from the share to see the result.

## Reboot survival

The architecture inherently tests reboot survival on every cycle: each
reboot validates that windows-mcp self-starts and the MCP tools work.

## Re-running

After the first setup, every subsequent reboot re-fires `test.ps1` and
overwrites `results.json`. To re-trigger without rebooting:

```
schtasks /Run /TN windows-mcp-test
```

To wipe everything and start over:
```
schtasks /Delete /TN windows-mcp-test /F
schtasks /Delete /TN windows-mcp-server /F
sc.exe delete WindowsMCPHost
# then re-run setup.ps1
```
