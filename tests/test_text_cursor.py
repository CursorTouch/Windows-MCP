import asyncio
from types import SimpleNamespace

import pytest
from comtypes import COMError
from pydantic import ValidationError

import windows_mcp.text_cursor as text_cursor
from windows_mcp.text_cursor import ranges, service, snapshots
from windows_mcp.text_cursor.constants import MAX_SELECTED_TEXT_CHARS, MAX_TEXT_UNIT_MOVE
from windows_mcp.text_cursor.operations import WriteActionResult
from windows_mcp.text_cursor.uia import TextRange, TextRangeEndpoint, TextUnit
from windows_mcp.tools.text_cursor import _description


class FakeTextRange:
    """Minimal in-memory text range for offset calculations."""

    def __init__(self, start: int, end: int, document_length: int) -> None:
        self.start = start
        self.end = end
        self.document_length = document_length

    def clone(self) -> "FakeTextRange":
        return FakeTextRange(self.start, self.end, self.document_length)

    def collapse_range(self, *, to_end: bool) -> None:
        position = self.end if to_end else self.start
        self.start = position
        self.end = position

    def move(self, unit: TextUnit, count: int) -> int:
        assert unit is TextUnit.Character
        assert self.start == self.end

        target = min(max(self.start + count, 0), self.document_length)
        moved = target - self.start
        self.start = target
        self.end = target
        return moved


class FakeTextPattern:
    def __init__(self, document_length: int) -> None:
        self.document_length = document_length

    def document_range(self) -> FakeTextRange:
        return FakeTextRange(0, self.document_length, self.document_length)


@pytest.mark.parametrize(
    ("model", "values"),
    [
        (
            text_cursor.MoveRelativeAction,
            {"mode": "move_relative", "delta": MAX_TEXT_UNIT_MOVE + 1},
        ),
        (
            text_cursor.MoveRelativeAction,
            {"mode": "move_relative", "delta": -MAX_TEXT_UNIT_MOVE - 1},
        ),
        (
            text_cursor.MoveAbsoluteAction,
            {"mode": "move_absolute", "offset": MAX_TEXT_UNIT_MOVE + 1},
        ),
        (
            text_cursor.SelectRelativeAction,
            {
                "mode": "select_relative",
                "start_delta": -MAX_TEXT_UNIT_MOVE - 1,
                "end_delta": 0,
            },
        ),
        (
            text_cursor.SelectRelativeAction,
            {
                "mode": "select_relative",
                "start_delta": 0,
                "end_delta": MAX_TEXT_UNIT_MOVE + 1,
            },
        ),
        (
            text_cursor.SelectAbsoluteAction,
            {
                "mode": "select_absolute",
                "start": MAX_TEXT_UNIT_MOVE + 1,
                "end": MAX_TEXT_UNIT_MOVE + 1,
            },
        ),
    ],
)
def test_character_counts_must_fit_uia_int(model, values):
    with pytest.raises(ValidationError):
        model(**values)


def test_character_offset_round_trips_through_document_position():
    document_length = 100
    position = 37
    info = SimpleNamespace(
        text_range=FakeTextRange(position, position, document_length),
        text_pattern=FakeTextPattern(document_length),
    )

    offset = snapshots.endpoint_offset(info, TextRangeEndpoint.Start)
    target, actual = ranges.document_position(info, offset)

    assert offset == position
    assert actual == position
    assert target.start == position
    assert target.end == position


def test_selection_endpoint_offsets_use_character_units():
    info = SimpleNamespace(
        text_range=FakeTextRange(12, 34, 100),
    )

    assert snapshots.endpoint_offset(info, TextRangeEndpoint.Start) == 12
    assert snapshots.endpoint_offset(info, TextRangeEndpoint.End) == 34


