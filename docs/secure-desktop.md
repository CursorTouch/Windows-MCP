# Secure Desktop / UAC support

Per [issue #236](https://github.com/CursorTouch/Windows-MCP/issues/236), an
optional service mode lets an LLM agent **see and click UAC consent
dialogs** that fire on the Winlogon (Secure Desktop). Without this, every
elevation interrupt halts the agent.

The feature has two moving parts. Both must be installed for the full
"detect *and* dismiss" flow to work end-to-end:

1. **`WindowsMCPHost`** — a LocalSystem Windows service. Detects when the
   input desktop flips to Winlogon (UAC fired), enforces the consent
   policy persisted in the registry, and dispatches UIA / click requests
   to the user-session worker over a named pipe.
2. **`windows-mcp-uia-worker.exe`** — an Authenticode-signed,
   UIAccess-enabled binary that runs *inside* the active console user's
   session, walks consent.exe's UIA tree, and returns it to the host
   service.

Why two parts? Two Windows boundaries get in the way of "just walk the
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
  (`%ProgramFiles%` or `%WinDir%`).

## Installing

```
windows-mcp service secure-desktop install
```

Run elevated. By default this prompts you:

```
The Secure-Desktop helper can also be enabled, which lets the LLM agent
SEE and CLICK Windows UAC consent dialogs...

Enabling will:
  1. Install PyInstaller into the current Python env (~25 MB, one time).
  2. Build the helper as windows-mcp-uia-worker.exe (~60-120 s).
  3. Generate a self-signed code-signing cert (this machine only).
  4. Add the cert to LocalMachine\Root and \TrustedPublisher.
  5. Sign the helper and install it to %ProgramFiles%\WindowsMCP\.

Build the signed helper now? [Y/n]:
```

* **Yes** (default): the install command runs the full
  build-then-self-sign-then-install flow. The cert it generates is local
  to your machine — it's added to your machine's trust stores but no
  other Windows install will accept signatures made with it.
* **No**: the host service installs in **detect-only** mode.
  `WaitForUACPrompt` will still fire on UAC, you'll still get the
  desktop name and the persisted policy, but the dialog's UIA tree will
  come back empty and `Click(loc=...)` against the dialog will fail —
  every elevation has to be approved or denied by hand at the keyboard.
  You can re-run `install` later to switch on the helper.

Non-interactive flags (skip the prompt):

* `--self-sign-uia-worker` — auto-Yes, no prompt.
* `--no-uia-worker` — auto-No, no prompt.
* `--uia-worker <path>` — provide a commercially-signed binary you've
  built yourself (e.g. in a release pipeline with an Authenticode EV
  cert). Skips the build / cert / sign steps entirely, just installs
  and registers your binary.

## Uninstalling

```
windows-mcp service secure-desktop uninstall
```

Stops and removes the service, clears the registry key, removes the
worker binary from `%ProgramFiles%\WindowsMCP\`, and — if you opted into
self-signing — removes the self-signed cert from `LocalMachine\My`,
`LocalMachine\Root`, and `LocalMachine\TrustedPublisher`.

## Why not just disable UAC?

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

## Why we self-sign

UIAccess is one of the very few Windows features that explicitly
requires a signature even when the user is admin. Without it, any
medium-integrity process could declare `uiAccess="true"` in its
manifest and silently bypass UAC for the user — defeating the feature
entirely.

But Windows doesn't care *whose* cert it is, only that the cert chains
to a root the local machine trusts. So a self-signed cert added only to
this machine's `LocalMachine\Root` (which is what the install flow
does) is enough to satisfy the OS, without paying for a commercial
Authenticode cert. The cert never leaves this machine, and uninstall
removes it.

If you're packaging windows-mcp for redistribution to many machines,
sign the worker once with a commercial cert at CI time and ship the
binary; users pass it via `--uia-worker <path>` and skip the self-sign
flow.

## Known limitation: Win11 Winlogon DACL

On Windows 11 (verified against Win11 24H2 build) the worker still can't
walk `consent.exe`'s UIA tree, even with everything above in place
(uiAccess=true + Authenticode-signed + Program Files +
`SetTokenInformation(TokenUIAccess=1)` on the spawn token + correctly
declared ctypes argtypes on every Win32 API we call). The block is the
Winlogon desktop DACL itself: `OpenDesktopW("Winlogon", ...)` returns
`ERROR_ACCESS_DENIED` (gle=5) from the user-session worker, and the
session-0 SYSTEM broker can't enumerate the session-1 Winlogon either
(`EnumDesktopWindows` returns FALSE with `windows_seen=0` regardless of
impersonation; Win11's kernel refuses cross-session window enumeration
even from SYSTEM). The DACL the broker *can* modify is its own session-0
Winlogon, which is a different desktop object than the one the worker
needs.

End-to-end effect: `WaitForUACPrompt` still detects UAC, returns
`fired=True` with the correct desktop name and policy, but the `tree`
field returns the user's Default-desktop windows (Taskbar, Start menu)
because the worker can't attach to Winlogon and so its `GetRootElement`
sees Default. `Click(loc=...)` against a UAC dialog therefore still
needs user input.

Microsoft's first-party accessibility tools (Magnifier, Narrator) work
around this by running as a service in session 1 with
`SeCreateGlobalPrivilege`. A future fix would either (a) ship a
session-1 SYSTEM helper that modifies session-1 Winlogon's DACL before
the worker spawn, or (b) use the Windows 11 24H2+ "Access to
notifications" RPC interface that consent.exe exposes. Both are
non-trivial.
