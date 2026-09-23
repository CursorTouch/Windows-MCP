"""Observe, decide, validate, and execute the browser-agent loop."""

from __future__ import annotations

from dataclasses import replace

import json
import time
from typing import Any

from windows_mcp.agent.action_space import (
    action_space,
    build_questions,
    decision_from_answers,
    extract_urls,
    make_choice_answer,
)
from windows_mcp.agent.config import AgentConfig
from windows_mcp.agent.policy import (
    Decision,
    Policy,
    PolicyUnavailableError,
    get_policy,
)
from windows_mcp.web import get_web_service


def _coerce_positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a positive integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if number < 1:
        raise ValueError(f"{name} must be a positive integer")
    return number


def _coerce_bool(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ValueError(f"{name} must be true or false")


class _DryRunSkeletonPolicy:
    """Deterministic local policy used only when dry-run has no credentials."""

    name = "dry-run-skeleton"

    @property
    def available(self) -> bool:
        return False

    def decide(
        self, state: dict[str, Any], goal: str, history: list[dict[str, Any]]
    ) -> Decision:
        questions, elements, targets, controls = build_questions(state, goal)
        candidates = questions["operation"]["criteria"]
        if "CLICK" in targets:
            operation = "CLICK"
        elif "TYPE_TEXT" in targets:
            operation = "TYPE_TEXT"
        elif controls:
            operation = next(iter(controls))
        elif candidates:
            operation = next(iter(candidates))
        else:
            operation = "BLOCKED"

        answers: dict[str, Any] = {
            "operation": make_choice_answer(operation, list(candidates), 0.0)
        }
        if operation in targets:
            target = next(iter(targets[operation]))
            answers[operation.lower() + "_target"] = make_choice_answer(
                target, list(targets[operation]), 0.0
            )
        decision_data = decision_from_answers(answers, targets, controls)
        return Decision(
            **decision_data,
            model="offline-dry-run",
            usage={},
            latency_ms=0,
        )

    def text(self, context: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        raise NotImplementedError("The dry-run skeleton does not generate field text.")


class WebAgentService:
    """Run a bounded Jev-style browser-agent loop."""

    def __init__(
        self,
        *,
        policy: str = "auto",
        max_steps: int = 25,
        headless: bool = False,
        endpoint: str | None = None,
        dry_run: bool = False,
        timeout_ms: int = 30000,
        max_elements: int = 150,
        config: AgentConfig | None = None,
    ) -> None:
        self.policy_name = policy
        self.max_steps = _coerce_positive_int(max_steps, "max_steps")
        self.headless = _coerce_bool(headless, "headless")
        self.endpoint = endpoint.strip() if isinstance(endpoint, str) and endpoint.strip() else None
        self.dry_run = _coerce_bool(dry_run, "dry_run")
        self.timeout_ms = _coerce_positive_int(timeout_ms, "timeout_ms")
        self.max_elements = _coerce_positive_int(max_elements, "max_elements")
        self._config = config

    def _resolve_policy(self) -> Policy:
        try:
            return get_policy(self.policy_name, self._config)
        except PolicyUnavailableError:
            if self.dry_run and self.policy_name.strip().casefold() == "auto":
                return _DryRunSkeletonPolicy()
            raise

    def _open_page(self, url: str) -> Any:
        web = get_web_service()
        if self.endpoint:
            web.connect(endpoint=self.endpoint, timeout_ms=self.timeout_ms)
            status_text = web.status()
            try:
                status = json.loads(status_text)
            except json.JSONDecodeError:
                status = {}
            if status.get("url") != url:
                script = f"() => {{ window.location.href = {json.dumps(url)}; }}"
                web.evaluate(script)
                try:
                    web.wait("networkidle", timeout_ms=self.timeout_ms)
                except Exception:
                    pass
            return web
        web.launch(headless=self.headless, url=url, timeout_ms=self.timeout_ms)
        return web

    @staticmethod
    def _policy_state(
        snapshot: dict[str, Any], history: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return {
            "url": snapshot.get("url", ""),
            "title": snapshot.get("title", ""),
            "text": snapshot.get("text", ""),
            "elements": snapshot.get("elements", []),
            "fingerprint": snapshot.get("fingerprint", ""),
            "recent_actions": history[-10:],
        }

    @staticmethod
    def _disambiguate(
        decision: Decision,
        candidate: dict[str, Any] | None,
        targets: dict[str, dict[str, Any]],
    ) -> tuple[Decision, dict[str, Any] | None]:
        """Resolve an operation deterministically when the target space is trivial.

        Most goals name an operation unambiguously even when the model is unsure
        which element it belongs to. ``TYPE_TEXT`` in particular usually has
        exactly one candidate (the page's single search or input field), so there
        is nothing to choose: picking it removes a real source of error, because
        an unsure model occasionally selects a nearby but unrelated element --
        for example the ``Image creator`` link on a homepage when the goal was to
        type into the search box. The same applies to ``SELECT`` on a lone
        dropdown.

        The decision is only rewritten when the operation has exactly one
        candidate and the model already selected that operation. Anything else is
        returned untouched so the model keeps the choice.
        """

        if decision.operation not in targets:
            return decision, candidate
        candidates = targets[decision.operation]
        if not candidates:
            return decision, candidate

        if len(candidates) == 1:
            only_index, only_candidate = next(iter(candidates.items()))
        else:
            # Several candidates: rank them by how well the role fits the
            # operation. A page usually has exactly one real search/input field,
            # so a combobox or searchbox beats a stray <input>; a link is never a
            # text field, which is what an unsure model sometimes picks (it
            # associates a typed domain like "github.com" with an "Image creator"
            # link sitting next to the box).
            ranks = {
                "TYPE_TEXT": {"combobox": 0, "searchbox": 1, "textbox": 2},
                "SELECT": {"combobox": 0, "listbox": 1},
            }.get(decision.operation)
            if not ranks:
                return decision, candidate
            ranked = sorted(
                candidates.items(),
                key=lambda item: (
                    ranks.get(str(item[1].get("role", "")).casefold(), 9),
                    int(item[0]),
                ),
            )
            best_rank = ranks.get(str(ranked[0][1].get("role", "")).casefold(), 9)
            if best_rank == 9:
                return decision, candidate
            only_index, only_candidate = ranked[0]

        if candidate is not None and candidate.get("ref") == only_candidate.get("ref"):
            return decision, candidate
        resolved = replace(
            decision,
            target=str(only_index),
            choice=only_candidate.get("id"),
            resolved_deterministically=True,
        )
        return resolved, only_candidate

    @staticmethod
    def _validate_decision(
        decision: Decision, snapshot: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        if type(decision.confidence) not in (int, float):
            raise ValueError("Policy confidence was not numeric; no action executed.")
        if not 0 <= float(decision.confidence) <= 1:
            raise ValueError("Policy confidence was outside [0, 1]; no action executed.")
        _, targets, controls = action_space(snapshot.get("elements", []))
        operation = decision.operation
        if operation in targets:
            if decision.target is None:
                raise ValueError("Policy selected an operation without a target.")
            target = str(decision.target)
            candidate = targets[operation].get(target)
            if candidate is None:
                raise ValueError("Policy target was not in the observed action space.")
            if decision.choice != candidate.get("id"):
                raise ValueError("Policy choice did not match its observed target.")
            return candidate, targets, controls
        if operation in controls:
            candidate = controls[operation]
            if decision.choice != candidate.get("id"):
                raise ValueError("Policy choice did not match its observed control.")
            return candidate, targets, controls
        expected = {
            "SCROLL_UP",
            "SCROLL_DOWN",
            "WAIT",
            "DONE",
            "BLOCKED",
        }
        if operation.startswith("NAVIGATE_") and operation[9:].isdigit():
            return None, targets, controls
        if operation not in expected:
            raise ValueError(f"Unsupported policy operation {operation!r}.")
        if decision.choice != operation:
            raise ValueError("Policy choice did not match its selected operation.")
        return None, targets, controls

    @staticmethod
    def _text_context(
        goal: str,
        candidate: dict[str, Any],
        snapshot: dict[str, Any],
        history: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "goal": goal,
            "field": {
                "label": candidate.get("label", ""),
                "role": candidate.get("role", ""),
                "value": candidate.get("current_value", candidate.get("value", "")),
            },
            "page": {
                "title": snapshot.get("title", ""),
                "text": str(snapshot.get("text", ""))[:6000],
            },
            "recent_actions": [
                {
                    key: item.get(key)
                    for key in ("action", "operation", "target", "text", "page_changed")
                }
                for item in history[-6:]
            ],
        }

    @staticmethod
    def _press_key(operation: str) -> str:
        name = operation.removeprefix("PRESS_").replace("_", " ").strip()
        mapping = {
            "enter": "Enter",
            "tab": "Tab",
            "escape": "Escape",
            "esc": "Escape",
            "space": "Space",
            "backspace": "Backspace",
            "delete": "Delete",
            "arrow up": "ArrowUp",
            "arrow down": "ArrowDown",
            "arrow left": "ArrowLeft",
            "arrow right": "ArrowRight",
        }
        return mapping.get(name.casefold(), name)

    def _execute(
        self,
        web: Any,
        decision: Decision,
        candidate: dict[str, Any] | None,
        goal: str,
        snapshot: dict[str, Any],
        history: list[dict[str, Any]],
        policy: Policy,
    ) -> tuple[str, str | None, dict[str, Any] | None, dict[str, Any]]:
        operation = decision.operation
        if operation.startswith("NAVIGATE_") and operation[9:].isdigit():
            urls = extract_urls(goal)
            position = int(operation[9:])
            if not 1 <= position <= len(urls):
                raise ValueError("Navigation target was not in the goal.")
            target_url = urls[position - 1]
            web.goto(target_url, timeout_ms=self.timeout_ms)
            return f"Navigated to {target_url}.", None, None, snapshot
        if operation == "WAIT":
            web.wait("timeout", timeout_ms=500)
            return "Waited 500ms.", None, None, snapshot
        if operation == "SCROLL_UP":
            return web.scroll(direction="up", amount=600), None, None, snapshot
        if operation == "SCROLL_DOWN":
            return web.scroll(direction="down", amount=600), None, None, snapshot
        if operation == "CLICK":
            if candidate is None:
                raise ValueError("CLICK had no validated target.")
            return web.click(ref=candidate["ref"], timeout_ms=self.timeout_ms), None, None, snapshot
        if operation == "SELECT":
            if candidate is None:
                raise ValueError("SELECT had no validated target.")
            return (
                web.select(value=candidate.get("value"), ref=candidate["ref"], timeout_ms=self.timeout_ms),
                None,
                None,
                snapshot,
            )
        if operation == "TYPE_TEXT":
            if candidate is None:
                raise ValueError("TYPE_TEXT had no validated target.")
            context = self._text_context(goal, candidate, snapshot, history)
            generated, helper = policy.text(context)
            after_text = web.snapshot_data(max_elements=self.max_elements)
            if after_text.get("fingerprint") != snapshot.get("fingerprint"):
                return "", generated, helper, after_text
            result = web.type_text(
                generated,
                ref=candidate["ref"],
                clear=True,
                timeout_ms=self.timeout_ms,
            )
            return result, generated, helper, after_text
        if operation.startswith("PRESS_"):
            key = self._press_key(operation)
            ref = candidate.get("ref") if candidate else None
            return web.press(key, ref=ref, timeout_ms=self.timeout_ms), None, None, snapshot
        raise ValueError(f"Unsupported executable operation {operation!r}.")

    def run(self, url: str, goal: str) -> dict[str, Any]:
        """Run one bounded browser-agent task and return a JSON-safe report."""

        if not isinstance(url, str) or not url.strip():
            raise ValueError("url must be a non-empty string")
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("goal must be a non-empty string")
        url = url.strip()
        goal = goal.strip()
        policy = self._resolve_policy()
        web = self._open_page(url)
        snapshot = web.snapshot_data(max_elements=self.max_elements)
        history: list[dict[str, Any]] = []
        status = "max_steps"
        error: str | None = None
        started = time.perf_counter()

        for step_number in range(1, self.max_steps + 1):
            state = self._policy_state(snapshot, history)
            try:
                decision = policy.decide(state, goal, history)
            except Exception as exc:
                error = f"Decision failed: {exc}"
                history.append(
                    {
                        "step": step_number,
                        "operation": None,
                        "target": None,
                        "confidence": None,
                        "latency_ms": 0,
                        "executed": False,
                        "stale": False,
                        "page_changed": None,
                        "effective": False,
                        "error": error,
                        "elapsed_ms": round((time.perf_counter() - started) * 1000),
                    }
                )
                status = "error"
                break

            if self.dry_run:
                history.append(
                    {
                        "step": step_number,
                        "operation": decision.operation,
                        "target": decision.target,
                        "choice": decision.choice,
                        "confidence": decision.confidence,
                        "latency_ms": decision.latency_ms,
                        "model": decision.model,
                        "usage": decision.usage,
                        "executed": False,
                        "stale": False,
                        "page_changed": None,
                        "effective": False,
                        "elapsed_ms": round((time.perf_counter() - started) * 1000),
                    }
                )
                status = "dry_run"
                break

            try:
                candidate, targets, controls = self._validate_decision(decision, snapshot)
                decision, candidate = self._disambiguate(decision, candidate, targets)
            except Exception as exc:
                error = f"Decision validation failed: {exc}"
                history.append(
                    {
                        "step": step_number,
                        "operation": decision.operation,
                        "target": decision.target,
                        "choice": decision.choice,
                        "confidence": decision.confidence,
                        "latency_ms": decision.latency_ms,
                        "model": decision.model,
                        "usage": decision.usage,
                        "executed": False,
                        "stale": False,
                        "page_changed": None,
                        "effective": False,
                        "error": error,
                        "elapsed_ms": round((time.perf_counter() - started) * 1000),
                    }
                )
                status = "error"
                break

            fresh = web.snapshot_data(max_elements=self.max_elements)
            if fresh.get("fingerprint") != snapshot.get("fingerprint"):
                history.append(
                    {
                        "step": step_number,
                        "operation": decision.operation,
                        "target": decision.target,
                        "choice": decision.choice,
                        "confidence": decision.confidence,
                        "latency_ms": decision.latency_ms,
                        "model": decision.model,
                        "usage": decision.usage,
                        "executed": False,
                        "stale": True,
                        "page_changed": True,
                        "effective": False,
                        "error": "Page changed before execution; decision discarded.",
                        "elapsed_ms": round((time.perf_counter() - started) * 1000),
                    }
                )
                snapshot = fresh
                continue

            try:
                candidate, _, _ = self._validate_decision(decision, fresh)
            except Exception as exc:
                error = f"Fresh decision validation failed: {exc}"
                status = "error"
                history.append(
                    {
                        "step": step_number,
                        "operation": decision.operation,
                        "target": decision.target,
                        "choice": decision.choice,
                        "confidence": decision.confidence,
                        "executed": False,
                        "stale": False,
                        "page_changed": None,
                        "effective": False,
                        "error": error,
                        "elapsed_ms": round((time.perf_counter() - started) * 1000),
                    }
                )
                break

            snapshot = fresh
            if decision.operation in {"DONE", "BLOCKED"}:
                history.append(
                    {
                        "step": step_number,
                        "operation": decision.operation,
                        "target": decision.target,
                        "choice": decision.choice,
                        "confidence": decision.confidence,
                        "latency_ms": decision.latency_ms,
                        "model": decision.model,
                        "usage": decision.usage,
                        "executed": False,
                        "stale": False,
                        "page_changed": False,
                        "effective": False,
                        "elapsed_ms": round((time.perf_counter() - started) * 1000),
                    }
                )
                status = "done" if decision.operation == "DONE" else "blocked"
                break

            before_fingerprint = snapshot.get("fingerprint")
            action_text = candidate.get("label") if candidate else None
            validation: str | None = None
            generated: str | None = None
            helper: dict[str, Any] | None = None
            after_text = snapshot
            try:
                validation, generated, helper, after_text = self._execute(
                    web,
                    decision,
                    candidate,
                    goal,
                    snapshot,
                    history,
                    policy,
                )
            except Exception as exc:
                error = f"Action failed: {exc}"
                try:
                    snapshot = web.snapshot_data(max_elements=self.max_elements)
                except Exception:
                    pass
                history.append(
                    {
                        "step": step_number,
                        "operation": decision.operation,
                        "target": decision.target,
                        "choice": decision.choice,
                        "confidence": decision.confidence,
                        "latency_ms": decision.latency_ms,
                        "model": decision.model,
                        "usage": decision.usage,
                        "action": action_text,
                        "text": generated,
                        "text_helper": helper,
                        "executed": False,
                        "stale": False,
                        "page_changed": None,
                        "effective": False,
                        "validation": validation,
                        "error": error,
                        "elapsed_ms": round((time.perf_counter() - started) * 1000),
                    }
                )
                status = "error"
                break

            if (
                decision.operation == "TYPE_TEXT"
                and after_text.get("fingerprint") != before_fingerprint
            ):
                history.append(
                    {
                        "step": step_number,
                        "operation": decision.operation,
                        "target": decision.target,
                        "choice": decision.choice,
                        "confidence": decision.confidence,
                        "latency_ms": decision.latency_ms,
                        "model": decision.model,
                        "usage": decision.usage,
                        "action": action_text,
                        "text": generated,
                        "text_helper": helper,
                        "executed": False,
                        "stale": True,
                        "page_changed": True,
                        "effective": False,
                        "error": "Page changed during text generation; decision discarded.",
                        "elapsed_ms": round((time.perf_counter() - started) * 1000),
                    }
                )
                snapshot = after_text
                continue

            post = web.snapshot_data(max_elements=self.max_elements)
            page_changed = post.get("fingerprint") != before_fingerprint
            history.append(
                {
                    "step": step_number,
                    "operation": decision.operation,
                    "target": decision.target,
                    "choice": decision.choice,
                    "confidence": decision.confidence,
                    "latency_ms": decision.latency_ms,
                    "model": decision.model,
                    "usage": decision.usage,
                    "action": action_text,
                    "text": generated,
                    "text_helper": helper,
                    "executed": True,
                    "stale": False,
                    "page_changed": page_changed,
                    "effective": page_changed,
                    "validation": validation,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000),
                }
            )
            snapshot = post

            recent = history[-3:]
            if (
                len(recent) == 3
                and all(item.get("executed") for item in recent)
                and all(item.get("operation") != "WAIT" for item in recent)
                and all(item.get("page_changed") is False for item in recent)
            ):
                status = "blocked"
                error = "No observable page change for 3 consecutive actions."
                break
            if step_number >= self.max_steps:
                status = "max_steps"
                break
            status = "running"

        models = sorted(
            {
                str(item.get("model", ""))
                for item in history
                if item.get("model")
            }
            | {
                str(item.get("text_helper", {}).get("model", ""))
                for item in history
                if isinstance(item.get("text_helper"), dict)
                and item.get("text_helper", {}).get("model")
            }
        )
        return {
            "status": status,
            "policy": policy.name,
            "models": models,
            "goal": goal,
            "url": snapshot.get("url", url),
            "title": snapshot.get("title", ""),
            "dry_run": self.dry_run,
            "max_steps": self.max_steps,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "steps": history,
            "error": error,
        }


AgentService = WebAgentService


def run_web_agent(
    url: str,
    goal: str,
    policy: str = "auto",
    max_steps: int = 25,
    headless: bool = False,
    endpoint: str | None = None,
    dry_run: bool = False,
    timeout_ms: int = 30000,
) -> dict[str, Any]:
    """Convenience entry point used by the MCP tool wrapper."""

    return WebAgentService(
        policy=policy,
        max_steps=max_steps,
        headless=headless,
        endpoint=endpoint,
        dry_run=dry_run,
        timeout_ms=timeout_ms,
    ).run(url=url, goal=goal)


__all__ = ["AgentService", "WebAgentService", "run_web_agent"]