def test_snapshot_fails_when_character_offset_is_unavailable(monkeypatch):
    monkeypatch.setattr(snapshots, "endpoint_offset", lambda *args, **kwargs: None)

    with pytest.raises(text_cursor.TextCursorError, match="TextUnit_Character offsets"):
        snapshots.make_snapshot(object(), context_chars=40)


def test_snapshot_contract_names_character_unit_fields():
    snapshot = text_cursor.CursorSnapshot(
        provider="fake",
        type="caret",
        caret_offset_units=7,
    )

    result = snapshot.model_dump()
    schema = text_cursor.CursorSnapshot.model_json_schema()

    assert result["caret_offset_units"] == 7
    assert "caret_offset" not in result
    assert "TextUnit_Character" in schema["properties"]["caret_offset_units"]["description"]


def test_tool_descriptions_use_character_units():
    assert "UTF-16" not in _description
    assert "passed directly to absolute move/select actions" in _description
    assert "UTF-16" not in (text_cursor.run_tool.__doc__ or "")


def test_text_range_get_text_normalizes_none_to_empty_text():
    text_range = TextRange(
        SimpleNamespace(GetText=lambda max_length: None),
    )

    assert text_range.get_text() == ""


def test_text_range_get_text_propagates_com_error():
    def fail_get_text(max_length):
        raise COMError(-2147467259, "provider unavailable", None)

    text_range = TextRange(
        SimpleNamespace(GetText=fail_get_text),
    )

    with pytest.raises(COMError, match="provider unavailable"):
        text_range.get_text()


@pytest.mark.asyncio
async def test_cancelled_delay_does_not_reach_com_worker(monkeypatch):
    sleep_started = asyncio.Event()
    execute_called = False

    async def blocking_sleep(delay: float) -> None:
        assert delay == 300
        sleep_started.set()
        await asyncio.Event().wait()

    def fake_execute(action) -> None:
        nonlocal execute_called
        execute_called = True

    monkeypatch.setattr(service.asyncio, "sleep", blocking_sleep)
    monkeypatch.setattr(service, "execute_sync", fake_execute)

    task = asyncio.create_task(
        text_cursor.run_tool(text_cursor.GetInfoAction(mode="get_info", delay=300))
    )
    await sleep_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert execute_called is False


def test_snapshot_warns_when_provider_returns_multiple_selections(monkeypatch):
    caret_info = SimpleNamespace(
        text_range=SimpleNamespace(
            get_text=lambda max_length=-1: "selected",
            text_before=lambda count: "before",
            text_after=lambda count: "after",
            bounding_rectangles=lambda: [],
        ),
        element=SimpleNamespace(get_name=lambda: "editor"),
        source="TextPattern.GetSelection",
        exact_caret=False,
        selection_count=3,
    )
    monkeypatch.setattr(
        snapshots,
        "endpoint_offset",
        lambda info, endpoint: 4 if endpoint is TextRangeEndpoint.Start else 12,
    )

    snapshot = snapshots.make_snapshot(caret_info, context_chars=40)

    assert snapshot.selection_start_units == 4
    assert snapshot.selection_end_units == 12
    assert any("3 disjoint selections" in warning for warning in snapshot.warnings)
    assert any("uses only the first selection" in warning for warning in snapshot.warnings)


def test_snapshot_truncates_long_selected_text_with_ellipsis(monkeypatch):
    limit = MAX_SELECTED_TEXT_CHARS
    # The wrapper is asked for limit + 1 chars; the provider returns that many,
    # which signals the real selection is longer than the limit.
    caret_info = SimpleNamespace(
        text_range=SimpleNamespace(
            get_text=lambda max_length=-1: "a" * max_length,
            text_before=lambda count: "",
            text_after=lambda count: "",
            bounding_rectangles=lambda: [],
        ),
        element=SimpleNamespace(get_name=lambda: "editor"),
        source="TextPattern.GetSelection",
        exact_caret=False,
        selection_count=1,
    )
    monkeypatch.setattr(
        snapshots,
        "endpoint_offset",
        lambda info, endpoint: 0 if endpoint is TextRangeEndpoint.Start else 10_000,
    )

    snapshot = snapshots.make_snapshot(caret_info, context_chars=40)

    assert len(snapshot.selected_text) == limit + 1  # limit chars + ellipsis
    assert snapshot.selected_text.endswith("…")
    assert snapshot.selected_text[:limit] == "a" * limit
    assert any("truncated" in warning for warning in snapshot.warnings)


