# Secure Desktop / UAC support

Per [issue #236](https://github.com/CursorTouch/Windows-MCP/issues/236),
an optional service mode lets an LLM agent **see and click UAC consent
dialogs** that fire when an admin elevation prompt appears. Without this,
every elevation interrupt halts the agent.

The feature has two moving parts:

1. **Registry policy.** The install command writes
   `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System`:
   * `PromptOnSecureDesktop = 0`
   * `ConsentPromptBehaviorAdmin = 4` ("Prompt for consent")

   With both values set, Win 10 / Win 11 25H2 renders the UAC dialog on
   the user's Default desktop instead of the isolated Winlogon "secure
   desktop". The `consent.exe` window becomes a regular top-level window
   reachable by plain UIAutomation — no UIAccess privilege, no cross-
   desktop tricks. See [`win11-uac-investigation.md`](win11-uac-investigation.md)
   for the eight-iteration arc that landed on CPB=4 specifically; CPB=5
   (Microsoft's modern default) is treated as "still secure desktop" by
   Win 11 25H2 even with `PromptOnSecureDesktop=0`.

2. **`WindowsMCPHost` service.** A LocalSystem Windows service that
   coordinates UAC detection, enforces the consent policy persisted in
   the registry, and spawns a user-session worker that walks the
   `consent.exe` UIA tree.

## Installing

```
windows-mcp service secure-desktop install
```

Run elevated. The command:

* registers `WindowsMCPHost` as a LocalSystem service (auto-start),
* writes the consent policy (`block` / `allow_with_match` / `allow_all`)
  to `HKLM\SOFTWARE\Windows-MCP\SecureDesktop`,
* writes the UAC registry values that route UAC to the Default desktop.

Useful flags:

* `--policy {block,allow_with_match,allow_all}` — what auto-click
  behaviour to grant the agent. Default `block` (detect-only, no
  auto-click).
* `--allow-publisher "Microsoft Corporation"` — repeat / comma-separate
  for `allow_with_match`.
* `--force` — uninstall an existing registration first.
* `--allow-user-binary-path` — skip the check that the service binary
  lives under `%ProgramFiles%` or `%WinDir%`. Disposable VMs only;
  anywhere else, a user-writable service binary is a SYSTEM-elevation
  hole.

## Uninstalling

```
windows-mcp service secure-desktop uninstall
```

Stops + removes the service, clears the policy registry key, and
restores the stock UAC policy values (`PromptOnSecureDesktop=1`,
`ConsentPromptBehaviorAdmin=5` — modern Win 11 defaults).

## Security trade-off

Moving UAC off the secure desktop weakens one specific protection: a
malicious process already running in the user's session can now draw
pixels that mimic the UAC dialog (and Win 11's secure-desktop dim) and
capture clicks. Every other UAC protection is intact:

* `EnableLUA=1` — admin token still split. Every elevation still asks.
* Consent dialog still shows publisher + executable path + Yes/No.
* User still has to click. Nothing auto-elevates.
* Malware still can't programmatically click a real UAC dialog without
  UIAccess (which needs a signed binary in a trusted path).

The alternatives — leaving UAC on the secure desktop and reaching it
some other way (UIAccess-enabled worker binary, loosening Winlogon's
DACL from a SYSTEM broker, screenshot-as-UIA fallback, `EnableLUA=0`,
CPB=5 instead of CPB=4) — were each tried and each failed on Win 11
25H2 for documented reasons. The arc lives in
[`win11-uac-investigation.md`](win11-uac-investigation.md).

If you need the secure-desktop protection back, run `uninstall`. The
agent will then see UAC fire but won't be able to inspect or dismiss
the dialog.
