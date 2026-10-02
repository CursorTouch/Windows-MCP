"""Orchestrate TextCursor actions on the server's main-thread STA.

Collaborators (find_caret_provider, make_snapshot, apply_write,
snapshot_position, run_get_info, run_write) are referenced by their
module-level names so that tests can substitute them with
monkeypatch.setattr on this module.
"""

from __future__ import annotations

import asyncio

from .errors import TextCursorVerificationError
from .models import CursorAction, CursorToolResult, GetInfoAction
from .discovery import find_caret_provider
from .operations import WriteAction, apply_write
from .snapshots import make_snapshot, snapshot_position


def run_get_info(action: GetInfoAction) -> CursorToolResult:
    """Read information about the focused caret or selection."""
    caret_info = find_caret_provider()
    snapshot = make_snapshot(caret_info, action.context_chars)

    return CursorToolResult(
        success=True,
        mode=action.mode,
        message="Caret information acquired.",
        after=snapshot,
        warnings=snapshot.warnings,
    )


def run_write(action: WriteAction) -> CursorToolResult:
    """Apply a write action and report the resulting caret or selection."""
    caret_info = find_caret_provider()

    before = make_snapshot(caret_info, action.context_chars)
    verified, target = apply_write(action, caret_info)
    requested = action.model_dump(
        exclude={
            "delay",
            "context_chars",
            "verify",
        }
    )

    # Always reacquire the focused provider before reporting success or a
    # verification mismatch. The values returned by TextRange.Move describe
    # only the client-side target; `actual` must come from the real provider.
    refreshed = find_caret_provider()
    after = make_snapshot(refreshed, action.context_chars)
    actual = snapshot_position(after)

    if verified is False:
        raise TextCursorVerificationError(
            "The provider accepted the operation, but read-back verification "
            "showed that the real caret/selection did not match the calculated "
            f"target. Requested: {requested}; target: {target}; actual: {actual}."
        )

    warnings = list(dict.fromkeys([*before.warnings, *after.warnings]))

    return CursorToolResult(
        success=True,
        mode=action.mode,
        message="Operation applied.",
        verified=verified,
        requested=requested,
        target=target,
        actual=actual,
        before=before,
        after=after,
        warnings=warnings,
    )


async def run_tool(action: CursorAction) -> CursorToolResult:
    """
    Inspect or manipulate the focused Windows text control through UIA.
    Modes:
    - get_info
    - move_relative
    - move_absolute
    - select_relative
    - select_absolute
    - select_all
    - collapse_selection
    Every mode accepts `delay`, expressed in seconds. The delay occurs before
    the focused UIA element is located, so the caller can focus the target
    control during that interval.
    Absolute move/select inputs and returned offsets both use provider-defined
    UIA TextUnit_Character steps from DocumentRange start. Returned offsets can
    be passed directly to absolute move/select actions.
    """
    if action.delay > 0:
        await asyncio.sleep(action.delay)

    if isinstance(action, GetInfoAction):
        return run_get_info(action)
    return run_write(action)
