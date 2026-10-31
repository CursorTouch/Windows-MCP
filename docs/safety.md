# Safety layer

Windows-MCP drives the real desktop. The safety layer (`src/windows_mcp/safety/`) adds
guard rails that are **all enabled by default**. Every tool call goes through the
`SafetyGate` FastMCP middleware, which is registered before the desktop-control gate, so a
refused call never runs and never takes the desktop lease.

Order of checks for each call:

1. **Kill switch** (flag file or hotkey) -> refuse everything.
2. **Pending confirmation** -> refuse every other call while a dialog is open (so the agent
   cannot click "Yes" itself).
3. **Policy**: PowerShell policy, app/process allowlist.
4. **Local confirmation** for risky actions (native Windows Yes/No dialog, default **No**,
   timeout = deny).
5. Optional **screenshot**, run the tool, write the **audit** line.

Refusals come back as tool errors with a JSON body such as
`{"code": "safety_denied", "reason": "..."}` (`safety_denied`, `safety_declined`,
`safety_killswitch`).

All keys live in `~/.windows-mcp/config.toml` (or `--config`). Unknown keys and wrong types
are rejected at start-up, so a typo cannot silently fall back to a weaker setting.
Changes need a server restart.

## Full example with defaults

```toml
[server]
host = "localhost"          # default bind
require_auth = true         # HTTP/SSE refuse to start without auth_key or OAuth
allow_remote_bind = false   # HTTP/SSE refuse non-loopback hosts (0.0.0.0, LAN IPs)
# auth_key = "..."          # create with: windows-mcp auth

[safety]
enabled = true              # master switch for everything below

[safety.apps]
enforce = true
# Process image names, paths or Start Menu names. Matched by basename, case-insensitive,
# with ".exe" optional: "notepad", "Notepad.exe" and "C:\\Windows\\notepad.exe" are equal.
allowed = ["notepad", "calc", "calculator", "calculatorapp"]

[safety.powershell]
mode = "disabled"           # "disabled" | "allowlist" | "unrestricted"
allowed_commands = ["Get-ChildItem", "Get-Content", "Get-Date", "Get-Item",
                    "Get-Location", "Get-Process", "Get-Service", "Test-Path"]
max_length = 512

[safety.confirm]
enabled = true
timeout_seconds = 30
risky_shortcuts = ["alt+f4", "ctrl+w", "ctrl+q", "ctrl+shift+w", "delete", "shift+delete", "ctrl+d"]
installer_patterns = ["msiexec", "*.msi", "*setup*", "*install*", "winget", "choco", "appinstaller"]
elevation_processes = ["consent", "runas"]
risky_cmdlet_verbs = ["Remove", "Stop", "Uninstall", "Install", "Clear", "Restart", "Set", "Format", "Disable"]

[safety.audit]
enabled = true
path = "~/.windows-mcp/audit.jsonl"
screenshot = false
screenshot_dir = "~/.windows-mcp/audit-screenshots"
redact_keys = ["text", "content", "value", "command"]
max_value_length = 200

[safety.kill_switch]
enabled = true
flag_file = "~/.windows-mcp/KILL"
hotkey = "ctrl+alt+shift+f12"
```

## 1. App and process allowlist (`[safety.apps]`)

