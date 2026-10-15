"""Request-local desktop control token and cross-thread input-step counter."""

from __future__ import annotations

import contextvars
import threading
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
# Copied contexts share this event: cancelling the await cannot stop a running
# worker, but closing its request must prevent its next desktop input step.
current_request_closed: contextvars.ContextVar[threading.Event | None] = contextvars.ContextVar(
    "windows_mcp_control_request_closed", default=None
)


def get_step_count() -> int:
    counter = current_steps.get()
    return 0 if counter is None else counter.value
