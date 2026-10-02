"""Request-local desktop control token and cross-thread input-step counter."""

from __future__ import annotations

import contextvars
from dataclasses import dataclass


@dataclass
class StepCounter:
    # AnyIO copies context variables into worker threads. The value object is
    # shared, so a synchronous tool's executed steps remain visible to middleware.
    value: int = 0


current_token: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "windows_mcp_control_token", default=None
)
current_steps: contextvars.ContextVar[StepCounter | None] = contextvars.ContextVar(
    "windows_mcp_control_steps", default=None
)


def get_step_count() -> int:
    counter = current_steps.get()
    return 0 if counter is None else counter.value