| Tool | What is checked |
|---|---|
| `Click`, `Scroll`, `Move`, `MultiSelect`, `MultiEdit` with coordinates | process of the top-level window under **each** point (`WindowFromPoint`); drags check both ends |
| `Type`, `Shortcut`, any call by `label`, calls without coordinates | process of the **foreground** window |
| `App` `launch` | the executable the fuzzy Start Menu match resolves to (exe path, `.lnk` target, or the packaged app's manifest `Executable`); unresolvable = refuse. After the start, the process from the returned pid is checked again |
| `App` `launch_executable` | the resolved file: a path as given, a bare name (`notepad.exe`) through the absolute `PATH` entries only, never the current directory; not found = refuse |
| `App` `switch` / `resize` | processes of every visible window whose title contains `name` (none found = refuse) |
| `Process` `kill` | the process of `pid`, or `name` |

The requested name is not what counts: on an Italian Windows Notepad is listed as
"Blocco note", so `name = "notepad"` fuzzy-matches Notepad++ and is refused unless
`notepad++` is allowed, while `name = "blocco note"` is allowed by `notepad`.

If the started process (the pid from `Start-Process -PassThru`) is not allowed, that one
process is terminated and the call fails with the reason; nothing else is ever killed.
Packaged (Store) apps start through `shell:AppsFolder` without a pid, so for them only the
pre-launch check applies.

Unknown process, unparsable coordinates or a lookup error = refuse. The windows-mcp process
itself (which owns the confirmation dialogs) is always refused, even with `enforce = false`.

Not covered: read-only capture tools (`Screenshot`, `Snapshot`, `Scrape`, ...) and
background tools (`FileSystem`, `Registry`) are not tied to an app; the destructive modes of
the latter go through confirmation instead. Use `[tools] exclude` to remove them entirely.

## 2. PowerShell (`[safety.powershell]`)

* `disabled` (default): every call is refused. The tool stays listed; add it to
  `[tools] exclude = ["PowerShell"]` to hide it.
* `allowlist`: exactly one command whose first word is in `allowed_commands`
  (case-insensitive). Refused: `; | & && ` + "`" + ` $ ( ) { } [ ] < > @ % ' "`, newlines, `::`,
  `..`, `--%`, `-EncodedCommand`/`-e`, `-Command`/`-c`, `Invoke-Expression`/`iex`,
  `Start-Process`, `Invoke-Command`, `Add-Type`, `New-Object`, and any argument outside
  `[A-Za-z0-9_.:\/*?,=+~-]`. Quoting is not supported, so paths with spaces cannot be used.
  Commands whose verb is in `risky_cmdlet_verbs` also need confirmation.
* `unrestricted`: upstream behaviour, but every command needs confirmation.

## 3. Local confirmation (`[safety.confirm]`)

Shown with `MessageBoxTimeoutW` (topmost, system-modal, warning icon, default button
**No**). Only an explicit **Yes** approves; No, Esc, closing it, a timeout or any error deny.
Actions that ask:

* `Process` `kill` (closing apps), and `Shortcut` in `risky_shortcuts` (Alt+F4, Ctrl+W,
  Delete...);
* `FileSystem` `delete`, `move`, and `write`/`copy` with `overwrite`;
* `Registry` `set` / `delete`;
* `App` launches matching `installer_patterns` or with `/i`, `/package`, `-Verb`, `runas`
  arguments;
* any input aimed at a process in `elevation_processes` (UAC `consent.exe`);
* PowerShell as described above.

`enabled = false` skips the dialog (the action runs) - use only for unattended tests.

## 4. Audit log (`[safety.audit]`)

One JSON object per line (UTF-8, LF), for every call including refusals:

```json
{"ts": "2026-10-31T13:10:00.123+01:00", "tool": "Type", "arguments": {"text": "<redacted 12 chars>"},
 "decision": "allowed", "reason": null, "result": "ok", "error": null, "duration_ms": 84,
 "screenshot": null}
```

Every row has the same keys; ones that do not apply are `null`.

* `decision`: `allowed`, `confirmed`, `denied`, `declined`, `killswitch`.
* `reason`: why it was refused; `null` when it ran.
* `result`: `ok` or `error` when the tool ran; `null` when it was refused.
* `error`: the tool's error text (at most 500 chars) when `result` is `error`.
* `duration_ms`: measured from the moment the gate received the call to the moment it
  refused it or the tool finished, so it includes any time spent in a confirmation dialog.
* `screenshot`: path of the PNG, or `null`. Values of `redact_keys` are replaced by
their length; other long strings are cut at `max_value_length`. With `screenshot = true` a
full-desktop PNG is saved *before* each executed action (not for refused ones). A write
failure is logged and does not stop the server. The file is never rotated.

## 5. Kill switch (`[safety.kill_switch]`)

* **Flag file**: while `flag_file` exists every call is refused. Create it to stop, delete it
  to resume, e.g. `New-Item ~/.windows-mcp/KILL` / `Remove-Item ~/.windows-mcp/KILL`.
* **Hotkey** (`RegisterHotKey`, needs at least one modifier; keys `a-z`, `0-9`, `f1-f24`,
  `pause`, `scrolllock`): latches until the server restarts. If another app owns the hotkey an
  error is logged and the flag file still works.

The switch is checked before each call and again after a confirmation dialog; it does not
abort a tool that is already running.

## 6. HTTP transports (`[server]`)

`sse` and `streamable-http` refuse to start when:

* `host` is not loopback (`localhost`, `127.0.0.1`, `::1`) and `allow_remote_bind = false`;
* there is no `auth_key` (or OAuth client) and `require_auth = true`.

`windows-mcp auth` generates a token and writes it to the config. Clients send
`Authorization: Bearer <token>`. stdio is unaffected.
