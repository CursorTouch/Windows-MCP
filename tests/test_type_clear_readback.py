"""Type's clear must not send Ctrl+A, and Type says what the field reads back.

Ctrl+A is an application shortcut in many programs: in list views it often means
"mark every record", so a clear aimed at a search box ticked every row of the list
behind it. The clear now selects with Home and Shift+End (Ctrl+Home and
Ctrl+Shift+End in multi-line fields) and deletes with Backspace. After typing, the
field is read back through UIA ValuePattern or, for native Win32 Edit controls that
expose no pattern, WM_GETTEXT; a message box that steals focus is reported.
"""

import asyncio
from collections.abc import Callable

import pytest

from windows_mcp.desktop import service
from windows_mcp.desktop.service import Desktop
from windows_mcp.tools.input import register

SINGLE_MONITOR = (0, 0, 1920, 1080)
ES_MULTILINE = 0x0004
ES_PASSWORD = 0x0020


class FakeValuePattern:
    def __init__(self, field: "FakeField", read_only: bool = False) -> None:
        self.field = field
        self.IsReadOnly = read_only
        self.set_calls: list[str] = []

    @property
    def Value(self) -> str:
        return self.field.text

    def SetValue(self, value: str, waitTime: float = 0) -> bool:
        self.set_calls.append(value)
        self.field.text = value
        return True


class FakeField:
    """A focused element whose text follows the keys the test sends."""

    def __init__(
        self,
        text: str = "",
        *,
        control_type: int = service.uia.ControlType.EditControl,
        type_name: str = "EditControl",
        name: str = "",
        class_name: str = "",
        handle: int = 0,
        style: int = 0,
        value_pattern: bool = True,
        read_only: bool = False,
        password: bool = False,
        backspace_clears: bool = True,
    ) -> None:
        self.text = text
        self.ControlType = control_type
        self.ControlTypeName = type_name
        self.Name = name
        self.ClassName = class_name
        self.NativeWindowHandle = handle
        self.style = style
        self.IsPassword = password
        self.backspace_clears = backspace_clears
        self.pattern = FakeValuePattern(self, read_only) if value_pattern else None

    def GetPattern(self, pattern_id: int):
        if pattern_id == service.uia.PatternId.ValuePattern:
            return self.pattern
        return None


@pytest.fixture
def desktop(monkeypatch: pytest.MonkeyPatch) -> Desktop:
    instance = Desktop.__new__(Desktop)
    instance.desktop_state = None
    monkeypatch.setattr(service, "sleep", lambda seconds: None)
    monkeypatch.setattr(service.uia, "GetVirtualScreenRect", lambda: SINGLE_MONITOR)
    for name in ("Click", "RightClick", "MiddleClick", "SetCursorPos"):
        monkeypatch.setattr(service.uia, name, lambda *args, **kwargs: None)
    return instance


@pytest.fixture
def focus(monkeypatch: pytest.MonkeyPatch) -> Callable[..., list[str]]:
    """Point keyboard focus at fake fields; returns the keys sent."""

    def install(
        field: FakeField,
        after_typing: FakeField | None = None,
        at_click: FakeField | None = None,
    ) -> list[str]:
        """at_click is the element under the click; by default the focused one."""
        keys: list[str] = []
        state = {"focused": field}

        def send_keys(key: str, *args: object, **kwargs: object) -> None:
            keys.append(key)
            target = state["focused"]
            if key == "{Back}":
                if target.backspace_clears:
                    target.text = ""
            elif not key.startswith("{"):
                target.text += key
                if after_typing is not None:
                    state["focused"] = after_typing

        monkeypatch.setattr(service.uia, "SendKeys", send_keys)
        monkeypatch.setattr(service.uia, "GetFocusedControl", lambda: state["focused"])
        monkeypatch.setattr(service.uia, "ControlsAreSame", lambda a, b: a is b)
        monkeypatch.setattr(
            service.uia, "ControlFromPoint", lambda x, y: at_click or state["focused"]
        )
        monkeypatch.setattr(service.uia, "GetWindowLong", lambda handle, index: field.style)
        monkeypatch.setattr(service.uia, "GetEditText", lambda handle: field.text)
        return keys

    return install


