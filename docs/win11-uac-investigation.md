# Win 11 UAC investigation — why we disable the Secure Desktop

## Summary

On Windows 11, an external process — even one with UIAccess running in the
correct session — **cannot reach `consent.exe` while UAC is on the Secure
Desktop**. Every documented and undocumented route we tried in a Win 11 test
environment came back empty. The only mechanism that exposes the dialog to
the agent is the documented Microsoft policy
`PromptOnSecureDesktop = 0`, which makes UAC render on the user's Default
desktop instead of switching to Winlogon. `windows-mcp service
secure-desktop install` now sets that registry value (and restores it on
uninstall).

This document records the dead-ends so the next person evaluating the
problem can skip them.

## Why "just walk the tree from the service" doesn't work

Two Windows boundaries are in play:

1. **Session 0 isolation** — services in session 0 cannot enumerate UIA
   elements owned by user-session processes, even after `SetThreadDesktop`
   to Winlogon.
2. **The Secure Desktop is a separate `HDESK`** under
   `WinSta0`. From inside the user session, even with UIAccess, the
   desktop has a tight DACL that blocks `OpenDesktopW("Winlogon",
   DESKTOP_ALL_ACCESS)` for non-trusted callers.

So the textbook approach — "spawn a UIAccess helper in the active user
session, attach it to Winlogon, walk the UIA tree" — runs straight into
both walls on Win 11.

## Strategies we tried, and what each one returned

All measurements taken on a Win 11 test environment (build 10.0.26200)
with a UAC prompt actively up on the Secure Desktop. Each row is a
strategy that's still readable in `git log`'s history before this change
removed it.

### S0. Direct `OpenDesktopW("Winlogon", DESKTOP_ALL_ACCESS)` from a UIAccess worker

```
gle=ERROR_ACCESS_DENIED
```

Winlogon's DACL doesn't grant `DESKTOP_*` to interactive users. UIAccess
gives you `SendInput` rights across UIPI levels, not arbitrary kernel
object access.

### S1. Broker (SYSTEM) loosens Winlogon's DACL, worker reattaches

```
EnumDesktopWindows ok=False gle=0 windows_seen=0
```

After temporarily ACEing the console user onto Winlogon with
`DESKTOP_ALL_ACCESS`, the worker can now `OpenDesktopW` + `SetThreadDesktop`
— but `EnumDesktopWindows` still returns zero windows. The OpenDesktop
handle is granted but the desktop's window list is filtered for callers
that aren't `TrustedInstaller`/`Winlogon` itself.

### S2. Broker enumerates Winlogon, hands worker the consent HWND

```
broker enumerated Winlogon: consent.exe hwnd=0x0
```

Same `EnumDesktopWindows` call from the SYSTEM broker (no DACL change)
also returns zero windows.  Tried with the broker impersonating the
elevated user token via `ImpersonateLoggedOnUser` (commit `b638e5b`); same
result.

### S3. `IUIAutomation.GetFocusedElement` from the UIAccess worker

Returned a `Text Area` whose top ancestor was the **user-session**
`Administrator: Windows PowerShell`. The worker's `GetFocusedElement`
sees the user desktop's focus, not Winlogon's.

### S4. `IUIAutomation.ElementFromPoint(640, 360)` (centre of the dialog)

Same result as S3 — returns the user-session window underneath the
secure-desktop overlay, not consent.exe.

### S5. `GetForegroundWindow` then `IUIAutomation.ElementFromHandle`

```
GetForegroundWindow=0x0
```

`GetForegroundWindow` is desktop-scoped to the caller. From Default, it
returns `NULL` while the input desktop is Winlogon.

### S6. `GetGUIThreadInfo` for every consent.exe thread

We enumerate consent.exe via `CreateToolhelp32Snapshot` and call
`GetGUIThreadInfo` on each of its TIDs:

```
GetGUIThreadInfo(<tid>) ok=True active=0x0 focus=0x0 menuOwner=0x0
```

Some TIDs succeed (ok=True) — the thread has a GUI — but every `hwnd`
field is null when queried from another desktop.

### S7. `IUIAutomation.RawViewWalker` from the desktop root

