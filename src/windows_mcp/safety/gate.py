"""SafetyGate: the FastMCP middleware that enforces the safety policy on every call."""

from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware, MiddlewareContext

from windows_mcp.safety.apps import is_allowed, normalize_app, parse_point, parse_points
from windows_mcp.safety.audit import AuditLog, redact
from windows_mcp.safety.config import SafetyConfig
from windows_mcp.safety.killswitch import KillSwitch
from windows_mcp.safety.launch import (
    LaunchGuard,
    find_launch_denied,
    resolve_command,
    set_launch_guard,
)
from windows_mcp.safety.powershell import check_powershell, cmdlet_of

logger = logging.getLogger(__name__)

# Tools that send input to whatever window is under the pointer or focused.
INTERACTION_TOOLS = frozenset(
    {"Click", "Type", "Scroll", "Move", "Shortcut", "MultiSelect", "MultiEdit"}
)


@dataclass
class Decision:
    allowed: bool
    reason: str = ""
    confirm: str | None = None  # Question for the local dialog, when risky.


def _truthy(value: object) -> bool:
    return value is True or (isinstance(value, str) and value.lower() == "true")


def _norm_shortcut(value: str) -> str:
    return "+".join(p.strip().lower() for p in value.split("+") if p.strip())


class SafetyPolicy:
    """Pure decision logic. *platform* supplies the Windows lookups (see win32.py)."""

    def __init__(self, cfg: SafetyConfig, platform: Any) -> None:
        self.cfg = cfg
        self.platform = platform
        self.resolve_command = getattr(platform, "resolve_command", resolve_command)

    # -- process resolution -------------------------------------------------
    def _names(self, pids: list[int | None]) -> list[str | None]:
        return [self.platform.process_name(pid) for pid in pids]

    def _interaction_pids(self, name: str, args: dict) -> list[int | None]:
        points: list[tuple[int, int]] = []
        for key in ("loc", "from_loc"):
            if args.get(key) is not None:
                point = parse_point(args[key])
                if point is None:
                    return [None]  # Unparsable target: fail closed.
                points.append(point)
        if args.get("locs") is not None:
            parsed = parse_points(args["locs"])
            if parsed is None:
                return [None]
            points.extend(parsed)
        pids = [self.platform.pid_at_point(x, y) for x, y in points]
        # Keyboard input, label targets and the pointer start all reach the foreground window.
        if not points or args.get("label") is not None or args.get("labels") is not None:
            pids.append(self.platform.foreground_pid())
        return pids

    def _check_targets(self, pids: list[int | None], what: str) -> Decision | None:
        own = getattr(self.platform, "own_pid", None)
        if own is not None and own in pids:
            return Decision(
                False, f"{what} targets the windows-mcp process itself (confirmation dialogs)."
            )
        if not self.cfg.apps.enforce:
            return None
        for pid, proc in zip(pids, self._names(pids)):
            if not is_allowed(proc, self.cfg.apps.allowed):
                label = proc or f"unknown process (pid {pid})"
                return Decision(
                    False, f"{what} targets {label}, which is not in safety.apps.allowed."
                )
        return None

    # -- risk classification ------------------------------------------------
    def _looks_like_installer(self, *names: str | None) -> bool:
        for raw in names:
            if not raw:
                continue
            base = raw.strip('"').replace("/", "\\").split("\\")[-1].lower()
            for pattern in self.cfg.confirm.installer_patterns:
                p = pattern.lower()
                if fnmatch.fnmatch(base, p) or fnmatch.fnmatch(normalize_app(base), p):
                    return True
        return False

    def _elevation(self, names: list[str | None]) -> bool:
        return any(is_allowed(n, self.cfg.confirm.elevation_processes) for n in names if n)

    # -- main entry ---------------------------------------------------------
    def evaluate(self, name: str, args: dict | None) -> Decision:
        args = args or {}
        mode = args.get("mode")

        if name == "PowerShell":
            reason = check_powershell(args.get("command", ""), self.cfg.powershell)
            if reason:
                return Decision(False, reason)
            verb = cmdlet_of(args.get("command", "")).split("-")[0].lower()
            if verb in {v.lower() for v in self.cfg.confirm.risky_cmdlet_verbs} or (
                self.cfg.powershell.mode == "unrestricted"
            ):
                return Decision(True, confirm=f"Run PowerShell command?\n\n{args.get('command')}")
            return Decision(True)

        if name in INTERACTION_TOOLS:
            pids = self._interaction_pids(name, args)
            denied = self._check_targets(pids, name)
            if denied:
                return denied
            names = self._names(pids)
            if self._elevation(names):
                return Decision(
                    True,
                    confirm=f"{name} on an elevation/UAC prompt ({', '.join(filter(None, names))}). Allow?",
                )
            if name == "Shortcut":
                combo = _norm_shortcut(str(args.get("shortcut", "")))
                risky = {_norm_shortcut(s) for s in self.cfg.confirm.risky_shortcuts}
                if combo in risky:
                    target = ", ".join(filter(None, names)) or "the focused window"
                    return Decision(
                        True, confirm=f"Press {combo} in {target}? (may close or delete)"
                    )
            return Decision(True)

        if name == "App":
            mode = mode or "launch"
            if mode in ("launch", "launch_executable"):
                target = args.get("executable") if mode == "launch_executable" else args.get("name")
                if not target:
                    return Decision(True)  # The tool itself rejects missing arguments.
                # mode "launch": the Start Menu match is checked on its resolved executable
                # by LaunchGuard inside the launch itself (the name alone proves nothing).
                if mode == "launch_executable" and self.cfg.apps.enforce:
                    resolved = self.resolve_command(str(target))
                    if resolved is None:
                        return Decision(
                            False, f"Executable {target!r} not found as a path or on PATH."
                        )
                    if resolved.lower().endswith(".lnk"):
                        # A shortcut runs its target, so the target is what gets checked.
                        resolve_target = getattr(self.platform, "resolve_app_target", None)
                        target_exe = resolve_target(resolved) if resolve_target else None
                        if not target_exe:
                            return Decision(False, f"Shortcut {resolved} has no resolvable target.")
                        if not is_allowed(target_exe, self.cfg.apps.allowed):
                            return Decision(
                                False,
                                f"Shortcut {resolved} runs {target_exe}, which is not in "
                                "safety.apps.allowed.",
                            )
                    elif not is_allowed(resolved, self.cfg.apps.allowed):
                        return Decision(
                            False, f"Executable {resolved} is not in safety.apps.allowed."
                        )
                extra = args.get("args")
                arg_list = extra if isinstance(extra, list) else [str(extra)] if extra else []
                if self._looks_like_installer(str(target)) or any(
                    a.lower() in ("/i", "/package", "-verb", "runas") for a in arg_list
                ):
                    return Decision(
                        True,
                        confirm=f"Launch possible installer/elevation: {target} {' '.join(arg_list)}?",
                    )
                return Decision(True)
            if mode in ("switch", "resize"):
                title = args.get("name")
                pids = (
                    self.platform.pids_for_window_title(title)
                    if title
                    else [self.platform.foreground_pid()]
                )
                if not pids:
                    pids = [None]
                return self._check_targets(pids, f"App {mode}") or Decision(True)
            return Decision(True)

        if name == "Process":
            if mode != "kill":
                return Decision(True)
            if args.get("pid") is not None:
                try:
                    pid = int(args["pid"])
                except TypeError, ValueError:
                    return Decision(False, "Invalid pid.")
                denied = self._check_targets([pid], "Process kill")
                proc = self.platform.process_name(pid)
            else:
                proc = args.get("name")
                denied = None
                if (
                    getattr(self.platform, "own_pid", None) is not None
                    and proc
                    and is_allowed(proc, [self.platform.process_name(self.platform.own_pid) or ""])
                ):
                    denied = Decision(
                        False, "Process kill would target the windows-mcp host process."
                    )
                elif self.cfg.apps.enforce and not is_allowed(proc, self.cfg.apps.allowed):
                    denied = Decision(
                        False,
                        f"Process kill targets {proc!r}, which is not in safety.apps.allowed.",
                    )
            if denied:
                return denied
            return Decision(True, confirm=f"Close/kill process {proc} (pid {args.get('pid')})?")

        if name == "FileSystem" and mode in ("delete", "move"):
            return Decision(
                True,
                confirm=f"FileSystem {mode}: {args.get('path')}"
                + (f" -> {args.get('destination')}" if args.get("destination") else "")
                + "?",
            )
        if name == "FileSystem" and mode in ("write", "copy") and _truthy(args.get("overwrite")):
            return Decision(
                True,
                confirm=f"FileSystem {mode} overwriting {args.get('destination') or args.get('path')}?",
            )
        if name == "Registry" and mode in ("set", "delete"):
            return Decision(
                True, confirm=f"Registry {mode}: {args.get('path')} {args.get('name') or ''}?"
            )
        return Decision(True)