def test_clear_never_sends_ctrl_a(desktop: Desktop, focus: Callable[..., list[str]]) -> None:
    keys = focus(FakeField("old"))

    desktop.type(loc=(100, 100), text="EF", clear=True)

    assert "{Ctrl}a" not in keys
    assert keys[:3] == ["{Home}", "{Shift}{End}", "{Back}"]


def test_clear_sends_no_keys_to_a_grid_and_warns(
    desktop: Desktop, focus: Callable[..., list[str]]
) -> None:
    # A click that lands on a list or grid (e.g. a lookup popup that opened over
    # the field) must not get Home/Shift+End/Backspace: they move the selection.
    grid = FakeField(
        control_type=service.uia.ControlType.DataGridControl,
        type_name="DataGridControl",
        name="Grid",
        value_pattern=False,
    )
    keys = focus(grid)

    note = desktop.type(loc=(100, 100), text="640000014", clear=True)

    assert "{Home}" not in keys and "{Back}" not in keys and "{Ctrl}a" not in keys
    assert note.startswith(
        "Warning: did not clear: the focused element is DataGridControl 'Grid', not a text field."
    )


def test_append_into_a_non_text_element_says_it_cannot_read_back(
    desktop: Desktop, focus: Callable[..., list[str]]
) -> None:
    focus(FakeField(type_name="PaneControl", control_type=50033, value_pattern=False))

    note = desktop.type(loc=(100, 100), text="x")

    assert note == "Could not read the text back: the focused element is PaneControl."


def test_clear_uses_ctrl_home_and_ctrl_shift_end_in_a_document(
    desktop: Desktop, focus: Callable[..., list[str]]
) -> None:
    field = FakeField(
        "line 1\nline 2",
        control_type=service.uia.ControlType.DocumentControl,
        type_name="DocumentControl",
    )
    keys = focus(field)

    desktop.type(loc=(100, 100), text="EF", clear=True)

    assert keys[:3] == ["{Ctrl}{Home}", "{Ctrl}{Shift}{End}", "{Back}"]


def test_clear_treats_a_multiline_win32_edit_as_multi_line(
    desktop: Desktop, focus: Callable[..., list[str]]
) -> None:
    field = FakeField(
        "a\nb",
        control_type=service.uia.ControlType.PaneControl,
        type_name="PaneControl",
        class_name="Edit",
        handle=0x1234,
        style=ES_MULTILINE,
        value_pattern=False,
    )
    keys = focus(field)

    desktop.type(loc=(100, 100), text="EF", clear=True)

    assert keys[:3] == ["{Ctrl}{Home}", "{Ctrl}{Shift}{End}", "{Back}"]


@pytest.mark.parametrize(
    ("caret_position", "key"), [("start", "{Ctrl}{Home}"), ("end", "{Ctrl}{End}")]
)
@pytest.mark.parametrize("kind", ["document", "win32-multiline-edit"])
def test_caret_position_reaches_the_ends_of_a_multi_line_field(
    desktop: Desktop, focus: Callable[..., list[str]], caret_position: str, key: str, kind: str
) -> None:
    # Home and End stop at the ends of the clicked line, so "end" typed into
    # the middle of a document instead of after its last line.
    if kind == "document":
        field = FakeField(
            "line 1\nline 2",
            control_type=service.uia.ControlType.DocumentControl,
            type_name="DocumentControl",
        )
    else:
        field = FakeField("line 1\r\nline 2", class_name="Edit", handle=0x1234, style=ES_MULTILINE)
    keys = focus(field)

    desktop.type(loc=(100, 100), text="EF", caret_position=caret_position)

    assert keys == [key, "E", "F"]


def test_caret_position_ignores_a_multi_line_field_away_from_the_click(
    desktop: Desktop, focus: Callable[..., list[str]]
) -> None:
    # UIA can still report the element focused before the click, such as the
    # web page around a grid. Ctrl+End must not go to the grid because that
    # page is multi-line.
    page = FakeField(
        "line 1\nline 2",
        control_type=service.uia.ControlType.DocumentControl,
        type_name="DocumentControl",
    )
    grid = FakeField(control_type=service.uia.ControlType.DataGridControl, type_name="DataGrid")
    keys = focus(page, at_click=grid)

    desktop.type(loc=(100, 100), text="EF", caret_position="end")

    assert keys[0] == "{End}"


