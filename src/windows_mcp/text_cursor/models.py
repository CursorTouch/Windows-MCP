"""Input and output models for the TextCursor MCP tool."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .constants import MAX_TEXT_UNIT_MOVE

RelativeOrigin = Literal[
    "caret",
    "selection_start",
    "selection_end",
]

CollapseEdge = Literal["start", "end"]


class ActionBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delay: float = Field(
        default=0.0,
        ge=0.0,
        le=300.0,
        description=(
            "Seconds to wait before locating the focused UIA element and "
            "executing this action. Use this to leave time for the user or "
            "another automation step to focus the target text control."
        ),
    )

    context_chars: int = Field(
        default=40,
        ge=0,
        le=4096,
        description=("Number of UIA character units to read around the caret or selection."),
    )

    verify: bool = Field(
        default=True,
        description=(
            "Read the selection back after a write operation and verify that "
            "the provider actually applied it."
        ),
    )


class GetInfoAction(ActionBase):
    mode: Literal["get_info"]


class MoveRelativeAction(ActionBase):
    mode: Literal["move_relative"]

    delta: int = Field(
        ge=-MAX_TEXT_UNIT_MOVE,
        le=MAX_TEXT_UNIT_MOVE,
        description=(
            "Signed UIA TextUnit_Character movement. Positive moves forward; "
            "negative moves backward."
        ),
    )

    origin: RelativeOrigin = Field(
        default="caret",
        description=(
            "Movement origin. For a non-empty TextPattern fallback selection, "
            "use selection_start or selection_end."
        ),
    )


class MoveAbsoluteAction(ActionBase):
    mode: Literal["move_absolute"]

    offset: int = Field(
        ge=0,
        le=MAX_TEXT_UNIT_MOVE,
        description="Target position in UIA TextUnit_Character steps from DocumentRange start.",
    )


class SelectRelativeAction(ActionBase):
    mode: Literal["select_relative"]

    origin: RelativeOrigin = Field(
        default="caret",
        description=(
            "Origin the selection deltas are measured from. For a non-empty "
            "TextPattern fallback selection, use selection_start or selection_end."
        ),
    )

    start_delta: int = Field(
        ge=-MAX_TEXT_UNIT_MOVE,
        le=MAX_TEXT_UNIT_MOVE,
        description="Signed delta from origin to the inclusive selection start.",
    )
    end_delta: int = Field(
        ge=-MAX_TEXT_UNIT_MOVE,
        le=MAX_TEXT_UNIT_MOVE,
        description="Signed delta from origin to the exclusive selection end.",
    )

    @model_validator(mode="after")
    def validate_deltas(self) -> "SelectRelativeAction":
        if self.start_delta > self.end_delta:
            raise ValueError("start_delta must be less than or equal to end_delta")

        return self


class SelectAbsoluteAction(ActionBase):
    mode: Literal["select_absolute"]

    start: int = Field(
        ge=0,
        le=MAX_TEXT_UNIT_MOVE,
        description=(
            "Inclusive selection start in UIA TextUnit_Character steps from DocumentRange start."
        ),
    )

    end: int = Field(
        ge=0,
        le=MAX_TEXT_UNIT_MOVE,
        description=(
            "Exclusive selection end in UIA TextUnit_Character steps from DocumentRange start."
        ),
    )

    @model_validator(mode="after")
    def validate_offsets(self) -> "SelectAbsoluteAction":
        if self.start > self.end:
            raise ValueError("start must be less than or equal to end")

        return self


class SelectAllAction(ActionBase):
    mode: Literal["select_all"]


class CollapseSelectionAction(ActionBase):
    mode: Literal["collapse_selection"]
    edge: CollapseEdge


CursorAction = Annotated[
    Union[
        GetInfoAction,
        MoveRelativeAction,
        MoveAbsoluteAction,
        SelectRelativeAction,
        SelectAbsoluteAction,
        SelectAllAction,
        CollapseSelectionAction,
    ],
    Field(discriminator="mode"),
]


class ScreenRect(BaseModel):
    left: float
    top: float
    width: float
    height: float


def _ignore_none(value: object) -> bool:
    return value is None


class CursorSnapshot(BaseModel):
    provider: str
    element_name: str | None = None

    type: Literal["caret", "range"]

    caret_offset_units: int | None = Field(
        default=None,
        exclude_if=_ignore_none,
        description=(
            "Caret offset in provider-defined UIA TextUnit_Character steps "
            "from DocumentRange start."
        ),
    )
    selection_start_units: int | None = Field(
        default=None,
        exclude_if=_ignore_none,
        description=(
            "Selection start in provider-defined UIA TextUnit_Character steps "
            "from DocumentRange start."
        ),
    )
    selection_end_units: int | None = Field(
        default=None,
        exclude_if=_ignore_none,
        description=(
            "Selection end in provider-defined UIA TextUnit_Character steps "
            "from DocumentRange start."
        ),
    )

    selected_text: str | None = Field(default=None, exclude_if=_ignore_none)
    text_before: str | None = Field(default=None, exclude_if=_ignore_none)
    text_after: str | None = Field(default=None, exclude_if=_ignore_none)

    bounding_rects: list[ScreenRect] = Field(default_factory=list)

    warnings: list[str] = Field(default_factory=list)


class CursorToolResult(BaseModel):
    success: bool
    mode: str
    message: str

    verified: bool | None = Field(default=None, exclude_if=_ignore_none)

    requested: dict[str, Any] = Field(default_factory=dict)

    target: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Target calculated on the client-side UIA range after applying document-boundary "
            "clamping. This does not prove that the provider applied the target."
        ),
    )

    actual: dict[str, Any] = Field(
        default_factory=dict,
        description="Real caret or selection position read back from the provider after the write.",
    )

    before: CursorSnapshot | None = None
    after: CursorSnapshot | None = None

    warnings: list[str] = Field(default_factory=list)