def test_snapshot_keeps_selected_text_at_limit_untruncated(monkeypatch):
    limit = MAX_SELECTED_TEXT_CHARS
    # A selection exactly at the limit: the provider returns fewer than the
    # requested limit + 1 chars, so it must not be flagged as truncated.
    caret_info = SimpleNamespace(
        text_range=SimpleNamespace(
            get_text=lambda max_length=-1: "b" * limit,
            text_before=lambda count: "",
            text_after=lambda count: "",
            bounding_rectangles=lambda: [],
        ),
        element=SimpleNamespace(get_name=lambda: "editor"),
        source="TextPattern.GetSelection",
        exact_caret=False,
        selection_count=1,
    )
    monkeypatch.setattr(
        snapshots,
        "endpoint_offset",
        lambda info, endpoint: 0 if endpoint is TextRangeEndpoint.Start else limit,
    )

    snapshot = snapshots.make_snapshot(caret_info, context_chars=40)

    assert snapshot.selected_text == "b" * limit
    assert "…" not in snapshot.selected_text
    assert not any("truncated" in warning for warning in snapshot.warnings)


def test_snapshot_preserves_genuinely_empty_selected_text(monkeypatch):
    caret_info = SimpleNamespace(
        text_range=SimpleNamespace(
            get_text=lambda max_length=-1: "",
            bounding_rectangles=lambda: [],
        ),
        element=SimpleNamespace(get_name=lambda: "editor"),
        source="TextPattern.GetSelection",
        exact_caret=False,
        selection_count=1,
    )
    monkeypatch.setattr(
        snapshots,
        "endpoint_offset",
        lambda info, endpoint: 4 if endpoint is TextRangeEndpoint.Start else 12,
    )

    snapshot = snapshots.make_snapshot(
        caret_info,
        context_chars=40,
        include_context=False,
    )

    assert snapshot.selected_text == ""
    assert not any("reading selected_text" in warning for warning in snapshot.warnings)


def test_snapshot_omits_selected_text_and_warns_on_com_error(monkeypatch):
    def fail_get_text(max_length=-1):
        raise COMError(-2147467259, "provider unavailable", None)

    caret_info = SimpleNamespace(
        text_range=SimpleNamespace(
            get_text=fail_get_text,
            bounding_rectangles=lambda: [],
        ),
        element=SimpleNamespace(get_name=lambda: "editor"),
        source="TextPattern.GetSelection",
        exact_caret=False,
        selection_count=1,
    )
    monkeypatch.setattr(
        snapshots,
        "endpoint_offset",
        lambda info, endpoint: 4 if endpoint is TextRangeEndpoint.Start else 12,
    )

    snapshot = snapshots.make_snapshot(
        caret_info,
        context_chars=40,
        include_context=False,
    )

    assert snapshot.selected_text is None
    assert snapshot.selection_start_units == 4
    assert snapshot.selection_end_units == 12
    assert any("reading selected_text" in warning for warning in snapshot.warnings)


def test_snapshot_reads_context_fields_independently_on_com_error(monkeypatch):
    def fail_text_before(count):
        raise COMError(-2147467259, "provider unavailable", None)

    caret_info = SimpleNamespace(
        text_range=SimpleNamespace(
            text_before=fail_text_before,
            text_after=lambda count: "after",
            bounding_rectangles=lambda: [],
        ),
        element=SimpleNamespace(get_name=lambda: "editor"),
        source="TextPattern.GetSelection",
        exact_caret=True,
        selection_count=1,
    )
    monkeypatch.setattr(snapshots, "endpoint_offset", lambda info, endpoint: 7)

    snapshot = snapshots.make_snapshot(caret_info, context_chars=40)

    assert snapshot.caret_offset_units == 7
    assert snapshot.text_before is None
    assert snapshot.text_after == "after"
    assert any("reading text_before" in warning for warning in snapshot.warnings)
    assert not any("reading text_after" in warning for warning in snapshot.warnings)


