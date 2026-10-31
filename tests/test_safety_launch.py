"""Start Menu launch and PATH resolution checks (fake Windows calls, runs on any OS).

Reproduces the field bug on Italian Windows: Notepad is listed as "Blocco note", so the
fuzzy Start Menu match for "notepad" picks Notepad++ and the name-only allowlist passed it.
"""

import importlib.util
import os
import sys
from pathlib import Path

from windows_mcp.safety.config import parse_safety_config
from windows_mcp.safety.gate import SafetyGate
from windows_mcp.safety.launch import (
    LaunchGuard,
    get_launch_guard,
    resolve_command,
    set_launch_guard,
)

NPP = "C:\\Program Files\\Notepad++\\notepad++.exe"
NOTEPAD_AUMID = "Microsoft.WindowsNotepad_8wekyb3d8bbwe!App"
ITALIAN_START_MENU = {
    "blocco note": NOTEPAD_AUMID,
    "notepad++": NPP,
    "paint": "Microsoft.Paint_8wekyb3d8bbwe!App",
}


def _load_app_launch():
    # Loaded by path: importing the windows_mcp.desktop package needs Windows.
    path = Path(__file__).parents[1] / "src/windows_mcp/desktop/app_launch.py"
    spec = importlib.util.spec_from_file_location("app_launch_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


match_start_menu_app = _load_app_launch().match_start_menu_app


class FakeLaunchPlatform:
    def __init__(self, targets=None, exes=None, can_kill=True):
        self.targets = targets or {
            NPP: NPP,
            NOTEPAD_AUMID: "C:\\Program Files\\WindowsApps\\Microsoft.WindowsNotepad\\Notepad\\Notepad.exe",
        }
        self.exes = exes or {}
        self.can_kill = can_kill
        self.terminated = []

    def resolve_app_target(self, appid):
        return self.targets.get(appid)

    def process_exe(self, pid):
        return self.exes.get(pid)

    def terminate_process(self, pid):
        self.terminated.append(pid)
        return self.can_kill


def guard(platform=None, allowed=("notepad",)):
    cfg = parse_safety_config({"apps": {"allowed": list(allowed)}})
    return LaunchGuard(cfg, platform or FakeLaunchPlatform())


def test_notepad_fuzzy_matches_notepad_plus_plus_and_is_denied():
    matched = match_start_menu_app("notepad", ITALIAN_START_MENU)
    assert matched == ("notepad++", NPP)  # The bug: the requested name passes, the target differs.
    error = guard().check_target("notepad", *matched)
    assert error and "notepad++.exe" in error and "not in safety.apps.allowed" in error


def test_localized_notepad_entry_is_allowed_by_its_executable():
    matched = match_start_menu_app("blocco note", ITALIAN_START_MENU)
    assert matched == ("blocco note", NOTEPAD_AUMID)
    assert guard().check_target("blocco note", *matched) is None


def test_unresolvable_target_is_denied():
    g = guard(FakeLaunchPlatform(targets={}))
    error = g.check_target("chrome", "chrome", "Chrome")
    assert error and "could not be determined" in error


def test_resolver_exception_is_denied():
    class Broken(FakeLaunchPlatform):
        def resolve_app_target(self, appid):
            raise OSError("COM failure")

    assert guard(Broken()).check_target("notepad", "notepad++", NPP)


def test_launched_process_not_allowed_is_terminated_and_reported():
    platform = FakeLaunchPlatform(exes={4242: NPP})
    error = guard(platform).verify_launched("notepad", 4242)
    assert error and "notepad++.exe" in error and "terminated" in error
    assert platform.terminated == [4242]  # Only the pid we started.


def test_launched_process_kill_failure_is_reported():
    platform = FakeLaunchPlatform(exes={7: NPP}, can_kill=False)
    assert "could NOT be terminated" in guard(platform).verify_launched("notepad", 7)


def test_launched_allowed_or_unknown_process_is_left_alone():
    platform = FakeLaunchPlatform(exes={5: "C:\\Windows\\System32\\notepad.exe"})
    g = guard(platform)
    assert g.verify_launched("notepad", 5) is None
    assert g.verify_launched("notepad", 6) is None  # Exited already.
    assert g.verify_launched("notepad", 0) is None  # No pid from a packaged-app launch.
    assert platform.terminated == []


def test_gate_installs_guard_only_when_enforcing(tmp_path):
    base = {"audit": {"enabled": False}, "kill_switch": {"flag_file": str(tmp_path / "K")}}
    SafetyGate(parse_safety_config(base), FakeLaunchPlatform())
    assert isinstance(get_launch_guard(), LaunchGuard)
    SafetyGate(parse_safety_config({**base, "apps": {"enforce": False}}), FakeLaunchPlatform())
    assert get_launch_guard() is None
    set_launch_guard(None)


def test_bare_name_resolves_through_path_not_cwd(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    real = bin_dir / "notepad.exe"
    real.write_text("")
    real.chmod(0o755)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    planted = cwd / "notepad.exe"
    planted.write_text("")
    planted.chmod(0o755)
    monkeypatch.chdir(cwd)
    path_env = os.pathsep.join([".", "relative", str(bin_dir)])
    assert resolve_command("notepad.exe", path_env=path_env) == str(real)
    assert resolve_command("missing.exe", path_env=path_env) is None
    # A value with a directory part is a path, resolved as given.
    assert resolve_command(str(planted), path_env=path_env) == str(planted)


def test_tool_resolve_executable_uses_path(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "tool.exe"
    exe.write_text("")
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.chdir(tmp_path)
    # tools/app.py imports windows_mcp.infrastructure (Windows-only); stub it.
    stub = type(sys)("windows_mcp.infrastructure")
    stub.with_analytics = lambda *a, **k: lambda f: f
    monkeypatch.setitem(sys.modules, "windows_mcp.infrastructure", stub)
    path = Path(__file__).parents[1] / "src/windows_mcp/tools/app.py"
    spec = importlib.util.spec_from_file_location("app_tool_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module._resolve_executable("tool.exe") == exe
    try:
        module._resolve_executable("nothere.exe")
    except ValueError as exc:
        assert "not found" in str(exc)
    else:
        raise AssertionError("expected ValueError")