Returned the user-session top-level windows (Taskbar, the elevated
PowerShell, the cmd window that triggered the UAC, the Open File security
warning that's still pending). Consent.exe never appears as a child of
the root element we have access to.

### S8. UIA event subscription: `FocusChanged`, `WindowOpened`,
`StructureChanged`

```
AddFocusChangedEventHandler registered
AddAutomationEventHandler(WindowOpened) registered
AddStructureChangedEventHandler registered
... <event wait expires, no callback fires>
```

The user's instinct that "Narrator hears UIA events across the secure
desktop boundary" was the most promising lead, but no events ever fire
for a UIAccess worker registered on Default while UAC is up.  Narrator's
behaviour likely relies on the `EnableUIAccess` Group Policy plus its own
shipped MSAA hook chain, neither of which our process can replicate.

### S9. Broker (SYSTEM) `BitBlt` of the Secure Desktop after
`SetThreadDesktop(Winlogon)`

```
ImageGrab.grab(all_screens=True) → solid black 1280x720 frame
```

`capture_screenshot()` already used `SetThreadDesktop(Winlogon)` under
`_input_desktop()` before calling `ImageGrab.grab` — the same approach
that captures the lock screen successfully when no UAC is up. While UAC
is up the call succeeds but every pixel is `(0, 0, 0)`. This is the
anti-keylogger / anti-screenscrape hardening introduced in Win 10 1809
and made stricter in Win 11 — the secure desktop renders to a separate
GPU surface that GDI capture cannot touch.

The synthetic-tree fallback that searched the frame for Microsoft Blue
(`#0067C0`) consent-button pixels therefore found zero matches and
returned `[]`.

## The wall, summarised

| Surface | Behaviour on Win 11 while UAC is up |
| --- | --- |
| UIA `GetFocusedElement` from UIAccess on Default | returns Default's focus, doesn't cross |
| UIA `GetForegroundWindow` / `ElementFromPoint` / `ElementFromHandle` | NULL or Default-desktop windows |
| UIA `GetGUIThreadInfo(consent-tid)` | succeeds but `hwndActive=0, hwndFocus=0` |
| UIA `RawViewWalker` from desktop root | Default-desktop windows only |
| UIA `FocusChanged` / `WindowOpened` / `StructureChanged` events | never fire for consent.exe |
| Win32 `EnumDesktopWindows` on Winlogon (broker, DACL-loosened) | `windows_seen=0` |
| GDI `BitBlt` of the secure desktop (broker, `SetThreadDesktop(Winlogon)`) | all-black frame |

Every API surface we have legitimate access to is blocked. Real-world
accessibility tools (Narrator, Magnifier) that *do* read UAC use code
paths we cannot reach from a third-party process — either a registered
accessibility callback baked into the kernel input path, or an undocumented
trust relationship between Winlogon and signed-by-Microsoft binaries.

## The fix we shipped

`windows-mcp service secure-desktop install` now writes
`HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System`:

* `PromptOnSecureDesktop = 0` (DWORD)
* `ConsentPromptBehaviorAdmin = 5` (DWORD)

Both are required. Iter 6 set only the first one, confirmed the readback
returned 0, and then watched UAC fire on Winlogon anyway because the Win 11
test environment ships with `ConsentPromptBehaviorAdmin = 2`
("Prompt for consent **on the secure desktop**") -- a value whose
documented description explicitly pins UAC to the secure desktop regardless
of `PromptOnSecureDesktop`. Values 3, 4, and 5 describe the same prompts
without the secure desktop; 5 is the Win 11 default.

With that policy:

* UAC still fires for elevation requests; the user still sees a dialog
  asking for consent.
* The dialog renders on the user's **Default** desktop instead of
  switching to Winlogon.
* `consent.exe` becomes a regular user-session top-level window
  reachable via plain UIA from a same-session worker — no UIAccess, no
  DACL loosening, no cross-desktop tricks.

`wait_for_uac_prompt` then becomes a 30-line function: poll for
`consent.exe` in the process snapshot, spawn the same-session worker's
`tree` op, return the result.

`service secure-desktop uninstall` restores `PromptOnSecureDesktop = 1`,
returning the machine to its default secure posture.

## Security trade-off

`PromptOnSecureDesktop = 0` is a documented Group Policy setting
(`Computer Configuration → Windows Settings → Security Settings →
Local Policies → Security Options → User Account Control: Switch to the
secure desktop when prompting for elevation`). It is supported by
Microsoft and is the same setting enterprise admins flip when running
older RDP or kiosk software that can't bridge the secure-desktop hop.

The cost: while the policy is off, any user-session process — including
malware that gained a foothold — can read or click UAC prompts. That
defeats the anti-phishing / anti-keylogger purpose of the Secure
Desktop. The trade-off is appropriate for the scenarios this project
targets (a VM or sandbox where the agent is meant to drive UAC), but it
is **not appropriate for general-purpose machines**. The install command
is opt-in, gated behind elevation, and reversed on uninstall, so the
machine-wide impact starts and ends with the user explicitly running
`windows-mcp service secure-desktop install`.
