"""SafetyGate end to end through a FastMCP client (Windows calls faked)."""

import json
import os

import pytest
from fastmcp import Client, FastMCP

from windows_mcp.safety.config import parse_safety_config
from windows_mcp.safety.gate import SafetyGate
from windows_mcp.safety.killswitch import KillSwitch
from windows_mcp.safety.win32 import parse_hotkey


class FakePlatform:
    own_pid = 999

    def __init__(self, answer=False):
        self.answer = answer
        self.asked = []
        self.shots = []

    def process_name(self, pid):
        return {1: "notepad.exe", 2: "chrome.exe"}.get(pid)

    def foreground_pid(self):
        return 1

    def pid_at_point(self, x, y):
        return 2 if x > 100 else 1

    def pids_for_window_title(self, title):
        return [1]

    def confirm_dialog(self, title, text, timeout):
        self.asked.append((title, text, timeout))
        return self.answer

    def take_screenshot(self, path):
        self.shots.append(path)
        return path


def make(tmp_path, answer=False, **safety):
    safety.setdefault("audit", {})
    safety["audit"].setdefault("path", str(tmp_path / "audit.jsonl"))
    safety.setdefault("kill_switch", {"flag_file": str(tmp_path / "KILL")})
    cfg = parse_safety_config(safety)
    platform = FakePlatform(answer)
    gate = SafetyGate(cfg, platform)
    mcp = FastMCP("t")
    mcp.add_middleware(gate)
    calls = []

    @mcp.tool(name="Click")
    def click(loc: list[int] | None = None, label: int | None = None) -> str:
        calls.append(loc)
        return "clicked"

    @mcp.tool(name="Process")
    def proc(mode: str, pid: int | None = None, name: str | None = None) -> str:
        calls.append((mode, pid))
        return "killed"

    @mcp.tool(name="Type")
    def type_(text: str) -> str:
        calls.append(text)
        return "typed"

    return mcp, gate, platform, calls, tmp_path / "audit.jsonl"


def audit_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


async def test_allowed_call_is_audited(tmp_path):
    mcp, _, _, calls, log = make(tmp_path)
    async with Client(mcp) as c:
        r = await c.call_tool("Click", {"loc": [5, 5]})
    assert r.data == "clicked" and calls == [[5, 5]]
    (entry,) = audit_lines(log)
    assert entry["tool"] == "Click" and entry["decision"] == "allowed" and entry["result"] == "ok"
    assert "ts" in entry and "duration_ms" in entry


async def test_denied_app_never_runs(tmp_path):
    mcp, _, _, calls, log = make(tmp_path)
    async with Client(mcp) as c:
        with pytest.raises(Exception, match="safety_denied"):
            await c.call_tool("Click", {"loc": [500, 5]})
    assert calls == []
    assert audit_lines(log)[0]["decision"] == "denied"


async def test_confirmation_declined_and_approved(tmp_path):
    mcp, gate, platform, calls, log = make(tmp_path, answer=False, confirm={"timeout_seconds": 7})
    async with Client(mcp) as c:
        with pytest.raises(Exception, match="safety_declined"):
            await c.call_tool("Process", {"mode": "kill", "pid": 1})
        assert calls == [] and platform.asked[0][2] == 7.0
        platform.answer = True
        r = await c.call_tool("Process", {"mode": "kill", "pid": 1})
    assert r.data == "killed"
    assert [e["decision"] for e in audit_lines(log)] == ["declined", "confirmed"]


async def test_confirmation_disabled_skips_dialog(tmp_path):
    mcp, _, platform, _, _ = make(tmp_path, confirm={"enabled": False})
    async with Client(mcp) as c:
        await c.call_tool("Process", {"mode": "kill", "pid": 1})
    assert platform.asked == []


async def test_dialog_error_means_deny(tmp_path):
    mcp, _, platform, calls, _ = make(tmp_path)

    def boom(*a):
        raise RuntimeError("no desktop")

    platform.confirm_dialog = boom
    async with Client(mcp) as c:
        with pytest.raises(Exception):
            await c.call_tool("Process", {"mode": "kill", "pid": 1})
    assert calls == []


async def test_kill_switch_flag_file(tmp_path):
    mcp, _, _, calls, log = make(tmp_path)
    (tmp_path / "KILL").write_text("")
    async with Client(mcp) as c:
        with pytest.raises(Exception, match="safety_killswitch"):
            await c.call_tool("Click", {"loc": [5, 5]})
        os.remove(tmp_path / "KILL")
        await c.call_tool("Click", {"loc": [5, 5]})
    assert calls == [[5, 5]]


