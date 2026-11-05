"""Start Menu launch and PATH resolution checks (fake Windows calls, runs on any OS).

Reproduces the field bug on Italian Windows: Notepad is listed as "Blocco note", so the
fuzzy Start Menu match for "notepad" picks Notepad++ and the name-only allowlist passed it.
"""

import importlib.util
import json
import os
import sys
from pathlib import Path

from windows_mcp.safety.config import parse_safety_config
from windows_mcp.safety.gate import SafetyGate
from windows_mcp.safety.launch import (
    LaunchDenied,
    LaunchGuard,
    ShortcutInfo,
    find_launch_denied,
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


def test_fuzzy_only_match_picks_notepad_plus_plus_and_guard_denies_it():
    # Without target lookups the fuzzy fallback picks Notepad++ (the original field bug);
    # the guard then refuses it on its real executable.
    matched = match_start_menu_app("notepad", ITALIAN_START_MENU)
    assert matched == ("notepad++", NPP)
    error = guard().check_target("notepad", *matched)
    assert error and "notepad++.exe" in error and "not in safety.apps.allowed" in error


def test_notepad_prefers_the_entry_whose_executable_is_notepad():
    resolve = FakeLaunchPlatform().resolve_app_target
    matched = match_start_menu_app("notepad", ITALIAN_START_MENU, resolve=resolve)
    assert matched == ("blocco note", NOTEPAD_AUMID)
    assert guard().check_target("notepad", *matched) is None
    matched = match_start_menu_app("notepad.exe", ITALIAN_START_MENU, resolve=resolve)
    assert matched == ("blocco note", NOTEPAD_AUMID)


def test_exact_display_name_wins_and_path_stem_matches():
    resolve = FakeLaunchPlatform().resolve_app_target
    assert match_start_menu_app("Notepad++", ITALIAN_START_MENU, resolve=resolve) == (
        "notepad++",
        NPP,
    )
    assert match_start_menu_app("Blocco Note", ITALIAN_START_MENU) == ("blocco note", NOTEPAD_AUMID)
    apps = {"editor": "C:\\Windows\\notepad.exe", "notepad++": NPP}
    assert match_start_menu_app("notepad", apps) == ("editor", "C:\\Windows\\notepad.exe")


def test_shortcut_entries_match_on_their_target():
    lnk = "C:\\ProgramData\\Start Menu\\Notepad.lnk"
    apps = {"notepad++": NPP, "blocco note": lnk}
    platform = FakeLaunchPlatform(targets={lnk: "C:\\Windows\\System32\\notepad.exe"})
    assert match_start_menu_app("notepad", apps, resolve=platform.resolve_app_target) == (
        "blocco note",
        lnk,
    )


def test_resolver_errors_fall_back_to_fuzzy():
    def broken(appid):
        raise OSError("COM failure")

    assert match_start_menu_app("notepad", ITALIAN_START_MENU, resolve=broken) == ("notepad++", NPP)


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


# -- denial reaches the client as safety_denied and is audited as denied --------------


def _gate_app(tmp_path, error_from_guard):
    import json as _json

    from fastmcp import FastMCP

    cfg = parse_safety_config(
        {
            "apps": {"allowed": ["notepad"]},
            "audit": {"path": str(tmp_path / "audit.jsonl")},
            "kill_switch": {"flag_file": str(tmp_path / "K")},
            "confirm": {"enabled": False},
        }
    )
    platform = FakeLaunchPlatform(exes={4242: NPP})
    gate = SafetyGate(cfg, platform)
    mcp = FastMCP("t")
    mcp.add_middleware(gate)

    @mcp.tool(name="App")
    def app(mode: str = "launch", name: str | None = None) -> str:
        # Mirrors Desktop.launch_app: guard check before, process check after.
        g = get_launch_guard()
        if error_from_guard == "before":
            matched = match_start_menu_app(name, ITALIAN_START_MENU)
            err = g.check_target(name, *matched)
        else:
            err = g.verify_launched(name, 4242)
        if err:
            raise LaunchDenied(err)
        return "launched"

    def rows():
        lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        return [_json.loads(line) for line in lines]

    return mcp, platform, rows


async def _call_denied(mcp):
    import json as _json

    from fastmcp import Client

    async with Client(mcp) as c:
        result = await c.call_tool(
            "App", {"mode": "launch", "name": "notepad"}, raise_on_error=False
        )
    assert result.is_error
    body = _json.loads(result.content[0].text)
    assert body["code"] == "safety_denied"
    return body


async def test_start_menu_denial_is_a_safety_denied_error(tmp_path):
    mcp, _, rows = _gate_app(tmp_path, "before")
    body = await _call_denied(mcp)
    assert "notepad++.exe" in body["reason"]
    (row,) = rows()
    assert row["decision"] == "denied" and row["result"] is None
    assert "notepad++.exe" in row["reason"]
    set_launch_guard(None)


async def test_post_launch_kill_is_a_safety_denied_error(tmp_path):
    mcp, platform, rows = _gate_app(tmp_path, "after")
    body = await _call_denied(mcp)
    assert "terminated" in body["reason"] and platform.terminated == [4242]
    (row,) = rows()
    assert row["decision"] == "denied" and row["result"] is None
    set_launch_guard(None)


def test_find_launch_denied_walks_the_cause_chain():
    inner = LaunchDenied("no")
    try:
        try:
            raise inner
        except LaunchDenied as e:
            raise RuntimeError("wrapped") from e
    except RuntimeError as outer:
        assert find_launch_denied(outer) is inner
    assert find_launch_denied(ValueError("x")) is None


# -- launch_executable on a .lnk starts the shortcut's target ---------------------------


def _load_app_tool(monkeypatch):
    stub = type(sys)("windows_mcp.infrastructure")
    stub.with_analytics = lambda *a, **k: lambda f: f
    monkeypatch.setitem(sys.modules, "windows_mcp.infrastructure", stub)
    path = Path(__file__).parents[1] / "src/windows_mcp/tools/app.py"
    spec = importlib.util.spec_from_file_location("app_tool_lnk_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakePopen:
    def __init__(self, pid=4242):
        self.pid = pid
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return self


def _shortcut_setup(tmp_path, target_name="editor.exe"):
    target = tmp_path / "Program Files" / target_name
    target.parent.mkdir()
    target.write_text("")
    workdir = tmp_path / "work"
    workdir.mkdir()
    lnk = tmp_path / "Editor.lnk"
    lnk.write_text("")
    info = ShortcutInfo(
        target=str(target), arguments='--profile "Work A"', working_dir=str(workdir)
    )
    return lnk, target, workdir, info


def test_lnk_starts_target_with_shortcut_args_and_workdir(tmp_path, monkeypatch):
    app = _load_app_tool(monkeypatch)
    set_launch_guard(None)
    lnk, target, workdir, info = _shortcut_setup(tmp_path)
    popen = FakePopen()
    out = json.loads(
        app._launch_executable(
            str(lnk), ["my file.txt"], None, read_shortcut=lambda p: info, popen=popen
        )
    )
    ((command, kwargs),) = popen.calls
    assert command == f'"{target}" --profile "Work A" "my file.txt"'  # Spaces quoted.
    assert kwargs["cwd"] == str(workdir) and kwargs["shell"] is False
    assert out["executable"] == str(target) and out["shortcut"] == str(lnk)


def test_lnk_explicit_cwd_wins(tmp_path, monkeypatch):
    app = _load_app_tool(monkeypatch)
    set_launch_guard(None)
    lnk, _, _, info = _shortcut_setup(tmp_path)
    popen = FakePopen()
    app._launch_executable(str(lnk), None, str(tmp_path), read_shortcut=lambda p: info, popen=popen)
    assert popen.calls[0][1]["cwd"] == str(tmp_path)


def test_lnk_target_changed_to_disallowed_is_denied_before_start(tmp_path, monkeypatch):
    app = _load_app_tool(monkeypatch)
    lnk, _, _, info = _shortcut_setup(tmp_path, "notepad++.exe")
    set_launch_guard(guard(allowed=("notepad",)))
    popen = FakePopen()
    try:
        app._launch_executable(str(lnk), None, None, read_shortcut=lambda p: info, popen=popen)
    except LaunchDenied as exc:
        assert "notepad++.exe" in exc.reason
    else:
        raise AssertionError("expected LaunchDenied")
    finally:
        set_launch_guard(None)
    assert popen.calls == []


def test_lnk_started_process_is_checked_by_pid(tmp_path, monkeypatch):
    app = _load_app_tool(monkeypatch)
    lnk, _, _, info = _shortcut_setup(tmp_path, "notepad.exe")
    platform = FakeLaunchPlatform(exes={4242: NPP})  # The started pid turned out to be npp.
    set_launch_guard(guard(platform, allowed=("notepad",)))
    try:
        app._launch_executable(
            str(lnk), None, None, read_shortcut=lambda p: info, popen=FakePopen()
        )
    except LaunchDenied as exc:
        assert "terminated" in exc.reason
    else:
        raise AssertionError("expected LaunchDenied")
    finally:
        set_launch_guard(None)
    assert platform.terminated == [4242]


def test_lnk_without_target_or_with_non_exe_target_is_rejected(tmp_path, monkeypatch):
    app = _load_app_tool(monkeypatch)
    set_launch_guard(None)
    lnk, _, _, _ = _shortcut_setup(tmp_path)
    doc = tmp_path / "readme.txt"
    doc.write_text("")
    cases = [
        (lambda p: None, "no readable target"),
        (lambda p: ShortcutInfo(target=str(doc)), "not an executable"),
        (lambda p: ShortcutInfo(target=str(tmp_path / "gone.exe")), "does not exist"),
    ]
    for reader, message in cases:
        popen = FakePopen()
        try:
            app._launch_executable(str(lnk), None, None, read_shortcut=reader, popen=popen)
        except ValueError as exc:
            assert message in str(exc)
        else:
            raise AssertionError(f"expected ValueError: {message}")
        assert popen.calls == []


def test_plain_executable_still_uses_argv_list(tmp_path, monkeypatch):
    app = _load_app_tool(monkeypatch)
    set_launch_guard(None)
    exe = tmp_path / "tool.exe"
    exe.write_text("")
    popen = FakePopen()
    app._launch_executable(str(exe), ["a b"], None, popen=popen)
    assert popen.calls[0][0] == [str(exe), "a b"]


# -- App launch with a path in `name`: started directly, never fuzzy-matched -----------


def test_path_like_names_are_not_start_menu_names():
    al = _load_app_launch()
    assert al.is_path_like("C:/Users/vince/wmcp-test/notepad_ok.lnk")
    assert al.is_path_like("C:\\Windows\\System32\\notepad.exe")
    assert al.is_path_like("\\\\server\\share\\app.exe")
    assert not al.is_path_like("notepad")
    assert not al.is_path_like("Blocco note")


def test_direct_launch_path_requires_existing_absolute_exe_or_lnk(tmp_path):
    al = _load_app_launch()
    assert al.direct_launch_path("notepad") is None  # Start Menu name: normal matching.
    lnk = tmp_path / "notepad_ok.lnk"
    lnk.write_text("")
    exe = tmp_path / "tool.exe"
    exe.write_text("")
    doc = tmp_path / "readme.txt"
    doc.write_text("")
    assert al.direct_launch_path(str(lnk)) == str(lnk.resolve())
    assert al.direct_launch_path(f'"{exe}"') == str(exe.resolve())
    for bad, message in [
        (str(tmp_path / "charmap_no.lnk"), "does not exist"),
        (str(doc), ".exe, .com or .lnk"),
        ("wmcp-test/notepad_ok.lnk", "must be an absolute path"),
    ]:
        try:
            al.direct_launch_path(bad)
        except ValueError as exc:
            assert message in str(exc)
        else:
            raise AssertionError(f"expected ValueError for {bad}")


def _launch_recorder(executable, pid=4242):
    calls = []

    def launch(path):
        calls.append(path)
        return json.dumps({"pid": pid, "executable": executable, "args": [], "cwd": None})

    return calls, launch


def test_allowed_lnk_path_is_checked_on_its_target_and_started(tmp_path):
    al = _load_app_launch()
    lnk = str(tmp_path / "notepad_ok.lnk")
    target = "C:\\Windows\\System32\\notepad.exe"
    g = guard(FakeLaunchPlatform(targets={lnk: target}), allowed=("notepad",))
    calls, launch = _launch_recorder(target)
    msg, status, pid = al.launch_direct_path(lnk, lnk, guard=g, launch=launch)
    assert (status, pid) == (0, 4242) and calls == [lnk] and target in msg


def test_disallowed_lnk_target_is_denied_before_start_not_fuzzy_matched(tmp_path):
    al = _load_app_launch()
    lnk = str(tmp_path / "charmap_no.lnk")
    g = guard(
        FakeLaunchPlatform(targets={lnk: "C:\\Windows\\System32\\charmap.exe"}),
        allowed=("notepad",),
    )
    calls, launch = _launch_recorder("x")
    try:
        al.launch_direct_path(lnk, lnk, guard=g, launch=launch)
    except LaunchDenied as exc:
        assert "charmap.exe" in exc.reason and "Start Menu" not in exc.reason
    else:
        raise AssertionError("expected LaunchDenied")
    assert calls == []


def test_disallowed_exe_path_and_unresolvable_lnk_are_denied(tmp_path):
    al = _load_app_launch()
    lnk = str(tmp_path / "broken.lnk")
    g = guard(FakeLaunchPlatform(targets={NPP: NPP}), allowed=("notepad",))
    for path, needle in [(NPP, "notepad++.exe"), (lnk, "could not be determined")]:
        calls, launch = _launch_recorder("x")
        try:
            al.launch_direct_path(path, path, guard=g, launch=launch)
        except LaunchDenied as exc:
            assert needle in exc.reason
        else:
            raise AssertionError("expected LaunchDenied")
        assert calls == []


def test_direct_path_without_guard_just_starts(tmp_path):
    al = _load_app_launch()
    calls, launch = _launch_recorder("C:\\x\\tool.exe", pid=7)
    assert al.launch_direct_path("p", "p", guard=None, launch=launch)[1:] == (0, 7)


def test_bare_file_name_with_launch_suffix_is_rejected_not_fuzzy_matched():
    al = _load_app_launch()
    for bare in ["notepad_ok.lnk", "NOTEPAD.EXE", "tool.Com", ' "app.lnk" ']:
        try:
            al.direct_launch_path(bare)
        except ValueError as exc:
            assert "must be an absolute path" in str(exc)
        else:
            raise AssertionError(f"expected ValueError for {bare!r}")
    # Start Menu names without a file suffix still go to normal matching.
    for name in ["notepad", "Blocco note", "Notepad++", "Visual Studio Code"]:
        assert al.direct_launch_path(name) is None


def test_launch_denied_is_logged_without_traceback():
    import logging

    from fastmcp.exceptions import FastMCPError

    exc = LaunchDenied("not allowed")
    assert isinstance(exc, FastMCPError)  # FastMCP logs it with exc_info=False.
    assert exc.log_level == logging.WARNING
    assert exc.reason == "not allowed"
