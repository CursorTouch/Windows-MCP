# Secure Desktop / UAC support

Per [issue #236](https://github.com/CursorTouch/Windows-MCP/issues/236), an
optional service mode lets an LLM agent **see and click UAC consent
dialogs** that fire on the Winlogon (Secure Desktop). Without this, every
elevation interrupt halts the agent.

The service ships in two pieces. Both must be installed for the full
"detect *and* dismiss" flow to work end-to-end:

1. **`WindowsMCPHost`** — a LocalSystem Windows service. Detects when the
   input desktop flips to Winlogon (UAC fired), enforces the consent
   policy persisted in the registry, and brokers UIA / click requests to
   the user-session worker over a named pipe.
2. **`windows-mcp-uia-worker.exe`** — an Authenticode-signed,
   UIAccess-enabled binary that runs *inside* the active console user's
   session, walks consent.exe's UIA tree, and returns it to the host
   service.

Why two pieces? Two Windows boundaries get in the way of "just walk the
tree from the service":

* **Session 0 isolation**: a service in session 0 cannot enumerate UIA
  elements owned by user-session processes, even after `SetThreadDesktop`
  to Winlogon. The host service polls the input desktop name (which
  *does* cross the boundary) and dispatches via `CreateProcessAsUser`
  into the user's session.
* **UIAccess + integrity levels**: consent.exe runs at *System*
  integrity. A user-session process — even running with the user's
  elevated linked admin token (high integrity) — is denied UI
  enumeration of higher-integrity processes unless its application
  manifest declares `uiAccess="true"` **and** the binary is
  Authenticode-signed **and** it was launched from a trusted path
  (`%ProgramFiles%` or `%WinDir%`). The worker is built and shipped
  with all three.

If you install the host service *without* the signed worker, the service
falls back to a plain `python -m windows_mcp.service.user_session_worker`
spawn. That fallback works for `WaitForUACPrompt`'s detection half
(`fired=True`, `desktop="Winlogon"`, `policy=…`), but the UIA tree it
returns will be empty — UIAccess denies cross-integrity enumeration to
unsigned binaries.

## Building the signed worker

1. Build the unsigned `.exe`:

   ```
   uv pip install pyinstaller
   uv run pyinstaller packaging/uia_worker.spec --clean
   ```

   The result is `dist/windows-mcp-uia-worker.exe` with the
   `packaging/uia_worker.manifest` embedded
   (`uiAccess="true"` / `requireAdministrator`).

2. Sign it with an Authenticode code-signing certificate. EV is preferred
   but not required. **Do not check the cert into git** — keep it in your
   release pipeline's secret store.

   ```
   signtool sign /fd SHA256 /a /tr http://timestamp.digicert.com /td SHA256 ^
       dist\windows-mcp-uia-worker.exe
   ```

3. (Optional) verify:

   ```
   signtool verify /pa dist\windows-mcp-uia-worker.exe
   ```

If you skip step 2 (or sign with a self-signed cert that the target
machine doesn't trust), Windows will refuse to grant UIAccess at launch
time — the worker will run, but UIA against consent.exe will silently
return nothing.

## Installing on the target machine

Run the install command **elevated**, pointing at the signed binary:

```
uv run windows-mcp service secure-desktop install ^
    --policy block ^
    --uia-worker C:\path\to\windows-mcp-uia-worker.exe
```

What the install does:

* Registers the LocalSystem `WindowsMCPHost` service to auto-start at
  boot.
* Copies the signed worker into `%ProgramFiles%\WindowsMCP\` and locks
  the directory ACL to `BUILTIN\Administrators` + `NT AUTHORITY\SYSTEM`
  (so a non-admin user cannot replace the worker and trick the service
  into running attacker-supplied UIA code as themselves).
* Records the installed path under
  `HKLM\SOFTWARE\Windows-MCP\SecureDesktop\UiaWorkerPath` so the host
  service knows to spawn the signed binary instead of the unsigned
  fallback.
* Persists the consent policy (`block` / `allow_with_match` /
  `allow_all`) under the same registry key.

## Uninstalling

```
uv run windows-mcp service secure-desktop uninstall
```

Stops the service, removes the SCM registration, deletes the registry
key, and removes the worker binary from `%ProgramFiles%\WindowsMCP\`.

## Threat model and "why not just disable UAC"

Setting `EnableLUA=0` or `ConsentPromptBehaviorAdmin=0` would also
"solve" the problem in a trivial sense — every elevation just succeeds
silently. We don't do this because:

1. UAC then no longer protects against *any* process the user didn't
   start themselves. The agent's elevation handling has to be a
   per-prompt decision, not a global "always yes".
2. `EnableLUA=0` disables file/registry virtualization and breaks
   AppContainer / Modern apps in the same session, including Microsoft
   Store apps.
3. The agent loses any audit trail of *what* it just authorized — no
   publisher string, no app name, no opportunity to refuse a specific
   prompt under `policy=allow_with_match`.

Keeping UAC at its strictest setting and giving the agent eyes into the
prompt via this two-process pattern is the same approach Microsoft's
own accessibility tools use (Magnifier, Narrator) — UIAccess is the
documented, supported mechanism for cross-integrity UI access.