async def test_kill_switch_hotkey_latches(tmp_path):
    mcp, gate, _, calls, _ = make(tmp_path)
    gate.kill_switch.engage()
    async with Client(mcp) as c:
        with pytest.raises(Exception, match="restart"):
            await c.call_tool("Click", {"loc": [5, 5]})
    assert calls == []


async def test_kill_switch_engaged_during_dialog(tmp_path):
    mcp, gate, platform, calls, _ = make(tmp_path)

    def approve_then_kill(*a):
        gate.kill_switch.engage()
        return True

    platform.confirm_dialog = approve_then_kill
    async with Client(mcp) as c:
        with pytest.raises(Exception, match="safety_killswitch"):
            await c.call_tool("Process", {"mode": "kill", "pid": 1})
    assert calls == []


async def test_redaction_and_screenshot(tmp_path):
    mcp, _, platform, _, log = make(
        tmp_path, audit={"screenshot": True, "screenshot_dir": str(tmp_path / "s")}
    )
    async with Client(mcp) as c:
        await c.call_tool("Type", {"text": "my secret password"})
    entry = audit_lines(log)[0]
    assert "secret" not in json.dumps(entry) and entry["arguments"]["text"].startswith("<redacted")
    assert entry["screenshot"] == platform.shots[0] and platform.shots[0].endswith("-Type.png")


async def test_safety_disabled_passes_everything(tmp_path):
    mcp, _, _, calls, log = make(tmp_path, enabled=False)
    async with Client(mcp) as c:
        await c.call_tool("Click", {"loc": [500, 5]})
    assert calls == [[500, 5]] and not log.exists()


async def test_tool_error_audited(tmp_path):
    mcp, _, _, _, log = make(tmp_path)

    @mcp.tool(name="Scroll")
    def scroll() -> str:
        raise ValueError("bad")

    async with Client(mcp) as c:
        with pytest.raises(Exception):
            await c.call_tool("Scroll", {})
    assert audit_lines(log)[0]["result"] == "error"


async def test_pending_confirmation_blocks_other_calls(tmp_path):
    mcp, gate, _, calls, _ = make(tmp_path)
    await gate._confirm_lock.acquire()
    try:
        async with Client(mcp) as c:
            with pytest.raises(Exception, match="pending"):
                await c.call_tool("Click", {"loc": [5, 5]})
    finally:
        gate._confirm_lock.release()
    assert calls == []


def test_parse_hotkey():
    assert parse_hotkey("ctrl+alt+shift+f12") == (0x2 | 0x1 | 0x4, 0x7B)
    assert parse_hotkey("ctrl+alt+shift+k") == (0x2 | 0x1 | 0x4, 0x4B)  # Default.
    assert parse_hotkey("win+k") == (0x8, ord("K"))
    for bad in ("f12", "ctrl+", "hyper+k", "ctrl+enterx"):
        with pytest.raises(ValueError):
            parse_hotkey(bad)


def test_killswitch_hotkey_factory_failure_is_non_fatal(tmp_path):
    cfg = parse_safety_config({"kill_switch": {"flag_file": str(tmp_path / "K")}}).kill_switch

    def factory(spec, cb):
        raise OSError("hotkey taken")

    ks = KillSwitch(cfg, factory)
    ks.start()
    assert ks.engaged() is None
    (tmp_path / "K").write_text("")
    assert "flag file" in ks.engaged()


async def test_gate_runs_before_inner_middleware(tmp_path):
    from fastmcp.server.middleware import Middleware

    mcp, _, _, calls, _ = make(tmp_path)
    seen = []

    class Inner(Middleware):
        async def on_call_tool(self, context, call_next):
            seen.append(context.message.name)
            return await call_next(context)

    mcp.add_middleware(Inner())
    async with Client(mcp) as c:
        with pytest.raises(Exception, match="safety_denied"):
            await c.call_tool("Click", {"loc": [500, 5]})
        await c.call_tool("Click", {"loc": [5, 5]})
    assert seen == ["Click"]


AUDIT_KEYS = {
    "ts",
    "tool",
    "arguments",
    "decision",
    "reason",
    "result",
    "error",
    "duration_ms",
    "screenshot",
}


