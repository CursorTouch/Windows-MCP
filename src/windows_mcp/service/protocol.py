"""Named pipe protocol between the host service and the user-mode broker.

Messages are JSON-encoded byte strings exchanged over a message-mode named
pipe.  One request → one response per connection.

Methods
-------
ping            → "pong"
desktop_name    → str ("Default" | "Winlogon")
screenshot      → base64-encoded PNG bytes (full virtual screen)
uia_windows     → list[str] of top-level window titles on input desktop
uia_tree        → list[dict] of top-level windows serialized as UIA nodes
uia_invoke      → bool — find named element and invoke it
uia_click_at    → bool — invoke element at (x, y); policy-gated when desktop=Winlogon
uia_type_at     → bool — SetValue on editable element at (x, y); policy-gated
uia_drag_from_to→ bool — move element from (x1, y1) to (x2, y2); policy-gated
get_uac_publisher → str | None — extract publisher string from active UAC dialog
wait_for_uac_prompt → dict | None — block until UAC fires (or timeout)
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

PIPE_NAME = r"\\.\pipe\windows-mcp-host"
PIPE_BUFFER_SIZE = 1 << 20  # 1 MiB — large enough for a compressed PNG
CALL_TIMEOUT_MS = 15_000    # 15 s — screenshot can be slow under load


@dataclass
class Request:
    method: str
    params: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def encode(self) -> bytes:
        return json.dumps(
            {"id": self.id, "method": self.method, "params": self.params}
        ).encode()

    @classmethod
    def decode(cls, data: bytes) -> "Request":
        obj = json.loads(data.decode())
        return cls(id=obj["id"], method=obj["method"], params=obj.get("params", {}))


@dataclass
class Response:
    id: str
    result: Any = None
    error: str | None = None

    def encode(self) -> bytes:
        obj: dict[str, Any] = {"id": self.id}
        if self.error is not None:
            obj["error"] = self.error
        else:
            obj["result"] = self.result
        return json.dumps(obj).encode()

    @classmethod
    def decode(cls, data: bytes) -> "Response":
        obj = json.loads(data.decode())
        return cls(id=obj["id"], result=obj.get("result"), error=obj.get("error"))