@pytest.mark.parametrize("error_type", [AttributeError, TypeError])
def test_snapshot_does_not_hide_programming_errors_when_reading_text(
    monkeypatch,
    error_type,
):
    def fail_get_text(max_length=-1):
        raise error_type("broken text range wrapper")

    caret_info = SimpleNamespace(
        text_range=SimpleNamespace(
            get_text=fail_get_text,
            bounding_rectangles=lambda: [],
        ),
        element=SimpleNamespace(get_name=lambda: "editor"),
        source="TextPattern.GetSelection",
        exact_caret=False,
        selection_count=1,
    )
    monkeypatch.setattr(
        snapshots,
        "endpoint_offset",
        lambda info, endpoint: 4 if endpoint is TextRangeEndpoint.Start else 12,
    )

    with pytest.raises(error_type, match="broken text range wrapper"):
        snapshots.make_snapshot(
            caret_info,
            context_chars=40,
            include_context=False,
        )


def test_run_write_propagates_warning_from_before_snapshot(monkeypatch):
    warning = (
        "TextPattern.GetSelection returned 2 disjoint selections. "
        "TextCursor reports and uses only the first selection."
    )
    before = text_cursor.CursorSnapshot(
        provider="fake",
        type="range",
        selection_start_units=1,
        selection_end_units=2,
        warnings=[warning],
    )
    after = text_cursor.CursorSnapshot(
        provider="fake",
        type="caret",
        caret_offset_units=2,
    )
    providers = iter([object(), object()])

    monkeypatch.setattr(service, "find_caret_provider", lambda: next(providers))
    snapshots = iter([before, after])
    monkeypatch.setattr(service, "make_snapshot", lambda *args, **kwargs: next(snapshots))
    monkeypatch.setattr(
        service,
        "apply_write",
        lambda action, caret_info: WriteActionResult(None, {}),
    )

    result = service.run_write(
        text_cursor.MoveAbsoluteAction(mode="move_absolute", offset=2, verify=False)
    )

    assert result.warnings == [warning]


def test_run_write_distinguishes_target_from_read_back_actual(monkeypatch):
    before = text_cursor.CursorSnapshot(
        provider="fake",
        type="caret",
        caret_offset_units=1,
    )
    after = text_cursor.CursorSnapshot(
        provider="fake",
        type="caret",
        caret_offset_units=3,
    )
    providers = iter([object(), object()])
    snapshots = iter([before, after])

    monkeypatch.setattr(service, "find_caret_provider", lambda: next(providers))
    monkeypatch.setattr(
        service,
        "make_snapshot",
        lambda *args, **kwargs: next(snapshots),
    )
    monkeypatch.setattr(
        service,
        "apply_write",
        lambda action, caret_info: WriteActionResult(
            None,
            {"target_offset_units": 5},
        ),
    )

    result = service.run_write(
        text_cursor.MoveAbsoluteAction(
            mode="move_absolute",
            offset=5,
            verify=False,
        )
    )

    assert result.target == {"target_offset_units": 5}
    assert result.actual == {
        "type": "caret",
        "caret_offset_units": 3,
    }


def test_snapshot_position_reports_real_selection_coordinates():
    snapshot = text_cursor.CursorSnapshot(
        provider="fake",
        type="range",
        selection_start_units=4,
        selection_end_units=12,
    )

    assert snapshots.snapshot_position(snapshot) == {
        "type": "range",
        "selection_start_units": 4,
        "selection_end_units": 12,
    }
