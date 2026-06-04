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

The alternative — leaving UAC on the secure desktop and reaching it via
some other mechanism — is well-explored. The summary below is so future
readers don't waste time re-walking these paths. Full forensic detail
(iterations 1 through 8, with the actual Win32 calls, registry reads,
and observed return codes) lives in
[`win11-uac-investigation.md`](win11-uac-investigation.md).

If you need the secure-desktop protection back, run `uninstall`. The
agent will then see UAC fire but won't be able to inspect or dismiss
the dialog.

## What we tried that didn't work

* **UIAccess-enabled worker binary, code-signed and shipped from
  `%ProgramFiles%`.** This is Microsoft's documented "correct" answer
  for letting an accessibility tool read higher-integrity windows. We
  built the full pipeline (PyInstaller-frozen worker, embedded
  manifest with `uiAccess="true"`, self-signed cert added to
  LocalMachine\Root + TrustedPublisher, install into Program Files,
  `SetTokenInformation(TokenUIAccess=1)` on the spawn token). UIAccess
  was granted to the worker's token, but `OpenDesktopW("Winlogon", …)`
  still returned `ERROR_ACCESS_DENIED (5)` from the user-session
  worker. Win 11's Winlogon DACL is the actual gate; UIAccess opens
  cross-integrity reads but does not grant cross-desktop reads.
  Removed in this commit.

* **Loosening the Winlogon desktop DACL from a SYSTEM-context broker.**
  The host service has `SeRestorePrivilege` and could `SetSecurityInfo`
  on Winlogon to grant the console user `DESKTOP_READOBJECTS`. On Win 11
  25H2 the DACL write succeeded but `EnumDesktopWindows` on Winlogon
  still returned zero — the kernel applies an additional cross-session
  filter on top of the DACL, and the broker (session 0) can't reach
  session 1's Winlogon regardless. Removed.

* **Screenshot fallback.** Capture the framebuffer during UAC and
  segment the consent dialog by colour. Did work for *reading*
  (publisher, target executable), did not work for *clicking* (no UIA
  element ids, no input target for `Click`). Could detect-but-not-act,
  which left the agent half-blocked. Kept for the read-only "we know
  UAC fired" path, which is now redundant since plain UIA against
  Default desktop returns the full tree.

* **`EnableLUA=0`.** Disables UAC entirely. Breaks Microsoft Store /
  UWP apps in the same session, removes file/registry virtualisation,
  and silently auto-approves every elevation including the ones the
  agent didn't ask for. Considered and rejected as a default.

* **CPB=5 + POSD=0** (the obvious cousin of the iter-8 fix). Microsoft
  documents CPB=5 ("Prompt for consent for non-Windows binaries") as
  the modern Win 11 default. Setting it alongside `PromptOnSecureDesktop=0`
  *should* mean "prompt on Default desktop". Win 11 25H2 ignored
  `POSD=0` and routed UAC to Winlogon anyway. CPB=4 ("Prompt for
  consent" — same description without the Windows-binary carve-out)
  is the value Win 11 25H2 actually honours. The OS appears to treat
  the off-secure-desktop CPB values (3, 4, 5) as semantically distinct
  rather than equivalent. See iter-7 vs iter-8 in the investigation
  doc for the empirical confirmation.
