"""Reserved Laya integration entry point."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from windows_mcp.agent.config import AgentConfig
from windows_mcp.agent.policy import Decision


class LayaPolicy:
    """Laya ONNX policy stub.

    The backend is intentionally not implemented here.  The prepared upstream
    interface is ``systemOne(state, questions)`` and returns the same
    typed-choice answer shape as TypeSafe.  A future implementation should
    bridge to ``@receptron/laya`` and a local ONNX runtime/Node process, load
    the roughly 1.7 GB ``systemOne`` weights, and pass the returned answers
    through ``decision_from_answers`` before any browser action is allowed.
    """

    name = "laya"

    def __init__(self, config: AgentConfig) -> None:
        self.onnx_dir = Path(config.laya_onnx_dir).expanduser() if config.laya_onnx_dir else None
        self.repo = config.laya_repo

    @property
    def available(self) -> bool:
        """Return whether a local model directory and Node.js are present."""

        return bool(
            self.onnx_dir
            and self.onnx_dir.is_dir()
            and shutil.which("node") is not None
        )

    def decide(
        self, state: dict[str, Any], goal: str, history: list[dict[str, Any]]
    ) -> Decision:
        """Reserved Laya decision entry point."""

        raise NotImplementedError(
            "LayaPolicy is a reserved stub. TODO: start or connect to a Node "
            "@receptron/laya runtime, load the ONNX weights from "
            "[agent].laya_onnx_dir (about 1.7 GB), call "
            "systemOne(state, questions), the same contract as TypeSafe, then "
            "validate the returned choice before action execution."
        )

    def text(self, context: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """Laya does not provide the text helper in the current design."""

        raise NotImplementedError(
            "LayaPolicy.text is not wired up. Future Laya integration may call "
            "the same OpenAI-compatible text helper used by JevPolicy."
        )


__all__ = ["LayaPolicy"]
