#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Inspect and manipulate the caret/selection of the currently focused Windows
text control through UI Automation.

The caret/selection is discovered via IUIAutomationTextPattern.GetSelection()
on the focused element, walking up to its ancestors when needed.
IUIAutomationTextPattern2.GetCaretRange() is intentionally not used; see
discovery.try_get_caret_on_element for the rationale.

Supported modes:
- get_info
- move_relative
- move_absolute
- select_relative
- select_absolute
- select_all
- collapse_selection

Important:
- IUIAutomationTextRange.Move() only moves a client-side range.
- Select() asks the provider to apply that range as the real caret/selection.
- Write operations verify the applied range by reading it back.
- Some providers expose TextPattern but do not support moving the real caret.
"""

from __future__ import annotations

from .errors import TextCursorError, TextCursorVerificationError
from .models import (
    CollapseSelectionAction,
    CursorAction,
    CursorSnapshot,
    CursorToolResult,
    GetInfoAction,
    MoveAbsoluteAction,
    MoveRelativeAction,
    SelectAbsoluteAction,
    SelectAllAction,
    SelectRelativeAction,
)
from .service import run_tool

__all__ = [
    "CollapseSelectionAction",
    "CursorAction",
    "CursorSnapshot",
    "CursorToolResult",
    "GetInfoAction",
    "MoveAbsoluteAction",
    "MoveRelativeAction",
    "SelectAbsoluteAction",
    "SelectAllAction",
    "SelectRelativeAction",
    "TextCursorError",
    "TextCursorVerificationError",
    "run_tool",
]