@pytest.mark.parametrize(("caret_position", "key"), [("start", "{Home}"), ("end", "{End}")])
def test_caret_position_uses_home_and_end_in_a_single_line_field(
    desktop: Desktop, focus: Callable[..., list[str]], caret_position: str, key: str
) -> None:
    keys = focus(FakeField("abc"))

    desktop.type(loc=(100, 100), text="EF", caret_position=caret_position)

    assert keys == [key, "E", "F"]


def test_read_back_through_wm_gettext_for_a_win32_edit_without_patterns(
    desktop: Desktop, focus: Callable[..., list[str]]
) -> None:
    # Older Win32 apps expose their Edit windows to UIA as a bare Pane.
    field = FakeField(
        "2026-10-16",
        control_type=service.uia.ControlType.PaneControl,
        type_name="PaneControl",
        class_name="Edit",
        handle=0x1234,
        value_pattern=False,
    )
    focus(field)

    note = desktop.type(loc=(100, 100), text="2026-11-03", clear=True)

    assert "Cleared PaneControl with Home, Shift+End, Backspace." in note
    assert "Field reads back '2026-11-03'." in note
    assert "Warning" not in note


def test_falls_back_to_value_pattern_when_the_keys_leave_text(
    desktop: Desktop, focus: Callable[..., list[str]]
) -> None:
    field = FakeField("old", backspace_clears=False)
    focus(field)

    note = desktop.type(loc=(100, 100), text="EF", clear=True)

    assert field.pattern.set_calls == [""]
    assert "then UIA ValuePattern" in note
    assert "Field reads back 'EF'." in note


def test_warns_when_the_field_cannot_be_emptied(
    desktop: Desktop, focus: Callable[..., list[str]]
) -> None:
    focus(FakeField("old", backspace_clears=False, read_only=True))

    note = desktop.type(loc=(100, 100), text="EF", clear=True)

    assert "Warning: EditControl still held text after clearing" in note
    assert "Warning: the field reads back 'oldEF', not the typed text." in note


def test_warns_when_a_message_box_takes_focus_while_typing(
    desktop: Desktop, focus: Callable[..., list[str]]
) -> None:
    field = FakeField("")
    button = FakeField(type_name="ButtonControl", name="OK", value_pattern=False)
    focus(field, after_typing=button)

    note = desktop.type(loc=(100, 100), text="640000014", clear=True)

    assert "Warning: focus moved to ButtonControl 'OK' while typing" in note


def test_never_reads_back_a_password_field(
    desktop: Desktop, focus: Callable[..., list[str]]
) -> None:
    focus(FakeField("", password=True))

    note = desktop.type(loc=(100, 100), text="secret", clear=True)

    assert "secret" not in note


def test_append_reads_back_without_clearing(
    desktop: Desktop, focus: Callable[..., list[str]]
) -> None:
    keys = focus(FakeField("ab"))

    note = desktop.type(loc=(100, 100), text="c")

    assert "{Back}" not in keys
    assert note == "Field reads back 'abc'."


def test_long_values_are_cut_in_the_answer(
    desktop: Desktop, focus: Callable[..., list[str]]
) -> None:
    focus(FakeField("x" * 500))

    note = desktop.type(loc=(100, 100), text="y")

    assert len(note) < 260
    assert note.endswith("…'.")


class FakeMCP:
    def __init__(self) -> None:
        self.tools: dict[str, Callable] = {}

    def tool(self, *, name: str, **kwargs: object) -> Callable:
        def decorator(func: Callable) -> Callable:
            self.tools[name] = func
            return func

        return decorator


class NotingDesktop:
    desktop_state = object()

    def type(self, **kwargs: object) -> str:
        return "Field reads back 'EF'."


def test_type_tool_appends_the_desktop_note() -> None:
    mcp = FakeMCP()
    register(mcp, get_desktop=lambda: NotingDesktop(), get_analytics=lambda: None)

    result = asyncio.run(mcp.tools["Type"](text="EF", loc=[100, 100], clear=True))

    assert result == "Typed EF at (100,100). Field reads back 'EF'."
