"""Safety policy decisions with a fake Windows platform (runs on any OS)."""

import pytest

from windows_mcp.safety.config import SafetyConfig, parse_safety_config
from windows_mcp.safety.gate import SafetyPolicy


class FakePlatform:
    own_pid = 999

    def __init__(self, fg=1, at_point=None, names=None, titles=None):
        self.fg = fg
        self.at_point = at_point or {}
        self.names = names or {
            1: "notepad.exe",
            2: "chrome.exe",
            3: "consent.exe",
            999: "python.exe",
        }
        self.titles = titles or {"Notepad": [1], "Chrome": [2]}

    def process_name(self, pid):
        return self.names.get(pid)

    def foreground_pid(self):
        return self.fg

    def pid_at_point(self, x, y):
        return self.at_point.get((x, y), self.fg)

    def pids_for_window_title(self, title):
        return self.titles.get(title, [])


def policy(platform=None, **safety):
    return SafetyPolicy(
        parse_safety_config(safety) if safety else SafetyConfig(), platform or FakePlatform()
    )


@pytest.mark.parametrize("tool", ["Type", "Shortcut", "Scroll"])
def test_keyboard_tools_check_foreground(tool):
    assert policy(FakePlatform(fg=1)).evaluate(tool, {"text": "x", "shortcut": "ctrl+s"}).allowed
    d = policy(FakePlatform(fg=2)).evaluate(tool, {"text": "x", "shortcut": "ctrl+s"})
    assert not d.allowed and "chrome.exe" in d.reason


def test_click_resolves_window_under_point():
    p = FakePlatform(fg=1, at_point={(10, 20): 2})
    assert not policy(p).evaluate("Click", {"loc": [10, 20]}).allowed
    assert not policy(p).evaluate("Click", {"loc": "[10, 20]"}).allowed
    assert policy(p).evaluate("Click", {"loc": [5, 5]}).allowed


def test_label_click_also_checks_foreground():
    p = FakePlatform(fg=2)
    assert not policy(p).evaluate("Click", {"label": 3}).allowed


def test_unparsable_loc_fails_closed():
    assert not policy().evaluate("Click", {"loc": "nonsense"}).allowed


def test_multiselect_checks_every_point():
    p = FakePlatform(fg=1, at_point={(1, 1): 1, (2, 2): 2})
    assert not policy(p).evaluate("MultiSelect", {"locs": [[1, 1], [2, 2]]}).allowed
    assert policy(p).evaluate("MultiEdit", {"locs": [[1, 1, "a"]]}).allowed


def test_drag_checks_source_and_destination():
    p = FakePlatform(fg=1, at_point={(1, 1): 1, (2, 2): 2})
    assert not policy(p).evaluate("Move", {"loc": [1, 1], "from_loc": [2, 2], "drag": True}).allowed


def test_unknown_process_denied():
    assert not policy(FakePlatform(fg=None)).evaluate("Type", {"text": "a"}).allowed


def test_own_process_always_denied_even_without_enforce():
    d = policy(FakePlatform(fg=999), apps={"enforce": False}).evaluate("Type", {"text": "y"})
    assert not d.allowed and "windows-mcp" in d.reason


def test_enforce_false_allows_any_app():
    assert (
        policy(FakePlatform(fg=2), apps={"enforce": False}).evaluate("Type", {"text": "a"}).allowed
    )


def test_allowlist_normalizes_paths_and_exe():
    pol = policy(apps={"allowed": ["C:\\Program Files\\Google\\Chrome\\chrome.exe"]})
    assert pol.evaluate("App", {"mode": "launch", "name": "Chrome"}).allowed
    assert not pol.evaluate("App", {"mode": "launch", "name": "Notepad"}).allowed


def test_app_launch_executable_allowlist_and_installer_confirm():
    pol = policy(apps={"allowed": ["notepad", "setup"]})
    d = pol.evaluate("App", {"mode": "launch_executable", "executable": "C:\\x\\notepad.exe"})
    assert d.allowed and d.confirm is None
    d = pol.evaluate("App", {"mode": "launch_executable", "executable": "D:\\setup.exe"})
    assert d.allowed and d.confirm
    assert not pol.evaluate("App", {"mode": "launch_executable", "executable": "cmd.exe"}).allowed


def test_app_switch_resolves_title():
    pol = policy()
    assert pol.evaluate("App", {"mode": "switch", "name": "Notepad"}).allowed
    assert not pol.evaluate("App", {"mode": "switch", "name": "Chrome"}).allowed
    assert not pol.evaluate("App", {"mode": "switch", "name": "Missing"}).allowed


@pytest.mark.parametrize("combo", ["alt+f4", "ALT + F4", "ctrl+w", "shift+delete", "delete"])
def test_risky_shortcuts_need_confirmation(combo):
    d = policy().evaluate("Shortcut", {"shortcut": combo})
    assert d.allowed and d.confirm


def test_safe_shortcut_no_confirmation():
    assert policy().evaluate("Shortcut", {"shortcut": "ctrl+s"}).confirm is None


def test_uac_target_requires_confirmation():
    pol = policy(FakePlatform(fg=3), apps={"allowed": ["consent"]})
    d = pol.evaluate("Click", {"label": 1})
    assert d.allowed and "UAC" in d.confirm


def test_process_kill():
    pol = policy()
    d = pol.evaluate("Process", {"mode": "kill", "pid": 1})
    assert d.allowed and d.confirm
    assert not pol.evaluate("Process", {"mode": "kill", "pid": 2}).allowed
    assert not pol.evaluate("Process", {"mode": "kill", "name": "chrome.exe"}).allowed
    assert pol.evaluate("Process", {"mode": "kill", "name": "notepad.exe"}).confirm
    assert not pol.evaluate("Process", {"mode": "kill", "pid": 999}).allowed
    assert pol.evaluate("Process", {"mode": "list"}).confirm is None


@pytest.mark.parametrize(
    "tool,args",
    [
        ("FileSystem", {"mode": "delete", "path": "C:\\a"}),
        ("FileSystem", {"mode": "move", "path": "a", "destination": "b"}),
        ("FileSystem", {"mode": "write", "path": "a", "overwrite": "true"}),
        ("Registry", {"mode": "set", "path": "HKCU:\\x", "name": "n"}),
        ("Registry", {"mode": "delete", "path": "HKCU:\\x"}),
    ],
)
def test_destructive_background_tools_confirm(tool, args):
    d = policy().evaluate(tool, args)
    assert d.allowed and d.confirm


@pytest.mark.parametrize(
    "tool,args", [("FileSystem", {"mode": "read", "path": "a"}), ("Screenshot", {})]
)
def test_read_only_tools_pass(tool, args):
    d = policy().evaluate(tool, args)
    assert d.allowed and d.confirm is None


def test_powershell_default_disabled():
    d = policy().evaluate("PowerShell", {"command": "Get-Date"})
    assert not d.allowed and "disabled" in d.reason


def test_powershell_allowlist_and_risky_verb():
    pol = policy(powershell={"mode": "allowlist", "allowed_commands": ["Get-Date", "Stop-Process"]})
    assert pol.evaluate("PowerShell", {"command": "Get-Date"}).confirm is None
    assert pol.evaluate("PowerShell", {"command": "Stop-Process -Id 12"}).confirm
    assert not pol.evaluate("PowerShell", {"command": "Get-Date; Remove-Item x"}).allowed


def test_powershell_unrestricted_always_confirms():
    pol = policy(powershell={"mode": "unrestricted"})
    assert pol.evaluate("PowerShell", {"command": "anything | at all"}).confirm