class SafetyGate(Middleware):
    """Kill switch -> policy -> local confirmation -> audit, around every tool call."""

    def __init__(
        self,
        cfg: SafetyConfig,
        platform: Any,
        *,
        kill_switch: KillSwitch | None = None,
        audit: AuditLog | None = None,
        input_monitor: Any = None,
    ) -> None:
        self.cfg = cfg
        self.platform = platform
        self.policy = SafetyPolicy(cfg, platform)
        self.kill_switch = kill_switch or KillSwitch(
            cfg.kill_switch, getattr(platform, "hotkey_listener", None)
        )
        self.audit = audit or AuditLog(cfg.audit, getattr(platform, "take_screenshot", None))
        self._confirm_lock = asyncio.Lock()
        # Desktop control monitor: told about dialogs so the answer is not "user activity".
        self.input_monitor = input_monitor
        # The App tool's Start Menu launch consults this guard (fail closed on resolution).
        enforce = cfg.enabled and cfg.apps.enforce
        set_launch_guard(LaunchGuard(cfg, platform) if enforce else None)

    def start(self) -> None:
        self.kill_switch.start()

    def stop(self) -> None:
        self.kill_switch.stop()

    def _refuse(self, name: str, args: dict, decision: str, reason: str, start: float) -> ToolError:
        self.audit.write(self._record(name, args, start, decision=decision, reason=reason))
        return ToolError(json.dumps({"code": "safety_" + decision, "reason": reason}))

    def _record(
        self,
        name: str,
        args: dict,
        start: float,
        *,
        decision: str,
        reason: str | None = None,
        result: str | None = None,
        error: str | None = None,
        screenshot: str | None = None,
    ) -> dict:
        """One audit row. Every row has the same keys; unused ones are null."""
        return {
            "tool": name,
            "arguments": redact(args, self.cfg.audit.redact_keys, self.cfg.audit.max_value_length),
            "decision": decision,
            "reason": reason,
            "result": result,
            "error": error,
            "duration_ms": round((time.monotonic() - start) * 1000),
            "screenshot": screenshot,
        }

    async def _ask(self, question: str) -> bool:
        if not self.cfg.confirm.enabled:
            return True
        monitor = self.input_monitor
        begin = await asyncio.to_thread(monitor.begin_confirmation) if monitor else None
        approved = bool(
            await asyncio.to_thread(
                self.platform.confirm_dialog,
                "Windows-MCP: confirm AI action",
                question + "\n\nNo answer within "
                f"{int(self.cfg.confirm.timeout_seconds)} s = DENY.",
                self.cfg.confirm.timeout_seconds,
            )
        )
        if approved and begin is not None:
            try:
                await asyncio.to_thread(monitor.end_confirmation, *begin)
            except Exception:  # Never turn an approval into a crash; control stays strict.
                logger.exception("Could not excuse confirmation-dialog input")
        return approved

    async def on_call_tool(self, context: MiddlewareContext, call_next: Callable) -> Any:
        name = context.message.name
        args = dict(getattr(context.message, "arguments", None) or {})
        if not self.cfg.enabled:
            return await call_next(context)
        start = time.monotonic()

        reason = self.kill_switch.engaged()
        if reason:
            raise self._refuse(name, args, "killswitch", reason, start)
        if self._confirm_lock.locked():
            # Nothing may run while a dialog is open, or the agent could click "Yes" itself.
            raise self._refuse(
                name, args, "denied", "A safety confirmation is pending; retry later.", start
            )

        try:
            decision = await asyncio.to_thread(self.policy.evaluate, name, args)
        except Exception as exc:
            logger.exception("Safety policy evaluation failed; denying")
            decision = Decision(False, f"Safety policy error: {exc}")
        if not decision.allowed:
            raise self._refuse(name, args, "denied", decision.reason, start)

        outcome = "allowed"
        if decision.confirm is not None and self.cfg.confirm.enabled:
            async with self._confirm_lock:
                approved = await self._ask(decision.confirm)
            if not approved:
                raise self._refuse(
                    name,
                    args,
                    "declined",
                    "The user did not approve on the PC (No or timeout).",
                    start,
                )
            outcome = "confirmed"
            reason = self.kill_switch.engaged()  # The switch may be hit while the dialog is open.
            if reason:
                raise self._refuse(name, args, "killswitch", reason, start)

        shot = (
            await asyncio.to_thread(self.audit.screenshot, name)
            if self.cfg.audit.screenshot
            else None
        )
        try:
            result = await call_next(context)
        except Exception as exc:
            denied = find_launch_denied(exc)
            if denied is not None:
                # LaunchGuard refused inside the launch (resolved target or started process).
                raise self._refuse(name, args, "denied", denied.reason, start) from exc
            self.audit.write(
                self._record(
                    name,
                    args,
                    start,
                    decision=outcome,
                    result="error",
                    error=str(exc)[:500],
                    screenshot=shot,
                )
            )
            raise
        self.audit.write(
            self._record(name, args, start, decision=outcome, result="ok", screenshot=shot)
        )
        return result


def build_safety_gate(cfg: SafetyConfig) -> SafetyGate:
    """Create the gate with the real Windows platform backends."""
    from windows_mcp.desktop.control import get_controller
    from windows_mcp.safety.win32 import WindowsPlatform

    return SafetyGate(cfg, WindowsPlatform(), input_monitor=get_controller())