async def test_every_audit_row_has_the_same_fields(tmp_path):
    mcp, gate, _, _, log = make(tmp_path, answer=False)
    async with Client(mcp) as c:
        await c.call_tool("Click", {"loc": [5, 5]})  # allowed
        with pytest.raises(Exception, match="safety_denied"):
            await c.call_tool("Click", {"loc": [500, 5]})
        with pytest.raises(Exception, match="safety_declined"):
            await c.call_tool("Process", {"mode": "kill", "pid": 1})
        (tmp_path / "KILL").write_text("")
        with pytest.raises(Exception, match="safety_killswitch"):
            await c.call_tool("Click", {"loc": [5, 5]})
    rows = audit_lines(log)
    assert [r["decision"] for r in rows] == ["allowed", "denied", "declined", "killswitch"]
    for row in rows:
        assert set(row) == AUDIT_KEYS
        assert isinstance(row["duration_ms"], int) and row["duration_ms"] >= 0
    assert rows[0]["result"] == "ok" and rows[0]["reason"] is None
    for row in rows[1:]:
        assert row["result"] is None and row["error"] is None and row["reason"]


async def test_launch_denied_logs_one_line_unexpected_error_keeps_traceback(tmp_path, caplog):
    import logging

    from windows_mcp.safety.launch import LaunchDenied

    mcp, _, _, _, log = make(tmp_path)

    @mcp.tool(name="App")
    def app(mode: str) -> str:
        if mode == "launch":
            raise LaunchDenied("notepad++.exe is not in the allowlist")
        if mode == "path":
            from windows_mcp.desktop.app_launch import direct_launch_path

            direct_launch_path("notepad_ok.lnk")
        raise RuntimeError("boom")

    caplog.set_level(logging.DEBUG)
    async with Client(mcp) as c:
        with pytest.raises(Exception, match="safety_denied"):
            await c.call_tool("App", {"mode": "launch"})
    denied = [
        r for r in caplog.records if "allowlist" in r.getMessage() or "'App'" in r.getMessage()
    ]
    assert denied, "expected a log line for the refusal"
    assert all(not r.exc_info for r in denied)
    assert all(r.levelno <= logging.WARNING for r in denied)
    assert any("notepad++.exe is not in the allowlist" in r.getMessage() for r in denied)
    assert audit_lines(log)[-1]["decision"] == "denied"

    # An invalid launch path: isError=true with its message, audit error, no traceback.
    caplog.clear()
    async with Client(mcp) as c:
        result = await c.call_tool("App", {"mode": "path"}, raise_on_error=False)
    assert result.is_error is True
    assert "must be an absolute path" in result.content[0].text
    assert audit_lines(log)[-1]["result"] == "error"
    assert caplog.records, "expected a log line for the path error"
    assert all(not r.exc_info for r in caplog.records)
    assert all(r.levelno <= logging.WARNING for r in caplog.records)

    caplog.clear()
    async with Client(mcp) as c:
        with pytest.raises(Exception):
            await c.call_tool("App", {"mode": "switch"})
    assert any(r.exc_info and r.exc_info[0] is RuntimeError for r in caplog.records)


@pytest.mark.parametrize(
    "bad, message",
    [
        ("notepad_ok.lnk", "must be an absolute path"),
        ("missing", "does not exist"),
        ("readme", ".exe, .com or .lnk"),
    ],
)
async def test_app_path_validation_error_is_tool_error_and_audited(tmp_path, bad, message):
    from windows_mcp.desktop.app_launch import direct_launch_path

    (tmp_path / "readme.txt").write_text("")
    names = {
        "missing": str(tmp_path / "missing.lnk"),
        "readme": str(tmp_path / "readme.txt"),
    }
    mcp, _, _, _, log = make(tmp_path, apps={"allowed": ["notepad"]})

    @mcp.tool(name="App")
    def app(mode: str, name: str) -> str:
        # Same contract as Desktop.launch_app: a bad path raises, it is not a text result.
        direct_launch_path(name)
        return "launched"

    async with Client(mcp) as c:
        result = await c.call_tool(
            "App", {"mode": "launch", "name": names.get(bad, bad)}, raise_on_error=False
        )
    assert result.is_error is True
    assert message in result.content[0].text
    entry = audit_lines(log)[-1]
    assert entry["decision"] == "allowed" and entry["result"] == "error"
    assert message in entry["error"]
