from typing import Set

BROWSER_NAMES = set(["msedge.exe", "chrome.exe", "firefox.exe"])

AVOIDED_APPS: Set[str] = set(["Recording toolbar"])

EXCLUDED_APPS: Set[str] = set(["Program Manager", "Taskbar"]).union(AVOIDED_APPS)

# Window switching behavior configuration
# Options:
# - "foreground": Uses win32gui.SetForegroundWindow (recommended, non-tainting)
# - "topmost_safe": Uses SetWindowTopmost with immediate removal (safe fallback)
# - "topmost_legacy": Uses original SetWindowTopmost behavior (may cause sticky windows)
WINDOW_SWITCH_MODE = "foreground"
