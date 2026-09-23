"""TypeSafe Jev policy — reachable through TypeSafe or through OpenRouter."""

from __future__ import annotations

import time
from typing import Any

from windows_mcp.agent.action_space import build_questions, decision_from_answers
from windows_mcp.agent.config import AgentConfig
from windows_mcp.agent.openrouter import OpenAITextHelper, _post_json
from windows_mcp.agent.policy import Decision


class JevPolicy:
    """Jev typed-choice policy.

    Jev is a *decisions* model: one forward pass returns a typed choice with
    calibrated probabilities instead of generating text. It is reachable two
    ways, and both use the same request/response shape:

    * TypeSafe's own endpoint (``/v1/systemone``) with a TypeSafe API key.
    * OpenRouter's decisions endpoint (``/api/alpha/decisions``) with an
      OpenRouter key. Jev is absent from OpenRouter's ``/models`` listing and
      rejects ``/chat/completions`` because it is not a chat model, so this
      endpoint is the only way to reach it there.

    A TypeSafe key wins when both are configured because it removes one hop.
    """

    name = "jev"

    def __init__(self, config: AgentConfig) -> None:
        self._text_helper = OpenAITextHelper(config)

        if config.typesafe_api_key:
            self._endpoint = config.typesafe_base_url
            self._api_key = config.typesafe_api_key
            self.model = config.typesafe_model
            self.backend = "typesafe"
        elif config.openrouter_api_key:
            self._endpoint = config.openrouter_decisions_url
            self._api_key = config.openrouter_api_key
            self.model = config.openrouter_decisions_model
            self.backend = "openrouter-decisions"
        else:
            self._endpoint = None
            self._api_key = None
            self.model = config.typesafe_model
            self.backend = None

    @property
    def available(self) -> bool:
        """Return whether either decisions endpoint is configured."""

        return bool(self._api_key and self._endpoint)

    def decide(
        self, state: dict[str, Any], goal: str, history: list[dict[str, Any]]
    ) -> Decision:
        """Call Jev and return one validated decision."""

        if not self.available:
            raise RuntimeError(
                "JevPolicy is unavailable. Set WINDOWS_MCP_OPENROUTER_API_KEY "
                "(uses OpenRouter /api/alpha/decisions) or "
                "WINDOWS_MCP_TYPESAFE_API_KEY (uses api.typesafe.ai/v1/systemone)."
            )
        questions, elements, targets, controls = build_questions(state, goal)
        body = {
            "model": self.model,
            "state": {
                "page": {
                    "url": state.get("url", ""),
                    "title": state.get("title", ""),
                    "text": state.get("text", ""),
                },
                "elements": elements,
                "recent_actions": [
                    {
                        key: item.get(key)
                        for key in (
                            "action",
                            "kind",
                            "operation",
                            "target",
                            "text",
                            "page_changed",
                        )
                    }
                    for item in history[-10:]
                ],
            },
            "questions": questions,
        }
        started = time.perf_counter()
        response = _post_json(self._endpoint or "", self._api_key or "", body)
        answers = response.get("answers")
        if not isinstance(answers, dict):
            raise ValueError("Jev response omitted answers; no action executed.")
        decision_data = decision_from_answers(answers, targets, controls)
        return Decision(
            **decision_data,
            model=response.get("model", self.model),
            usage=response.get("usage", {}),
            latency_ms=round((time.perf_counter() - started) * 1000),
            request=body,
        )

    def text(self, context: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """Generate a field value with the configured OpenAI-compatible helper."""

        return self._text_helper.generate(context)


__all__ = ["JevPolicy"]
